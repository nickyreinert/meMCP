"""
scrapers/seeder.py — Simplified Entity Seeder
=============================================
Processes raw scraped data and stores in DB using simplified model.
Handles LLM enrichment and tag extraction.

YAML Sync Integration:
- After DB insertion, updates YAML cache with entity_id
- After LLM enrichment, updates YAML cache with enriched fields
- Supports bidirectional sync (YAML ↔ DB)
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional, Dict, List

from db.models import (
    DB_PATH, get_db, init_db, upsert_entity
)
from llm.enricher import LLMEnricher
from scrapers.yaml_sync import (
    update_yaml_after_db_insert,
    update_yaml_after_llm
)

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

    def seed_all(
        self,
        raw_items: list[dict],
        owner_cfg: dict,
        enrich_llm: bool = True,
        yaml_path: Optional[Path] = None,
        source_name: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Main entry point. Pass ALL scraped items from all sources.
        
        Args:
            raw_items: List of entity dictionaries from scrapers
            owner_cfg: Owner/profile configuration
            enrich_llm: Whether to run LLM enrichment during seeding
            yaml_path: Optional YAML file path for sync after DB insertion
            source_name: Optional source name to filter entities for YAML update
        
        Returns:
            Dictionary mapping entity keys (title or url) to entity_ids
        """
        conn = get_db(self.db_path)
        init_db(self.db_path)

        entity_mappings = []  # Track (entity, entity_id) for YAML sync
        entity_id_map = {}  # Return mapping of keys to entity_ids
        
        total_items = len(raw_items)
        log.info(f"Seeding {total_items} entities to database")
        
        try:
            # NOTE: Owner/personal entity removed - now handled by identity scraper
            # Identity data is loaded from identity.yaml via identity scraper
            # Run: python ingest.py --source identity

            # Process all entities
            for idx, item in enumerate(raw_items, 1):
                title = item.get("title", "Untitled")[:60]
                source = item.get("source", "unknown")
                log.info(f"  [{idx}/{total_items}] Seeding: {title} (source: {source})")
                
                entity_id = self._seed_entity(conn, item, enrich_llm=enrich_llm)
                if entity_id:
                    # Track for return value and YAML sync
                    key = item.get("url") or item.get("title")
                    if key:
                        entity_id_map[key] = entity_id
                    
                    if yaml_path:
                        # Track for YAML sync (filter by source if specified)
                        if not source_name or item.get("source") == source_name:
                            entity_mappings.append((item, entity_id))

            conn.commit()
            log.info(f"Seeding complete: {len(raw_items)} items processed")
            
            # 3. Update YAML cache with entity_ids
            if yaml_path and entity_mappings:
                # Build url -> entity_id mapping
                url_to_entity_id = {}
                for entity, entity_id in entity_mappings:
                    url = entity.get("url")
                    if url:
                        url_to_entity_id[url] = entity_id
                
                if url_to_entity_id:
                    log.info(f"Updating YAML cache with {len(url_to_entity_id)} entity_ids")
                    success = update_yaml_after_db_insert(yaml_path, url_to_entity_id)
                    if success:
                        log.info(f"✓ YAML cache updated with entity_ids")
            
            return entity_id_map
                
        except Exception as e:
            conn.rollback()
            log.error(f"Seeding failed: {e}")
            raise
        finally:
            conn.close()

    # --- DEPRECATED: Owner seeding removed ---
    # Identity data is now loaded via identity scraper from identity.yaml
    # def _seed_owner(self, conn: sqlite3.Connection, cfg: dict):
    #     """Seed the 'personal' entity (profile owner)."""
    #     eid = upsert_entity(conn, {
    #         "flavor":      "personal",
    #         "title":       cfg.get("name", ""),
    #         "description": cfg.get("tagline", ""),
    #         "url":         cfg.get("blog_url"),
    #         "source":      "manual",
    #         "tags":        cfg.get("tags", []),
    #     })
    #     log.info(f"Owner entity: {cfg.get('name')} ({eid})")
    #     self._seen_titles.add(cfg.get("name", "").lower())
    #     return eid

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
        source_cfg = self.config.get("oeuvre", {}).get(source, {}) or \
                     self.config.get("stages", {})
        llm_enabled = source_cfg.get("llm-processing", True) if source_cfg else True

        # LLM enrichment (skip if already enriched in source data, e.g., from YAML)
        llm_enriched = item.get("llm_enriched", 0)  # Check if already enriched
        llm_model = item.get("llm_model")
        llm_enriched_at = item.get("llm_enriched_at")
        
        if not llm_enriched and enrich_llm and llm_enabled and self.llm and flavor in ("stages", "oeuvre"):
            # Only enrich if not already enriched
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
                    llm_model = self.llm.model
                    from db.models import now_iso
                    llm_enriched_at = now_iso()
                    log.debug(f"  LLM enriched: {title}")
        elif llm_enriched:
            log.debug(f"  Using existing LLM enrichment: {title}")

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
            "canonical_url":   item.get("canonical_url"),
            "source":          source,
            "source_url":      item.get("source_url"),
            "start_date":      item.get("start_date"),
            "end_date":        item.get("end_date"),
            "date":            item.get("date") or item.get("published_at"),
            "is_current":      item.get("is_current", 0),
            "language":        item.get("language", "en"),
            "visibility":      item.get("visibility", "public"),
            "raw_data":        item.get("raw_data"),  # Pass through raw_data for identity entities
            "technologies":    technologies,
            "skills":          skills,
            "tags":            tags,
            "llm_enriched":    llm_enriched,
            "llm_enriched_at": llm_enriched_at,
            "llm_model":       llm_model,
        }

        eid = upsert_entity(conn, entity)
        llm_status = "✓ LLM enriched" if llm_enriched else "⊙ No LLM"
        log.info(f"      → Saved to DB: {eid[:8]}... ({llm_status})")
        return eid

    # --- LLM POST-PROCESSING ---

    def enrich_entity(
        self,
        entity_id: str,
        force: bool = False,
        yaml_path: Optional[Path] = None
    ) -> bool:
        """
        Enrich a single entity with LLM post-processing.
        
        Args:
            entity_id: Entity ID to enrich
            force: Force re-enrichment even if already enriched
            yaml_path: Optional YAML file path to update after enrichment
            
        Returns:
            True if enriched successfully
        """
        if not self.llm:
            log.warning("LLM not configured, skipping enrichment")
            return False

        conn = get_db(self.db_path)
        try:
            # Fetch entity
            row = conn.execute(
                """SELECT id, flavor, category, title, description, 
                   llm_enriched, source, url FROM entities WHERE id=?""",
                (entity_id,)
            ).fetchone()

            if not row:
                log.error(f"Entity {entity_id} not found")
                return False

            entity = dict(row)

            # Skip if already enriched (unless forced)
            if entity.get("llm_enriched") and not force:
                log.debug(f"Entity {entity_id} already enriched, skipping")
                return False

            # Check if source has LLM processing enabled
            source_name = entity.get("source")
            if source_name and self.config:
                source_cfg = self.config.get("oeuvre", {}).get(source_name, {}) or \
                             self.config.get("stages", {})
                if source_cfg and not source_cfg.get("llm-processing", True):
                    log.debug(f"Source {source_name} has llm-processing disabled")
                    return False

            # Enrich with LLM
            flavor = entity.get("flavor")
            if flavor not in ("stages", "oeuvre"):
                log.debug(f"Skipping enrichment for flavor: {flavor}")
                return False

            # Use description or URL as input text
            raw_text = entity.get("description", "") or entity.get("url", "")
            if not raw_text or len(raw_text) < 10:
                log.debug(f"Insufficient text for enrichment: {entity_id}")
                return False

            # Call LLM enrichment
            enrichment = self.llm.enrich(raw_text, flavor, entity.get("category"))
            if not enrichment:
                log.warning(f"LLM enrichment failed for {entity_id}")
                return False

            # Update entity in DB
            from db.models import now_iso
            llm_enriched_at = now_iso()
            conn.execute(
                """UPDATE entities SET 
                   description = ?,
                   llm_enriched = 1,
                   llm_enriched_at = ?,
                   llm_model = ?,
                   updated_at = ?
                   WHERE id = ?""",
                (
                    enrichment.get("description") or entity.get("description"),
                    llm_enriched_at,
                    self.llm.model,
                    now_iso(),
                    entity_id
                )
            )

            # Update tags in DB
            for tag_type, tags in [
                ("technology", enrichment.get("technologies", [])),
                ("skill", enrichment.get("skills", [])),
                ("generic", enrichment.get("tags", []))
            ]:
                for tag in tags:
                    normalized = norm_tag(tag)
                    conn.execute(
                        """INSERT OR IGNORE INTO tags (entity_id, tag, tag_type) 
                           VALUES (?, ?, ?)""",
                        (entity_id, normalized, tag_type)
                    )

            conn.commit()
            log.info(f"Enriched entity: {entity.get('title')} ({entity_id[:8]})")
            
            # Update YAML cache with enriched fields
            if yaml_path:
                entity_id_to_enrichment = {
                    entity_id: {
                        'description': enrichment.get("description"),
                        'technologies': enrichment.get("technologies", []),
                        'skills': enrichment.get("skills", []),
                        'tags': enrichment.get("tags", []),
                        'llm_model': self.llm.model,
                        'llm_enriched_at': llm_enriched_at
                    }
                }
                update_yaml_after_llm(yaml_path, entity_id_to_enrichment)
            
            return True

        except Exception as e:
            log.error(f"Failed to enrich entity {entity_id}: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def enrich_all(
        self,
        source: Optional[str] = None,
        batch_size: int = 50,
        yaml_path: Optional[Path] = None
    ) -> int:
        """
        Enrich all unenriched entities with LLM.
        
        Args:
            source: Optional source name to filter entities
            batch_size: Maximum number of entities to process
            yaml_path: Optional YAML file path to update after enrichment
            
        Returns:
            Count of successfully enriched entities
        """
        if not self.llm:
            log.warning("LLM not configured, skipping enrichment")
            return 0

        conn = get_db(self.db_path)
        try:
            # Find entities needing enrichment
            query = """SELECT id FROM entities 
                       WHERE (llm_enriched = 0 OR llm_enriched IS NULL)
                       AND flavor IN ('stages', 'oeuvre')"""
            params = []

            if source:
                query += " AND source = ?"
                params.append(source)

            query += f" LIMIT {batch_size}"

            entity_ids = [row[0] for row in conn.execute(query, params).fetchall()]

            if not entity_ids:
                log.info("No entities needing enrichment")
                return 0

            log.info(f"Found {len(entity_ids)} entities needing enrichment")

            count = 0
            for i, entity_id in enumerate(entity_ids, 1):
                log.info(f"Processing {i}/{len(entity_ids)}: {entity_id[:8]}")
                if self.enrich_entity(entity_id, yaml_path=yaml_path):
                    count += 1

                # Rate limiting for API calls
                if count > 0 and count % 10 == 0:
                    import time
                    time.sleep(0.5)

            log.info(f"Enriched {count}/{len(entity_ids)} entities")
            return count

        finally:
            conn.close()
