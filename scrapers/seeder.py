"""
scrapers/seeder.py — Simplified Entity Seeder
=============================================
Processes raw scraped data and stores in DB using simplified model.
Handles LLM enrichment and tag extraction.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from db.models import (
    DB_PATH, get_db, init_db, upsert_entity
)
from llm.enricher import LLMEnricher

log = logging.getLogger("mcp.seeder")


# --- TAG NORMALIZATION ---

TECH_ALIASES = {
    "js": "JavaScript",
    "ts": "TypeScript",
    "py": "Python",
    "c#": "C#",
    "cpp": "C++",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mongo": "MongoDB",
    "redis": "Redis",
    "docker": "Docker",
    "k8s": "Kubernetes",
    "aws": "AWS",
    "gcp": "Google Cloud",
    "azure": "Azure",
    "react": "React",
    "vue": "Vue.js",
    "angular": "Angular",
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "nodejs": "Node.js",
    "node": "Node.js",
    "tf": "TensorFlow",
    "pytorch": "PyTorch",
    "scikit": "Scikit-learn",
    "sklearn": "Scikit-learn",
}


def norm_tag(tag: str) -> str:
    """Normalize tag using aliases."""
    return TECH_ALIASES.get(tag.lower().strip(), tag.strip())


# --- SEEDER CLASS ---

class Seeder:

    def __init__(self, llm: LLMEnricher, db_path: Path = DB_PATH, config: dict = None):
        self.llm = llm
        self.db_path = db_path
        self.config = config or {}
        self._seen_titles: set[str] = set()

    def seed_all(self, raw_items: list[dict], owner_cfg: dict, enrich_llm: bool = True):
        """
        Main entry point. Pass ALL scraped items from all sources.
        Set enrich_llm=False to skip LLM processing during seeding.
        """
        conn = get_db(self.db_path)
        init_db(self.db_path)

        try:
            # 1. Seed the owner (personal entity)
            self._seed_owner(conn, owner_cfg)

            # 2. Process all entities
            for item in raw_items:
                self._seed_entity(conn, item, enrich_llm=enrich_llm)

            conn.commit()
            log.info(f"Seeding complete: {len(raw_items)} items processed")
        except Exception as e:
            conn.rollback()
            log.error(f"Seeding failed: {e}")
            raise
        finally:
            conn.close()

    # --- OWNER ---

    def _seed_owner(self, conn: sqlite3.Connection, cfg: dict):
        """Seed the 'personal' entity (profile owner)."""
        eid = upsert_entity(conn, {
            "flavor":      "personal",
            "title":       cfg.get("name", ""),
            "description": cfg.get("tagline", ""),
            "url":         cfg.get("blog_url"),
            "source":      "manual",
            "tags":        cfg.get("tags", []),
        })
        log.info(f"Owner entity: {cfg.get('name')} ({eid})")
        self._seen_titles.add(cfg.get("name", "").lower())
        return eid

    # --- GENERIC ENTITY SEEDER ---

    def _seed_entity(self, conn: sqlite3.Connection, item: dict, enrich_llm: bool = True) -> Optional[str]:
        """Seed a single entity with optional LLM enrichment."""
        flavor = item.get("flavor")
        if not flavor or not item.get("title"):
            log.warning(f"  Skipping item without flavor or title: {item}")
            return None

        title = item.get("title", "")
        source = item.get("source", "")
        
        # Deduplicate by title (case-insensitive)
        key = f"{source}:{title.lower()}"
        if key in self._seen_titles:
            log.debug(f"  Skipping duplicate: {title}")
            return None
        self._seen_titles.add(key)

        # Normalize tags
        technologies = [norm_tag(t) for t in item.get("technologies", [])]
        skills = [norm_tag(t) for t in item.get("skills", [])]
        tags = [norm_tag(t) for t in item.get("tags", [])]

        # Check if LLM enrichment is enabled for this source
        source_cfg = self.config.get("oeuvre_sources", {}).get(source, {}) or \
                     self.config.get("stages", {})
        llm_enabled = source_cfg.get("llm-processing", True) if source_cfg else True

        # LLM enrichment
        llm_enriched = False
        llm_model = None
        llm_enriched_at = None
        
        if enrich_llm and llm_enabled and self.llm and flavor in ("stages", "oeuvre"):
            raw_text = item.get("description", "") or item.get("url", "")
            if raw_text:
                enrichment = self.llm.enrich(raw_text, flavor, item.get("category"))
                if enrichment:
                    # Update description if enriched
                    if enrichment.get("description"):
                        item["description"] = enrichment["description"]
                    # Merge extracted tags
                    technologies.extend(enrichment.get("technologies", []))
                    skills.extend(enrichment.get("skills", []))
                    tags.extend(enrichment.get("tags", []))
                    llm_enriched = True
                    llm_model = self.llm.model_name
                    from db.models import now_iso
                    llm_enriched_at = now_iso()
                    log.debug(f"  LLM enriched: {title}")

        # Remove duplicates from tags
        technologies = list(set(technologies))
        skills = list(set(skills))
        tags = list(set(tags))

        # Build entity dict
        entity = {
            "flavor":          flavor,
            "category":        item.get("category"),
            "title":           title,
            "description":     item.get("description"),
            "url":             item.get("url"),
            "source":          source,
            "source_url":      item.get("source_url"),
            "start_date":      item.get("start_date"),
            "end_date":        item.get("end_date"),
            "date":            item.get("date"),
            "is_current":      item.get("is_current", 0),
            "language":        item.get("language", "en"),
            "visibility":      item.get("visibility", "public"),
            "technologies":    technologies,
            "skills":          skills,
            "tags":            tags,
            "llm_enriched":    llm_enriched,
            "llm_enriched_at": llm_enriched_at,
            "llm_model":       llm_model,
        }

        eid = upsert_entity(conn, entity)
        log.debug(f"  {flavor}/{item.get('category', 'N/A')}: {title} ({eid[:8]})")
        return eid
