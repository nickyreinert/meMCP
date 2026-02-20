"""
scrapers/yaml_exporter.py — YAML Export
========================================
Exports entities from DB to YAML files for manual editing.
Each exported item includes entity_id for selective updates.

Purpose:
  - Initial scrape → DB + YAML export
  - User manually edits YAML
  - Re-import via yaml connector with --yaml-update flag

Usage:
  from scrapers.yaml_exporter import export_to_yaml
  export_to_yaml(db_path, "medium_export.yaml", source="medium")
  export_to_yaml(db_path, "medium_export.yaml", source="medium")
"""

import logging
import sqlite3
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List

log = logging.getLogger("mcp.yaml_exporter")


def export_to_yaml(
    db_path: Path,
    output_path: Path,
    source: Optional[str] = None,
    entity_types: Optional[List[str]] = None
) -> int:
    """
    Export entities from DB to YAML format with entity_id.
    
    Args:
        db_path: Path to SQLite DB
        output_path: Where to write YAML file
        source: Filter by source (e.g., "linkedin", "medium")
        entity_types: Filter by entity types (e.g., ["professional", "education"])
    
    Returns:
        Number of entities exported
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Build query
        query = "SELECT * FROM entities WHERE 1=1"
        params = []
        
        if source:
            query += " AND source=?"
            params.append(source)
        
        if entity_types:
            placeholders = ",".join(["?"] * len(entity_types))
            query += f" AND type IN ({placeholders})"
            params.extend(entity_types)
        
        query += " ORDER BY type, start_date DESC NULLS LAST"
        
        rows = conn.execute(query, params).fetchall()
        
        if not rows:
            log.warning(f"No entities found for export (source={source})")
            return 0
        
        # Group by entity type
        grouped = _group_entities(conn, rows)
        
        # Write YAML
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            yaml.dump(
                grouped,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120
            )
        
        log.info(f"Exported {len(rows)} entities to {output_path}")
        return len(rows)
    
    finally:
        conn.close()


def _group_entities(conn: sqlite3.Connection, rows: List[sqlite3.Row]) -> Dict[str, Any]:
    """
    Group entities by type and format for YAML export.
    
    Structure:
      experience: [...]      # professional entities
      education: [...]       # education entities
      certifications: [...]  # achievement entities
      projects: [...]        # side_project entities
      articles: [...]        # literature entities
    """
    result = {}
    
    for row in rows:
        entity = dict(row)
        entity_type = entity["type"]
        entity_id = entity["id"]
        
        # Fetch tags
        tags_raw = conn.execute(
            "SELECT tag, tag_type FROM tags WHERE entity_id=? ORDER BY tag_type, tag",
            (entity_id,)
        ).fetchall()
        
        tags = [t["tag"] for t in tags_raw if t["tag_type"] == "generic"]
        tech_tags = [t["tag"] for t in tags_raw if t["tag_type"] == "technology"]
        capability_tags = [t["tag"] for t in tags_raw if t["tag_type"] == "capability"]
        
        # Fetch extension data based on type
        ext_data = _fetch_extension(conn, entity_id, entity_type)
        
        # Format based on entity type
        if entity_type == "professional":
            formatted = _format_professional(entity, ext_data, tags, tech_tags, capability_tags)
            result.setdefault("experience", []).append(formatted)
        
        elif entity_type == "education":
            formatted = _format_education(entity, ext_data, tags)
            result.setdefault("education", []).append(formatted)
        
        elif entity_type == "achievement":
            formatted = _format_achievement(entity, ext_data, tags)
            result.setdefault("certifications", []).append(formatted)
        
        elif entity_type == "side_project":
            formatted = _format_project(entity, ext_data, tags, tech_tags)
            result.setdefault("projects", []).append(formatted)
        
        elif entity_type == "literature":
            formatted = _format_literature(entity, ext_data, tags)
            result.setdefault("articles", []).append(formatted)
        
        elif entity_type in ("company", "institution"):
            # Export companies/institutions separately if needed
            formatted = _format_basic_entity(entity, ext_data, tags)
            result.setdefault(f"{entity_type}s", []).append(formatted)
    
    return result


def _fetch_extension(conn: sqlite3.Connection, entity_id: str, entity_type: str) -> Dict[str, Any]:
    """Fetch extension data from appropriate ext_* table."""
    ext_table = f"ext_{entity_type}"
    
    try:
        row = conn.execute(f"SELECT * FROM {ext_table} WHERE entity_id=?", (entity_id,)).fetchone()
        if row:
            data = dict(row)
            data.pop("entity_id", None)
            return {k: v for k, v in data.items() if v is not None}
        return {}
    except sqlite3.OperationalError:
        # Table doesn't exist for this type
        return {}


def _format_professional(
    entity: Dict,
    ext: Dict,
    tags: List[str],
    tech_tags: List[str],
    capability_tags: List[str]
) -> Dict:
    """Format professional entity for YAML export."""
    company_name = None
    if ext.get("company_id"):
        # Resolve company name from FK
        conn = sqlite3.connect(entity.get("_db_path", "db/profile.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT title FROM entities WHERE id=?", (ext["company_id"],)).fetchone()
        if row:
            company_name = row["title"]
        conn.close()
    
    return {
        "entity_id": entity["id"],
        "role": ext.get("role") or entity["title"].split(" at ")[0] if " at " in entity["title"] else entity["title"],
        "company": company_name or entity["title"].split(" at ")[-1] if " at " in entity["title"] else "Unknown",
        "start_date": entity.get("start_date"),
        "end_date": entity.get("end_date"),
        "employment_type": ext.get("employment_type", "full_time"),
        "location": ext.get("location"),
        "remote": bool(ext.get("remote")),
        "description": entity.get("description"),
        "tags": tech_tags,
        "skills": capability_tags,
        "industry": None,  # Could fetch from company entity
        "additional_url": None,
    }


def _format_education(entity: Dict, ext: Dict, tags: List[str]) -> Dict:
    """Format education entity for YAML export."""
    return {
        "entity_id": entity["id"],
        "institution": None,  # Resolve from institution_id
        "degree": ext.get("degree"),
        "field": ext.get("field"),
        "start_date": entity.get("start_date"),
        "end_date": entity.get("end_date"),
        "grade": ext.get("grade"),
        "exchange": bool(ext.get("exchange")),
        "description": entity.get("description"),
        "tags": tags,
    }


def _format_achievement(entity: Dict, ext: Dict, tags: List[str]) -> Dict:
    """Format achievement entity for YAML export."""
    return {
        "entity_id": entity["id"],
        "name": entity["title"],
        "issuer": None,  # Resolve from issuer_id
        "issued": entity.get("start_date"),
        "expires": ext.get("expires_at"),
        "credential_id": ext.get("credential_id"),
        "credential_url": ext.get("credential_url"),
        "tags": tags,
    }


def _format_project(entity: Dict, ext: Dict, tags: List[str], tech_tags: List[str]) -> Dict:
    """Format side_project entity for YAML export."""
    return {
        "entity_id": entity["id"],
        "title": entity["title"],
        "description": entity.get("description"),
        "url": entity.get("url"),
        "repo_url": ext.get("repo_url"),
        "demo_url": ext.get("demo_url"),
        "stars": ext.get("stars", 0),
        "forks": ext.get("forks", 0),
        "language": ext.get("language"),
        "status": ext.get("status", "active"),
        "tags": tech_tags,
        "start_date": entity.get("start_date"),
    }


def _format_literature(entity: Dict, ext: Dict, tags: List[str]) -> Dict:
    """Format literature entity for YAML export."""
    return {
        "entity_id": entity["id"],
        "title": entity["title"],
        "description": entity.get("description"),
        "url": entity.get("url"),
        "platform": ext.get("platform"),
        "published_at": ext.get("published_at") or entity.get("start_date"),
        "read_time": ext.get("read_time"),
        "lang": ext.get("lang", "en"),
        "tags": tags,
    }


def _format_basic_entity(entity: Dict, ext: Dict, tags: List[str]) -> Dict:
    """Generic formatting for company, institution, etc."""
    formatted = {
        "entity_id": entity["id"],
        "title": entity["title"],
        "description": entity.get("description"),
        "url": entity.get("url"),
        "tags": tags,
    }
    formatted.update(ext)
    return formatted
