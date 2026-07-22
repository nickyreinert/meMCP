"""
config_loader.py — Unified configuration loader (DB-first)
==========================================================
ALL configuration lives in the SQLite database.
There are NO YAML config files.

Bootstrap: The DB path is resolved from the MEMCP_DB_PATH env var
(default: db/profile.db). Everything else comes from DB.

If the DB is empty or doesn't exist, hardcoded bootstrap defaults
ensure the app can start — the admin UI / setup wizard then fills
in the real values.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


# ── Bootstrap defaults ───────────────────────────────────────────────────────
# Minimal safe config so the app can start with an empty DB.

def _get_bootstrap_defaults() -> dict:
    return {
        "server": {
            "host": "0.0.0.0",
            "port": 8000,
            "base_url": "http://localhost:8000",
        },
        "security": {
            "cors_origins": ["*"],
            "trusted_proxies": ["127.0.0.1", "::1"],
        },
        "session": {
            "enabled": True,
            "timeout_hours": 5,
            "log_file": "logs/api_access.log",
            "track_coverage": True,
            "db_path": "db/sessions.db",
            "relevant_endpoints": {},
        },
        "llm": {
            "backend": "none",
            "model": "",
            "groq_api_key": "",
            "ollama_url": "http://localhost:11434",
            "shrink_text": False,
            "shrink_skip_chars": 3,
        },
        "cache_dir": ".cache",
        "cache_ttl_hours": 6,
        "watch_interval_minutes": 120,
        "protected_endpoints": {},
        "db_path": "db/profile.db",
    }


def _resolve_db_path(root: Path) -> Path:
    """Resolve DB path from env var or default, relative to project root."""
    db_path_str = os.environ.get("MEMCP_DB_PATH", "db/profile.db")
    db_path = Path(db_path_str)
    if not db_path.is_absolute():
        db_path = root / db_path
    return db_path


# ── DB readers ───────────────────────────────────────────────────────────────

def _load_tech_from_db(conn) -> dict:
    """Read tech config namespaces from DB, return dict with same shape consumers expect."""
    from db.config_store import get_config_section

    merged: dict = {}

    # server → config["server"]
    section = get_config_section(conn, "server")
    if section:
        merged["server"] = section

    # security → config["security"]
    section = get_config_section(conn, "security")
    if section:
        merged["security"] = section

    # session → config["session"]
    section = get_config_section(conn, "session")
    if section:
        merged["session"] = section

    # llm → config["llm"]
    section = get_config_section(conn, "llm")
    if section:
        merged["llm"] = section

    # cache → flatten to top-level keys (backward compat)
    section = get_config_section(conn, "cache")
    if section:
        merged["cache_dir"] = section.get("cache_dir", ".cache")
        merged["cache_ttl_hours"] = section.get("cache_ttl_hours", 6)
        merged["watch_interval_minutes"] = section.get("watch_interval_minutes", 120)

    # endpoints → config["protected_endpoints"]
    section = get_config_section(conn, "endpoints")
    if section:
        merged["protected_endpoints"] = section

    return merged


def _load_content_from_db(conn) -> dict:
    """Read content/personal config from DB (sources, chat, metrics, i18n, identity)."""
    from db.config_store import get_config_section, get_all_sources, config_section_exists

    merged: dict = {}

    # Sources: reconstruct oeuvre/stages/identity dict
    if config_section_exists(conn, "sources"):
        sources = get_all_sources(conn)
        if sources.get("oeuvre"):
            merged["oeuvre"] = sources["oeuvre"]
        if sources.get("stages"):
            merged["stages"] = sources["stages"]
        if sources.get("identity"):
            merged["identity"] = sources["identity"]

    # Identity multilang data
    if config_section_exists(conn, "identity"):
        identity_data = get_config_section(conn, "identity")
        if identity_data.get("data"):
            merged["identity"] = identity_data["data"]

    # Chat config
    if config_section_exists(conn, "chat"):
        merged["chat"] = get_config_section(conn, "chat")

    # Metrics config
    if config_section_exists(conn, "metrics"):
        merged["metrics"] = get_config_section(conn, "metrics")

    # i18n config
    if config_section_exists(conn, "i18n"):
        merged["i18n"] = get_config_section(conn, "i18n")

    return merged


# ── Public API ───────────────────────────────────────────────────────────────

def load_config(root: Optional[Union[Path, str]] = None,
                db_path: Optional[Union[Path, str]] = None) -> dict:
    """
    DB-first loader. All config comes from SQLite.

    Resolution:
      1. Start with bootstrap defaults (so app can start with empty DB)
      2. Overlay tech config from DB (server, security, llm, session, cache, endpoints)
      3. Overlay content config from DB (sources, chat, metrics, i18n, identity)

    Args:
        root: Project root directory.
        db_path: Path to SQLite DB. If None, derived from MEMCP_DB_PATH env var.

    Returns:
        Merged configuration dict.
    """
    if root is None:
        root = Path(__file__).parent
    root = Path(root)

    # Step 1: Bootstrap defaults
    merged = _get_bootstrap_defaults()

    # Resolve DB path
    if db_path is not None:
        db_path = Path(db_path)
        if not db_path.is_absolute():
            db_path = root / db_path
    else:
        db_path = _resolve_db_path(root)

    merged["db_path"] = str(db_path)

    if not db_path.exists():
        logger.debug("DB not found at %s — using bootstrap defaults", db_path)
        return merged

    # Step 2+3: Read from DB
    try:
        from db.config_store import init_encryption
        from db.models import get_db

        secret = os.environ.get("CONFIG_SECRET", "")
        init_encryption(secret)

        conn = get_db(db_path)
        try:
            tech = _load_tech_from_db(conn)
            merged.update(tech)

            content = _load_content_from_db(conn)
            merged.update(content)
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("DB config load failed (DB may not be initialized): %s", exc)

    return merged


# Backward-compat aliases
load_tech_yaml = load_config
load_config_yaml_only = load_config
