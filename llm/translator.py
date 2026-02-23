"""
llm/translator.py — Bulk Translation Runner
=============================================
Translates all entities (and the greeting) that are missing a translation
for any configured target language. Designed to run after the seeder, or
on a schedule, or triggered via POST /admin/translate.

Usage:
  python -m llm.translator                   # translate all missing
  python -m llm.translator --lang de         # only German
  python -m llm.translator --dry-run         # preview without writing
  python -m llm.translator --force           # re-translate everything
  python -m llm.translator --entity-id UUID  # one entity only

Rate limiting:
  GROQ free tier: ~30 req/min, ~14,400 req/day
  We batch with a small sleep between calls to stay well under limits.
  With ~200 entities × 2 fields × 2 langs = ~800 calls — fine for free tier.
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

import yaml

log = logging.getLogger("mcp.translator")

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import (
    get_db, init_db, DB_PATH,
    upsert_translation, upsert_greeting_translation,
    get_greeting_translation, list_entities_needing_translation,
    needs_translation, SUPPORTED_LANGS, DEFAULT_LANG,
    list_entities,
)
from llm.enricher import LLMEnricher


# ─────────────────────────────────────────────────────────────────────────────

def load_cfg(path: str = None) -> dict:
    from config_loader import load_config as _load
    if path and path != "config.yaml":
        with open(path) as f:
            return yaml.safe_load(f)
    return _load()


def model_label(enricher: LLMEnricher) -> str:
    return f"{enricher.backend}/{enricher.model}"


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY TRANSLATION
# ─────────────────────────────────────────────────────────────────────────────

def translate_entities(conn: sqlite3.Connection, enricher: LLMEnricher,
                       target_lang: str, force: bool = False,
                       dry_run: bool = False,
                       entity_id: str = None,
                       batch_sleep: float = 0.5) -> dict:
    """
    Translate all entities missing `target_lang`.
    Returns a stats dict.
    """
    stats = {"translated": 0, "skipped": 0, "failed": 0, "lang": target_lang}

    if entity_id:
        rows = [dict(conn.execute(
            "SELECT * FROM entities WHERE id=?", (entity_id,)
        ).fetchone() or {})]
        rows = [r for r in rows if r]
    elif force:
        rows = list_entities(conn, limit=2000)
    else:
        rows = list_entities_needing_translation(conn, target_lang, limit=2000)

    log.info(f"Translating {len(rows)} entities → {target_lang} "
             f"(force={force}, dry_run={dry_run})")

    for entity in rows:
        eid   = entity.get("id")
        etype = entity.get("type")

        # Skip types where title/description don't need translation
        if etype in ("technology", "person"):
            stats["skipped"] += 1
            continue

        # Skip if already translated and not forcing
        if not force and not needs_translation(conn, eid, target_lang):
            stats["skipped"] += 1
            continue

        title_orig = entity.get("title", "")
        desc_orig  = entity.get("description", "") or ""
        source_lang = entity.get("language") or DEFAULT_LANG

        # Skip if source is already target lang
        if source_lang == target_lang:
            stats["skipped"] += 1
            continue

        log.debug(f"  [{etype}] {title_orig[:50]}")

        try:
            t_title = enricher.translate(
                title_orig, target_lang=target_lang,
                source_lang=source_lang, context="title"
            )
            t_desc = enricher.translate(
                desc_orig, target_lang=target_lang,
                source_lang=source_lang, context="description"
            ) if desc_orig.strip() else None

            if dry_run:
                log.info(f"  DRY [{target_lang}] {title_orig!r} → {t_title!r}")
                stats["translated"] += 1
                continue

            if t_title or t_desc:
                upsert_translation(
                    conn, eid, target_lang,
                    title=t_title,
                    description=t_desc,
                    model=model_label(enricher),
                )
                stats["translated"] += 1
            else:
                stats["failed"] += 1

        except Exception as e:
            log.warning(f"  Translation failed for {eid}: {e}")
            stats["failed"] += 1

        # Rate-limit courtesy pause
        time.sleep(batch_sleep)

    if not dry_run:
        conn.commit()

    log.info(f"Done: {stats}")
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# GREETING TRANSLATION
# ─────────────────────────────────────────────────────────────────────────────

def translate_greeting(conn: sqlite3.Connection, enricher: LLMEnricher,
                       cfg: dict, target_lang: str,
                       force: bool = False, dry_run: bool = False) -> bool:
    """
    Translate the static greeting/identity text.
    Source text comes from config.yaml (identity section).
    """
    if not force and get_greeting_translation(conn, target_lang):
        log.info(f"Greeting already translated to {target_lang}, skipping")
        return False

    identity = cfg.get("identity", {})
    source = {
        "tagline": identity.get("tagline", ""),
        "short":   identity.get("tagline", ""),   # used as short bio
        "greeting": identity.get("greeting",
                     f"Hi, I'm {identity.get('name', '')}! "
                     f"{identity.get('tagline', '')}"),
    }

    log.info(f"Translating greeting → {target_lang}")
    translated = enricher.translate_greeting(source, target_lang)

    if dry_run:
        log.info(f"  DRY greeting [{target_lang}]: {translated.get('tagline')!r}")
        return True

    upsert_greeting_translation(
        conn, target_lang,
        tagline=translated.get("tagline"),
        short=translated.get("short"),
        greeting=translated.get("greeting"),
        model=model_label(enricher),
    )
    conn.commit()
    log.info(f"Greeting translated to {target_lang}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace, cfg: dict):
    enricher = LLMEnricher(cfg.get("llm", {}))

    if enricher.backend == "none":
        log.error("LLM backend is 'none' — translation requires GROQ or Ollama. "
                  "Set llm.backend in config.yaml or export GROQ_API_KEY.")
        sys.exit(1)

    # Determine which languages to process
    configured_langs: list[str] = cfg.get("i18n", {}).get("target_languages", ["de"])
    if args.lang:
        langs = [args.lang]
    else:
        langs = [l for l in configured_langs if l != DEFAULT_LANG]

    conn = get_db(DB_PATH)
    init_db(DB_PATH)

    total_stats = []

    for lang in langs:
        if lang not in SUPPORTED_LANGS and not args.allow_extra_langs:
            log.warning(f"Lang '{lang}' not in SUPPORTED_LANGS {SUPPORTED_LANGS}. "
                        f"Use --allow-extra-langs to force.")
            continue

        log.info(f"═══ Processing language: {lang} ═══")

        # Translate greeting first (fast, single call)
        translate_greeting(conn, enricher, cfg, lang,
                           force=args.force, dry_run=args.dry_run)

        # Translate all entities
        stats = translate_entities(
            conn, enricher, lang,
            force=args.force,
            dry_run=args.dry_run,
            entity_id=args.entity_id,
            batch_sleep=cfg.get("i18n", {}).get("batch_sleep_seconds", 0.4),
        )
        total_stats.append(stats)

    conn.close()

    log.info("═══ Translation complete ═══")
    for s in total_stats:
        log.info(f"  [{s['lang']}] translated={s['translated']} "
                 f"skipped={s['skipped']} failed={s['failed']}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    )

    parser = argparse.ArgumentParser(description="Personal MCP — Bulk Translator")
    parser.add_argument("--config",           default=None,
                        help="Path to a single config YAML (default: auto-merge config.tech.yaml + config.content.yaml)")
    parser.add_argument("--lang",             default=None,
                        help="Target language code, e.g. de (default: all configured)")
    parser.add_argument("--entity-id",        default=None,
                        help="Translate a single entity by ID")
    parser.add_argument("--force",            action="store_true",
                        help="Re-translate even if translation already exists")
    parser.add_argument("--dry-run",          action="store_true",
                        help="Show what would be translated, don't write to DB")
    parser.add_argument("--allow-extra-langs", action="store_true",
                        help="Allow languages not in SUPPORTED_LANGS")
    args = parser.parse_args()

    cfg = load_cfg(args.config)
    run(args, cfg)
