"""
scrapers/seeder.py — Entity Seeder
====================================
Takes raw dicts from scrapers, normalises them into the Entity graph,
resolves cross-references (company FK, institution FK, technology relations),
and optionally enriches descriptions via LLM.

This is the intelligence layer between raw scrape and clean DB records.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from db.models import (
    get_db, init_db, upsert_entity, upsert_extension,
    add_relation, list_entities, DB_PATH
)
from llm.enricher import LLMEnricher

log = logging.getLogger("mcp.seeder")

# ─────────────────────────────────────────────────────────────────────────────
# TECHNOLOGY NORMALISATION TABLE
# Maps common aliases → canonical tag name
# ─────────────────────────────────────────────────────────────────────────────

TECH_ALIASES: dict[str, str] = {
    "py":           "Python",
    "js":           "JavaScript",
    "ts":           "TypeScript",
    "c#":           "CSharp",
    "c++":          "CPlusPlus",
    "cpp":          "CPlusPlus",
    "vb":           "VisualBasic",
    "vba":          "VBA",
    "jupyter":      "JupyterNotebook",
    "nb":           "JupyterNotebook",
    "pytorch":      "PyTorch",
    "tf":           "TensorFlow",
    "gpt":          "OpenAI",
    "gpt-4":        "OpenAI",
    "llm":          "LLM",
    "copilot":      "CopilotStudio",
    "powerautomate":"PowerAutomate",
    "pa":           "PowerAutomate",
    "aep":          "AdobeExperiencePlatform",
    "cja":          "AdobeCJA",
    "aa":           "AdobeAnalytics",
    "al":           "AdobeLaunch",
    "ga":           "GoogleAnalytics",
    "gds":          "GoogleDataStudio",
}

# ─────────────────────────────────────────────────────────────────────────────
# TECHNOLOGIES TABLE  (tag_type='technology')
# Maps specific technologies, tools, platforms, frameworks, languages etc.
# to their category (language, framework, platform, cloud, database, tool).
# Anything in this dict gets stored with tag_type='technology'.
# ─────────────────────────────────────────────────────────────────────────────

TECHNOLOGIES: dict[str, str] = {
    "Python": "language", "JavaScript": "language", "TypeScript": "language",
    "PHP": "language", "CSharp": "language", "CPlusPlus": "language",
    "VBA": "language", "VisualBasic": "language", "Rust": "language",
    "FSharp": "language", "Ruby": "language", "Java": "language",
    "Bash": "language", "Shell": "language", "SQL": "language",
    "HTML": "language", "CSS": "language",

    "PyTorch": "framework", "TensorFlow": "framework",
    "FastAPI": "framework", "Flask": "framework",
    "React": "framework", "Vue": "framework",
    "Cordova": "framework", "Gradio": "framework",
    "Hugo": "framework", "WordPress": "framework",

    "OpenAI": "platform", "CopilotStudio": "platform",
    "PowerAutomate": "platform", "WPPOpen": "platform",
    "AdobeExperiencePlatform": "platform", "AdobeAnalytics": "platform",
    "AdobeLaunch": "platform", "AdobeCJA": "platform",
    "AdobeTarget": "platform", "GoogleAnalytics": "platform",
    "GoogleDataStudio": "platform", "Snowplow": "platform",
    "VWO": "platform", "Acquia": "platform", "Econda": "platform",
    "CleverReach": "platform",

    "AWS": "cloud", "Docker": "cloud",

    "MongoDB": "database", "MySQL": "database", "SQLite": "database",
    "BigQuery": "database",

    "Git": "tool", "GitHub": "tool", "VSCode": "tool",
    "Tableau": "tool", "Adverity": "tool", "SSIS": "tool",
    "JupyterNotebook": "tool",
}



# ─────────────────────────────────────────────────────────────────────────────
# CAPABILITIES  (tag_type='capability')
# Broad competencies and domain expertise as opposed to specific tools/technologies.
# Anything in this set gets stored with tag_type='capability' rather than 'technology'.
# ─────────────────────────────────────────────────────────────────────────────

CAPABILITIES: set[str] = {
    "DataAnalytics", "DataEngineering", "DataVisualization", "Analytics",
    "BusinessIntelligence", "MachineLearning", "GenAI", "ArtificialIntelligence",
    "SEO", "SEM", "ContentMarketing", "OnlineMarketing", "DigitalMarketing",
    "ProjectManagement", "ProductManagement", "ProcessAutomation", "Automation",
    "WebTracking", "AttributionModeling", "ABTesting", "Personalization",
    "WebDevelopment", "BackendDevelopment", "FrontendDevelopment", "MobileDevelopment",
    "APIDesign", "SystemsOps", "CloudOps", "DevOps", "DataIntegration",
    "ETL", "Scraping", "WebScraping", "NLP", "ComputerVision",
    "EcommerceAnalytics", "SocialMediaAnalytics", "EmailMarketing",
    "DocumentAutomation", "LLM", "AgenticAI",
    "Consulting", "TechnicalWriting", "Teaching",
}

# Sub-type mappings from employment_type / inst_type → sub_type values
PROFESSIONAL_SUBTYPE_MAP: dict[str, str] = {
    "full_time":  "full_time",
    "part_time":  "part_time",
    "contract":   "contract",
    "freelance":  "freelance",
    "intern":     "intern",
}

EDUCATION_SUBTYPE_MAP: dict[str, str] = {
    "university": "university",
    "school":     "school",
    "vocational": "school",
    "bootcamp":   "bootcamp",
    "online":     "online",
    "military":   "military",
    "exchange":   "exchange",
}

# Oeuvre sub-type mapping (for side_project and literature entities)
# These map to the explicit sub_type values used in config's sub_type_override
OEUVRE_SUBTYPE_MAP: dict[str, str] = {
    "coding":     "coding",      # GitHub repos, code projects
    "article":    "article",     # Medium articles, publications
    "blog_post":  "blog_post",   # Blog posts
    "book":       "book",        # Books, long-form writing
    "website":    "website",     # Portfolio sites, web projects
    "podcast":    "podcast",     # Podcast episodes
    "video":      "video",       # YouTube videos, courses
}


def _norm_tag(tag: str) -> str:
    return TECH_ALIASES.get(tag.lower(), tag)


# ─────────────────────────────────────────────────────────────────────────────
# SEEDER CLASS
# ─────────────────────────────────────────────────────────────────────────────

class Seeder:

    def __init__(self, llm: LLMEnricher, db_path: Path = DB_PATH, config: dict = None):
        self.llm = llm
        self.db_path = db_path
        self.config = config or {}  # Store config for per-source LLM control
        self._company_index:     dict[str, str] = {}   # title.lower() → entity_id
        self._institution_index: dict[str, str] = {}
        self._tech_index:        dict[str, str] = {}   # tag.lower() → entity_id
        self._entity_index:      dict[str, str] = {}   # title.lower() → entity_id

    def seed_all(self, raw_items: list[dict], owner_cfg: dict, enrich_llm: bool = True):
        """Main entry point. Pass ALL scraped items from all sources.
        Set enrich_llm=False to skip LLM processing during seeding."""
        conn = get_db(self.db_path)
        init_db(self.db_path)

        try:
            # 1. Seed the owner (person entity)
            self._seed_owner(conn, owner_cfg)

            # 2. First pass: companies + institutions (need IDs before professionals/education)
            companies    = [i for i in raw_items if i.get("type") == "company"]
            institutions = [i for i in raw_items if i.get("type") == "institution"]
            self._seed_companies(conn, companies)
            self._seed_institutions(conn, institutions)

            # 3. Second pass: technologies (collect from tags across all items)
            self._seed_technologies(conn, raw_items)

            # 4. Main entities
            for item in raw_items:
                t = item.get("type")
                if t in ("company", "institution"):
                    continue   # already done
                self._seed_entity(conn, item, enrich_llm=enrich_llm)

            conn.commit()

            # Export stages template if configured
            stages_config = self.config.get("stages", {})
            if stages_config.get("export_template"):
                from .stages_template_generator import export_stages_template

                stages_items = [i for i in raw_items if i.get("type") in
                                ("professional", "company", "education", "institution", "achievement")]

                if stages_items:
                    output_path = Path("stages_template.json")
                    export_stages_template(stages_items, output_path)

            log.info("Seeding complete")
        except Exception as e:
            conn.rollback()
            log.error(f"Seeding failed: {e}")
            raise
        finally:
            conn.close()

    # ── Owner ────────────────────────────────────────────────────────────────

    def _seed_owner(self, conn: sqlite3.Connection, cfg: dict):
        """Seed the 'person' entity (profile owner)."""
        eid = upsert_entity(conn, {
            "type":        "person",
            "title":       cfg.get("name", ""),
            "slug":        "owner",
            "description": cfg.get("tagline", ""),
            "url":         cfg.get("blog_url"),
            "source":      "manual",
            "tags":        cfg.get("tags", []),
        })
        # Store extra identity fields in raw_data (no ext table for person)
        conn.execute("""
            UPDATE entities SET raw_data=? WHERE id=?
        """, (str({
            "tagline":  cfg.get("tagline"),
            "location": cfg.get("location"),
            "github":   f"https://github.com/{cfg.get('github_username','')}",
            "medium":   cfg.get("medium_url"),
            "linkedin": cfg.get("linkedin_url"),
            "email":    cfg.get("email"),
        }), eid))
        self._entity_index[cfg.get("name","").lower()] = eid
        log.info(f"Owner entity: {cfg.get('name')} ({eid})")
        return eid

    # ── Companies ────────────────────────────────────────────────────────────

    def _seed_companies(self, conn: sqlite3.Connection, items: list[dict]):
        for item in items:
            title = item.get("title", "")
            key   = title.lower()
            if key in self._company_index:
                continue
            eid = upsert_entity(conn, item)
            upsert_extension(conn, eid, "company", item.get("ext", {}))
            self._company_index[key] = eid
            self._entity_index[key]  = eid
            log.debug(f"  company: {title}")

    # ── Institutions ─────────────────────────────────────────────────────────

    def _seed_institutions(self, conn: sqlite3.Connection, items: list[dict]):
        for item in items:
            title = item.get("title", "")
            key   = title.lower()
            if key in self._institution_index:
                continue
            eid = upsert_entity(conn, item)
            upsert_extension(conn, eid, "institution", item.get("ext", {}))
            self._institution_index[key] = eid
            self._entity_index[key]      = eid
            log.debug(f"  institution: {title}")

    # ── Technologies ─────────────────────────────────────────────────────────

    def _seed_technologies(self, conn: sqlite3.Connection, items: list[dict]):
        """Collect all technology tags across all items, create technology entities.
        Capability tags are stored separately and do NOT get their own tech entity."""
        all_tags: set[str] = set()
        for item in items:
            # Use both legacy 'tags' and explicit 'tech_tags'
            for tag in list(item.get("tags", [])) + list(item.get("tech_tags", [])):
                nt = _norm_tag(tag)
                # Only create technology entities for known tech categories, skip capabilities
                if nt and len(nt) > 1 and nt not in CAPABILITIES and nt in TECHNOLOGIES:
                    all_tags.add(nt)

        for tag in sorted(all_tags):
            key = tag.lower()
            if key in self._tech_index:
                continue
            category = TECHNOLOGIES.get(tag, "tool")
            eid = upsert_entity(conn, {
                "type":   "technology",
                "title":  tag,
                "source": "derived",
                "tags":   [tag, category],
            })
            upsert_extension(conn, eid, "technology", {
                "category": category,
                "proficiency": "proficient",
            })
            self._tech_index[key] = eid
            log.debug(f"  tech: {tag} ({category})")

    # ── Generic entity seeder ─────────────────────────────────────────────────

    def _seed_entity(self, conn: sqlite3.Connection, item: dict, enrich_llm: bool = True) -> Optional[str]:
        """Seed a single entity. Set enrich_llm=False to skip LLM processing."""
        etype = item.get("type")
        if not etype or not item.get("title"):
            return None

        # LLM enrichment (only if enabled and configured)
        desc = item.get("description", "")
        llm_enriched = False
        if enrich_llm and self.llm:
            # Enrich description if empty or short
            if len(desc) < 30:
                raw = item.get("ext", {}).get("readme") or desc or item.get("title", "")
                desc = self.llm.enrich_description(raw, context=f"{etype}: {item.get('title')}")
                item["description"] = desc
                llm_enriched = True

            # LLM suggest tags if few
            if len(item.get("tags", [])) < 2 and desc:
                suggested = self.llm.suggest_tags(desc)
                item["tags"] = list(set(item.get("tags", []) + suggested))
                llm_enriched = True

        # Track LLM enrichment in item
        if llm_enriched:
            item["llm_enriched"] = 1
            # Model name from LLM config
            if hasattr(self.llm, 'model'):
                item["llm_model"] = self.llm.model

        # Classify tags: capability vs technology vs generic
        raw_tags   = [_norm_tag(t) for t in item.get("tags", [])]
        capability_tags = [t for t in raw_tags if t in CAPABILITIES]
        # Explicit capabilities from linkedin export
        for sk in item.get("skills", []):
            sk_norm = _norm_tag(sk)
            if sk_norm not in capability_tags:
                capability_tags.append(sk_norm)
        tech_tags  = [t for t in raw_tags if t in TECHNOLOGIES and t not in CAPABILITIES]
        # Also carry explicit tech_tags from source
        for tt in item.get("tech_tags", []):
            tt_norm = _norm_tag(tt)
            if tt_norm not in tech_tags:
                tech_tags.append(tt_norm)
        generic_tags = [t for t in raw_tags if t not in CAPABILITIES and t not in TECHNOLOGIES]
        # Rebuild item tags (generic only — capability+tech stored separately)
        item["tags"]            = generic_tags
        item["capability_tags"] = capability_tags
        item["tech_tags"]       = tech_tags

        # Infer sub_type if not set
        if not item.get("sub_type"):
            etype = item.get("type", "")
            ext_data = item.get("ext", {})
            if etype == "professional":
                item["sub_type"] = PROFESSIONAL_SUBTYPE_MAP.get(
                    ext_data.get("employment_type", ""), "full_time"
                )
            elif etype == "education":
                # exchange flag overrides inst_type
                if ext_data.get("exchange"):
                    item["sub_type"] = "exchange"
                else:
                    item["sub_type"] = EDUCATION_SUBTYPE_MAP.get(
                        ext_data.get("inst_type", ""), "university"
                    )
            elif etype in ("side_project", "literature"):
                # Use sub_type_override from config if available
                source_config = self.config.get("oeuvre_sources", {}).get(item.get("source"), {})
                sub_type_override = source_config.get("sub_type_override")

                if sub_type_override and sub_type_override in OEUVRE_SUBTYPE_MAP:
                    item["sub_type"] = sub_type_override
                else:
                    # Fallback: try to infer from source name or default to coding
                    src = item.get("source", "")
                    item["sub_type"] = OEUVRE_SUBTYPE_MAP.get(src, "coding")

        # Resolve extension cross-references
        ext = dict(item.get("ext", {}))
        if "_company_title" in ext:
            co_key = ext.pop("_company_title", "").lower()
            ext["company_id"] = self._company_index.get(co_key)
        if "_institution_title" in ext:
            inst_key = ext.pop("_institution_title", "").lower()
            ext["institution_id"] = self._institution_index.get(inst_key)

        eid = upsert_entity(conn, item)
        upsert_extension(conn, eid, etype, ext)

        # ── Relations ────────────────────────────────────────────────────────

        # Link entity → technologies via tech_tags (creates used_technology relations)
        for tag in item.get("tech_tags", []):
            tech_id = self._tech_index.get(tag.lower())
            if tech_id:
                add_relation(conn, eid, tech_id, "used_technology",
                             note=f"{item.get('title')} used {tag}")
        # Also link legacy 'tags' that happen to be known tech (backwards compat)
        for tag in item.get("tags", []):
            nt = _norm_tag(tag)
            tech_id = self._tech_index.get(nt.lower())
            if tech_id:
                add_relation(conn, eid, tech_id, "used_technology",
                             note=f"{item.get('title')} used {nt}")

        # Professional → company
        if etype == "professional" and ext.get("company_id"):
            add_relation(conn, eid, ext["company_id"], "worked_at")

        # Education → institution
        if etype == "education" and ext.get("institution_id"):
            add_relation(conn, eid, ext["institution_id"], "studied_at")

        # Side project → professional (part_of)
        if etype == "side_project" and "_part_of_job" in ext:
            job_title = ext.pop("_part_of_job", "").lower()
            job_id    = self._entity_index.get(job_title)
            if job_id:
                add_relation(conn, eid, job_id, "part_of")

        self._entity_index[item.get("title", "").lower()] = eid
        log.debug(f"  seeded {etype}: {item.get('title')[:60]}")
        return eid

    # ── LLM Post-processing ────────────────────────────────────────────────────

    def enrich_entity(self, entity_id: str, force: bool = False) -> bool:
        """Enrich a single entity with LLM. Returns True if enriched."""
        if not self.llm:
            log.warning("LLM not configured, skipping enrichment")
            return False

        conn = get_db(self.db_path)
        try:
            # Fetch entity
            row = conn.execute(
                "SELECT id, type, title, description, llm_enriched, source FROM entities WHERE id=?",
                (entity_id,)
            ).fetchone()

            if not row:
                log.error(f"Entity {entity_id} not found")
                return False

            entity = dict(row)

            # Check if already enriched
            if entity.get("llm_enriched") and not force:
                log.debug(f"Entity {entity_id} already enriched, skipping")
                return False

            # Check if source has llm-processing enabled
            source_name = entity.get("source")
            if source_name and self.config:
                source_cfg = self.config.get("sources", {}).get(source_name, {})
                if not source_cfg.get("llm-processing", True):
                    log.debug(f"Source {source_name} has llm-processing disabled, skipping")
                    return False

            # Enrich description
            desc = entity.get("description", "")
            enriched = False
            if len(desc) < 30:
                raw = desc or entity.get("title", "")
                desc = self.llm.enrich_description(raw, context=f"{entity['type']}: {entity['title']}")
                enriched = True

            # Suggest tags (fetch existing tags first)
            existing_tags = [
                row[0] for row in conn.execute(
                    "SELECT tag FROM tags WHERE entity_id=?", (entity_id,)
                ).fetchall()
            ]

            new_tags = existing_tags
            if len(existing_tags) < 2 and desc:
                suggested = self.llm.suggest_tags(desc)
                new_tags = list(set(existing_tags + suggested))
                enriched = True

            if not enriched:
                log.debug(f"Entity {entity_id} doesn't need enrichment")
                return False

            # Update entity
            model_name = self.llm.model if hasattr(self.llm, 'model') else "unknown"
            conn.execute(
                """UPDATE entities
                   SET description=?, llm_enriched=1, llm_enriched_at=datetime('now'),
                       llm_model=?, updated_at=datetime('now')
                   WHERE id=?""",
                (desc, model_name, entity_id)
            )

            # Update tags (delete old, insert new)
            conn.execute("DELETE FROM tags WHERE entity_id=?", (entity_id,))
            for tag in new_tags:
                # Tag classification: capability vs technology vs generic
                tag_type = "generic"
                if tag in CAPABILITIES:
                    tag_type = "capability"
                elif tag in TECHNOLOGIES:
                    tag_type = "technology"

                conn.execute(
                    "INSERT INTO tags (entity_id, tag, tag_type) VALUES (?, ?, ?)",
                    (entity_id, tag, tag_type)
                )

            conn.commit()
            log.info(f"Enriched entity {entity_id}: {entity['title']}")
            return True

        except Exception as e:
            log.error(f"Failed to enrich entity {entity_id}: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def enrich_all(self, source: str = None, batch_size: int = 50) -> int:
        """Enrich all unenriched entities. Returns count of enriched entities."""
        if not self.llm:
            log.warning("LLM not configured, skipping enrichment")
            return 0

        conn = get_db(self.db_path)
        try:
            # Find entities needing enrichment
            query = "SELECT id FROM entities WHERE (llm_enriched = 0 OR llm_enriched IS NULL)"
            params = []

            if source:
                query += " AND source=?"
                params.append(source)

            query += f" LIMIT {batch_size}"

            entity_ids = [row[0] for row in conn.execute(query, params).fetchall()]

            if not entity_ids:
                log.info("No entities needing enrichment")
                return 0

            log.info(f"Found {len(entity_ids)} entities needing enrichment")

            count = 0
            for i, entity_id in enumerate(entity_ids, 1):
                log.info(f"Processing {i}/{len(entity_ids)}: {entity_id}")
                if self.enrich_entity(entity_id):
                    count += 1

                # Rate limiting for API calls
                if count > 0 and count % 10 == 0:
                    import time
                    time.sleep(0.5)  # Avoid hitting rate limits

            log.info(f"Enriched {count}/{len(entity_ids)} entities")
            return count

        finally:
            conn.close()

    # ── YAML Update (Cascade) ─────────────────────────────────────────────────

    def update_entity(self, conn: sqlite3.Connection, item: dict) -> bool:
        """
        Update existing entity from YAML with cascade updates.
        
        Cascade logic:
          1. Update entity row (title, description, dates, etc.)
          2. Update ext_* table
          3. Delete old tags → insert new tags (with classification)
          4. Delete old relations → rebuild relations
        
        Args:
            conn: DB connection
            item: Entity dict with entity_id (from YAML connector)
        
        Returns:
            True if successful, False otherwise
        """
        entity_id = item.get("entity_id")
        if not entity_id:
            log.error("Cannot update entity without entity_id")
            return False
        
        # Verify entity exists
        existing = conn.execute(
            "SELECT id, type FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
        
        if not existing:
            log.error(f"Entity {entity_id} not found in DB")
            return False
        
        etype = existing["type"]
        
        if etype != item.get("type"):
            log.warning(
                f"Entity type mismatch: DB has {etype}, YAML has {item.get('type')}. "
                f"Using DB type."
            )
            item["type"] = etype
        
        log.info(f"Updating {etype}: {item.get('title')} ({entity_id})")
        
        try:
            # ── 1. Classify tags (capability vs technology vs generic) ───────
            raw_tags = [_norm_tag(t) for t in item.get("tags", [])]
            capability_tags = [t for t in raw_tags if t in CAPABILITIES]
            
            # Add explicit skills as capability tags
            for sk in item.get("skills", []):
                sk_norm = _norm_tag(sk)
                if sk_norm not in capability_tags:
                    capability_tags.append(sk_norm)
            
            tech_tags = [t for t in raw_tags if t in TECHNOLOGIES and t not in CAPABILITIES]
            
            # Also include explicit tech_tags
            for tt in item.get("tech_tags", []):
                tt_norm = _norm_tag(tt)
                if tt_norm not in tech_tags:
                    tech_tags.append(tt_norm)
            
            generic_tags = [t for t in raw_tags if t not in CAPABILITIES and t not in TECHNOLOGIES]
            
            # ── 2. Update entity row ──────────────────────────────────────────
            conn.execute("""
                UPDATE entities
                SET title=?, description=?, start_date=?, end_date=?, is_current=?,
                    url=?, sub_type=?, updated_at=datetime('now')
                WHERE id=?
            """, (
                item.get("title"),
                item.get("description"),
                item.get("start_date"),
                item.get("end_date"),
                1 if item.get("is_current") else 0,
                item.get("url"),
                item.get("sub_type"),
                entity_id
            ))
            
            # ── 3. Update extension table ─────────────────────────────────────
            ext = dict(item.get("ext", {}))
            
            # Resolve company/institution FKs
            if "_company_title" in ext:
                co_key = ext.pop("_company_title", "").lower()
                ext["company_id"] = self._company_index.get(co_key)
                if not ext["company_id"]:
                    log.warning(f"Company '{co_key}' not found in index")
            
            if "_institution_title" in ext:
                inst_key = ext.pop("_institution_title", "").lower()
                ext["institution_id"] = self._institution_index.get(inst_key)
                if not ext["institution_id"]:
                    log.warning(f"Institution '{inst_key}' not found in index")
            
            # Update ext table
            upsert_extension(conn, entity_id, etype, ext)
            
            # ── 4. Delete old tags → insert new classified tags ───────────────
            conn.execute("DELETE FROM tags WHERE entity_id=?", (entity_id,))
            
            # Insert generic tags
            for tag in generic_tags:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (entity_id, tag, tag_type) VALUES (?, ?, 'generic')",
                    (entity_id, tag)
                )
            
            # Insert capability tags
            for tag in capability_tags:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (entity_id, tag, tag_type) VALUES (?, ?, 'capability')",
                    (entity_id, tag)
                )
            
            # Insert technology tags
            for tag in tech_tags:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (entity_id, tag, tag_type) VALUES (?, ?, 'technology')",
                    (entity_id, tag)
                )
            
            # ── 5. Delete old relations → rebuild ─────────────────────────────
            # Delete all outgoing relations from this entity
            conn.execute("DELETE FROM relations WHERE from_id=?", (entity_id,))
            
            # Rebuild technology relations
            for tag in tech_tags:
                tech_id = self._tech_index.get(tag.lower())
                if tech_id:
                    add_relation(conn, entity_id, tech_id, "used_technology",
                                note=f"{item.get('title')} used {tag}")
            
            # Rebuild company/institution relations
            if etype == "professional" and ext.get("company_id"):
                add_relation(conn, entity_id, ext["company_id"], "worked_at")
            
            if etype == "education" and ext.get("institution_id"):
                add_relation(conn, entity_id, ext["institution_id"], "studied_at")
            
            # ── 6. Handle nested projects (professional → side_projects) ──────
            if etype == "professional" and "_projects" in ext:
                for proj_item in ext.pop("_projects", []):
                    if proj_item.get("entity_id"):
                        # Update existing project
                        self.update_entity(conn, proj_item)
                    else:
                        # Create new project
                        self._seed_entity(conn, proj_item, enrich_llm=False)
            
            log.info(f"  ✓ Updated {etype}: {item.get('title')}")
            return True
        
        except Exception as e:
            log.error(f"Failed to update entity {entity_id}: {e}")
            return False

    def update_from_yaml(
        self,
        yaml_path: Path,
        entity_id: Optional[str] = None,
        enrich_llm: bool = False
    ) -> int:
        """
        Update entities from YAML export file.
        
        Args:
            yaml_path: Path to YAML file
            entity_id: If provided, update only this entity. Otherwise update all.
            enrich_llm: If True, run LLM enrichment after update
        
        Returns:
            Number of entities updated
        """
        from .yaml_connector import YamlConnector
        
        connector = YamlConnector(yaml_path)
        entities = connector.parse(entity_id=entity_id)
        
        if not entities:
            log.warning(f"No entities found in {yaml_path}")
            return 0
        
        conn = get_db(self.db_path)
        init_db(self.db_path)
        
        try:
            # Rebuild indexes (needed for FK resolution)
            self._rebuild_indexes(conn)
            
            count = 0
            for item in entities:
                if self.update_entity(conn, item):
                    count += 1
                    
                    # Optional LLM enrichment after update
                    if enrich_llm and self.llm:
                        self.enrich_entity(item["entity_id"], force=True)
            
            conn.commit()
            log.info(f"Updated {count}/{len(entities)} entities from {yaml_path}")
            return count
        
        except Exception as e:
            conn.rollback()
            log.error(f"YAML update failed: {e}")
            raise
        finally:
            conn.close()

    def _rebuild_indexes(self, conn: sqlite3.Connection):
        """Rebuild internal indexes for FK resolution."""
        # Company index
        for row in conn.execute("SELECT id, title FROM entities WHERE type='company'"):
            self._company_index[row[1].lower()] = row[0]
        
        # Institution index
        for row in conn.execute("SELECT id, title FROM entities WHERE type='institution'"):
            self._institution_index[row[1].lower()] = row[0]
        
        # Technology index
        for row in conn.execute("SELECT id, title FROM entities WHERE type='technology'"):
            self._tech_index[row[1].lower()] = row[0]
        
        # General entity index
        for row in conn.execute("SELECT id, title FROM entities"):
            self._entity_index[row[1].lower()] = row[0]
        
        log.debug("Rebuilt entity indexes")
