"""
scrapers/yaml_connector.py — YAML Connector
============================================
Reads YAML export files and updates entities in DB.

Purpose:
  Manual editing workflow:
    1. Initial scrape → DB + YAML export (with entity_id)
    2. User edits YAML file
    3. Run: python ingest.py --yaml-update --file <source>_export.yaml [--id <entity_id>]
  - Selective update: by entity_id or all entities in file
  - Cascade updates: automatically reprocesses tags, skills, relations
  - Generic: works with any platform (LinkedIn, Medium, GitHub, etc.)
  - Safe: requires entity_id to prevent accidental duplicates
"""

import logging
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional

log = logging.getLogger("mcp.yaml_connector")


class YamlConnector:
    """
    Generic YAML connector for manually edited entity files.
    
    Expected YAML structure (similar to auto-generated caches):
      experience: [{entity_id, role, company, ...}, ...]
      education: [{entity_id, institution, degree, ...}, ...]
      certifications: [{entity_id, name, issuer, ...}, ...]
      projects: [{entity_id, title, ...}, ...]
      articles: [{entity_id, title, url, ...}, ...]
    """
    
    def __init__(self, yaml_path: Path):
        self.yaml_path = yaml_path
        self.data: Optional[Dict[str, Any]] = None
    
    def load(self) -> bool:
        """Load and parse YAML file."""
        if not self.yaml_path.exists():
            log.error(f"YAML file not found: {self.yaml_path}")
            return False
        
        try:
            with open(self.yaml_path) as f:
                self.data = yaml.safe_load(f)
            
            if not self.data:
                log.error(f"Empty YAML file: {self.yaml_path}")
                return False
            
            log.info(f"Loaded YAML from {self.yaml_path}")
            return True
        
        except Exception as e:
            log.error(f"Failed to parse YAML: {e}")
            return False
    
    def parse(self, entity_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Parse YAML data into entity dicts for update.
        
        Args:
            entity_id: If provided, return only this entity. Otherwise return all.
        
        Returns:
            List of entity dicts ready for seeder update
        """
        if not self.data:
            if not self.load():
                return []
        
        entities = []
        
        # ── Experience (professional entities) ───────────────────────────────
        for item in self.data.get("experience", []):
            if not item.get("entity_id"):
                log.warning(f"Skipping professional entry without entity_id: {item.get('role')}")
                continue
            
            if entity_id and item["entity_id"] != entity_id:
                continue
            
            entity = self._parse_professional(item)
            entities.append(entity)
        
        # ── Education ─────────────────────────────────────────────────────────
        for item in self.data.get("education", []):
            if not item.get("entity_id"):
                log.warning(f"Skipping education entry without entity_id: {item.get('degree')}")
                continue
            
            if entity_id and item["entity_id"] != entity_id:
                continue
            
            entity = self._parse_education(item)
            entities.append(entity)
        
        # ── Certifications (achievement entities) ─────────────────────────────
        for item in self.data.get("certifications", []):
            if not item.get("entity_id"):
                log.warning(f"Skipping certification without entity_id: {item.get('name')}")
                continue
            
            if entity_id and item["entity_id"] != entity_id:
                continue
            
            entity = self._parse_achievement(item)
            entities.append(entity)
        
        # ── Projects (side_project entities) ──────────────────────────────────
        for item in self.data.get("projects", []):
            if not item.get("entity_id"):
                log.warning(f"Skipping project without entity_id: {item.get('title')}")
                continue
            
            if entity_id and item["entity_id"] != entity_id:
                continue
            
            entity = self._parse_project(item)
            entities.append(entity)
        
        # ── Articles (literature entities) ────────────────────────────────────
        for item in self.data.get("articles", []):
            if not item.get("entity_id"):
                log.warning(f"Skipping article without entity_id: {item.get('title')}")
                continue
            
            if entity_id and item["entity_id"] != entity_id:
                continue
            
            entity = self._parse_literature(item)
            entities.append(entity)
        
        log.info(f"Parsed {len(entities)} entities from YAML")
        return entities
    
    def _parse_professional(self, item: Dict) -> Dict:
        """Parse professional/experience entry."""
        # Projects within a job
        projects = []
        for proj in item.get("projects", []):
            projects.append({
                "type": "side_project",
                "entity_id": proj.get("entity_id"),  # May be None for new projects
                "title": proj.get("title"),
                "description": proj.get("description"),
                "tags": proj.get("tags", []),
                "tech_tags": proj.get("tech_tags", []),
                "ext": {
                    "_part_of_job": f"{item.get('role')} at {item.get('company')}",
                    "status": proj.get("status", "active"),
                },
            })
        
        return {
            "type": "professional",
            "entity_id": item["entity_id"],
            "title": f"{item.get('role', 'Role')} at {item.get('company', 'Company')}",
            "description": item.get("description"),
            "start_date": item.get("start_date"),
            "end_date": item.get("end_date"),
            "is_current": not bool(item.get("end_date")),
            "source": "yaml",
            "tags": item.get("tags", []),
            "tech_tags": item.get("tags", []),  # LinkedIn YAML uses 'tags' for tech
            "skills": item.get("skills", []),  # Capability tags
            "ext": {
                "role": item.get("role"),
                "employment_type": item.get("employment_type", "full_time"),
                "location": item.get("location"),
                "remote": item.get("remote", False),
                "_company_title": item.get("company"),  # Seeder resolves FK
                "_projects": projects,  # Nested projects
            },
        }
    
    def _parse_education(self, item: Dict) -> Dict:
        """Parse education entry."""
        return {
            "type": "education",
            "entity_id": item["entity_id"],
            "title": item.get("degree") or f"Studies at {item.get('institution')}",
            "description": item.get("description"),
            "start_date": item.get("start_date"),
            "end_date": item.get("end_date"),
            "source": "yaml",
            "tags": item.get("tags", []),
            "ext": {
                "degree": item.get("degree"),
                "field": item.get("field"),
                "grade": item.get("grade"),
                "exchange": 1 if item.get("exchange") else 0,
                "_institution_title": item.get("institution"),
            },
        }
    
    def _parse_achievement(self, item: Dict) -> Dict:
        """Parse certification/achievement entry."""
        return {
            "type": "achievement",
            "entity_id": item["entity_id"],
            "title": item.get("name"),
            "source": "yaml",
            "start_date": item.get("issued"),
            "tags": item.get("tags", []),
            "ext": {
                "credential_id": item.get("credential_id"),
                "credential_url": item.get("credential_url"),
                "expires_at": item.get("expires"),
                "_issuer_title": item.get("issuer"),
            },
        }
    
    def _parse_project(self, item: Dict) -> Dict:
        """Parse side_project entry."""
        return {
            "type": "side_project",
            "entity_id": item["entity_id"],
            "title": item.get("title"),
            "description": item.get("description"),
            "url": item.get("url"),
            "source": "yaml",
            "start_date": item.get("start_date"),
            "tags": item.get("tags", []),
            "tech_tags": item.get("tags", []),
            "ext": {
                "repo_url": item.get("repo_url"),
                "demo_url": item.get("demo_url"),
                "stars": item.get("stars", 0),
                "forks": item.get("forks", 0),
                "language": item.get("language"),
                "status": item.get("status", "active"),
            },
        }
    
    def _parse_literature(self, item: Dict) -> Dict:
        """Parse literature/article entry."""
        return {
            "type": "literature",
            "entity_id": item["entity_id"],
            "title": item.get("title"),
            "description": item.get("description"),
            "url": item.get("url"),
            "source": "yaml",
            "start_date": item.get("published_at"),
            "tags": item.get("tags", []),
            "ext": {
                "platform": item.get("platform"),
                "published_at": item.get("published_at"),
                "read_time": item.get("read_time"),
                "lang": item.get("lang", "en"),
            },
        }
