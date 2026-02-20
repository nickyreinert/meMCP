"""
scrapers/manual.py — Manual YAML Data Loader
=============================================
Loads manually curated entities from YAML files.
Useful for static information that doesn't change often or data that cannot be scraped.

Purpose:
- Load pre-structured entities from YAML files
- Support both 'stages' and 'oeuvre' flavors
- Check file modification time against cache_ttl_hours
"""

import logging
import yaml
from typing import List, Dict, Any
from pathlib import Path
from .base import BaseScraper
from datetime import datetime, timezone

log = logging.getLogger("mcp.scrapers.manual")


class ManualScraper(BaseScraper):
    """
    Load entities from manually curated YAML files.
    
    Expected YAML structure:
    ```yaml
    entities:
      job1:
        flavor: stages
        category: job
        title: "Software Engineer at XYZ"
        description: "..."
        company: "XYZ"
        start_date: "2020-01-01"
        end_date: "2022-12-31"
        skills: ["Python", "React"]
        technologies: ["Django", "AWS"]
        tags: ["Full-stack"]
      
      project1:
        flavor: oeuvre
        category: coding
        title: "Personal Portfolio Website"
        description: "..."
        url: "https://example.com"
        date: "2021-06-15"
        skills: ["Web Development"]
        technologies: ["Next.js"]
        tags: ["Portfolio"]
    ```
    """
    
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Load entities from YAML file.
        
        Args:
            force: If True, re-process even if cached
        
        Returns:
            List of entity dictionaries
        """
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []
        
        # Handle file:// URLs
        if url.startswith("file://"):
            file_path = url[7:]  # Remove 'file://' prefix
        else:
            log.error(f"Only file:// URLs supported for manual connector, got: {url}")
            return []
        
        yaml_path = Path(file_path)
        if not yaml_path.exists():
            log.error(f"File not found: {yaml_path}")
            return []
        
        # Check if file needs reprocessing based on modification time and cache TTL
        cache_ttl_hours = self.config.get("cache_ttl_hours", 168)  # Default: 7 days
        
        if not force:
            file_mtime = datetime.fromtimestamp(yaml_path.stat().st_mtime, tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - file_mtime).total_seconds() / 3600
            
            if age_hours > cache_ttl_hours:
                log.info(f"File {yaml_path.name} was modified {age_hours:.1f}h ago (TTL: {cache_ttl_hours}h)")
                # Note: For manual files, we still load them but mark for potential LLM reprocessing
                # The should_fetch() check will determine if entities need LLM enrichment
            else:
                log.debug(f"File {yaml_path.name} is fresh ({age_hours:.1f}h old)")
        
        log.info(f"Reading manual YAML from {yaml_path}")
        
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            log.error(f"Failed to parse YAML file {yaml_path}: {e}")
            return []
        
        if not data or "entities" not in data:
            log.error(f"Invalid YAML structure: missing 'entities' key in {yaml_path}")
            return []
        
        entities_dict = data.get("entities", {})
        results = []
        
        for entity_key, entity_data in entities_dict.items():
            if not isinstance(entity_data, dict):
                log.warning(f"Skipping invalid entity '{entity_key}': not a dictionary")
                continue
            
            # Extract base fields
            flavor = entity_data.get("flavor")
            category = entity_data.get("category")
            title = entity_data.get("title")
            
            if not flavor or not category or not title:
                log.warning(f"Skipping entity '{entity_key}': missing required fields (flavor, category, title)")
                continue
            
            # Build entity dict
            entity = {
                "flavor": flavor,
                "category": category,
                "title": title,
                "description": entity_data.get("description", ""),
                "source": self.name,
                "source_url": entity_data.get("url", f"file://{yaml_path}#{entity_key}"),
            }
            
            # Add optional fields based on flavor
            if flavor == "stages":
                # Stages-specific fields
                if "start_date" in entity_data:
                    entity["start_date"] = entity_data["start_date"]
                if "end_date" in entity_data:
                    entity["end_date"] = entity_data["end_date"]
                if "company" in entity_data:
                    entity["company"] = entity_data["company"]
            
            elif flavor == "oeuvre":
                # Oeuvre-specific fields
                if "url" in entity_data:
                    entity["url"] = entity_data["url"]
                if "date" in entity_data:
                    entity["published_at"] = entity_data["date"]
            
            # Add classification fields (common to both flavors)
            if "skills" in entity_data:
                entity["skills"] = entity_data["skills"]
            if "technologies" in entity_data:
                entity["technologies"] = entity_data["technologies"]
            if "tags" in entity_data:
                entity["tags"] = entity_data["tags"]
            
            # Store additional metadata in ext
            entity["ext"] = {
                "platform": "manual",
                "entity_key": entity_key,
            }
            
            # Add any extra fields to ext
            extra_fields = {"location", "read_time", "language", "authors"}
            for field in extra_fields:
                if field in entity_data:
                    entity["ext"][field] = entity_data[field]
            
            results.append(entity)
            log.debug(f"Loaded entity: {title} ({flavor}/{category})")
        
        log.info(f"Loaded {len(results)} entities from {yaml_path.name}")
        return results
