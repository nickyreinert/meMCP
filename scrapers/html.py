"""
scrapers/html.py — Single HTML Page Scraper
=============================================
Scrapes a single HTML page and creates one entity.

YAML Cache Workflow:
- If cache-file exists: Load from cache, skip fetching
- If LLM fields empty + LLM enabled: Flag for LLM reprocessing
- If cache-file missing: Fetch and save to cache file
- Manual editing: Edit cache file to refine content (preserved across runs)

For sitemap.xml parsing with multiple pages, use 'sitemap' connector instead.

Depends on:
- BeautifulSoup4 for HTML parsing
- base.BaseScraper for common functionality
- yaml_sync module for bidirectional YAML ↔ DB synchronization
"""

import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from .base import BaseScraper
from .yaml_sync import (
    load_yaml_with_metadata,
    save_yaml_atomic
)
from bs4 import BeautifulSoup
from urllib.parse import urlparse

log = logging.getLogger("mcp.scrapers.html")


class HTMLScraper(BaseScraper):
    """
    Scrapes a single HTML page and creates one entity (single-entity only).
    
    Purpose:
    - Scrape landing page, project page, portfolio site
    - Extract title, description, content with CSS selectors
    - Create YAML cache for manual editing
    
    For scraping multiple pages from sitemap.xml, use 'sitemap' connector instead.
    """

    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch single HTML page and create entity.
        
        Args:
            force: If True, re-fetch even if cache exists
        
        Returns:
            List with single entity dictionary
        """
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []

        settings = self.config.get("connector-setup", {})
        cache_file = self.config.get("cache-file")
        
        # Set yaml_cache_path for ingest.py integration
        if cache_file:
            if cache_file.startswith("file://"):
                self.yaml_cache_path = Path(cache_file[7:])
            else:
                self.yaml_cache_path = Path(cache_file)
        
        # Check if cache file exists and should be used
        if cache_file and not force:
            cached_entities = self._load_from_cache(cache_file)
            if cached_entities is not None:
                log.info(f"Loaded {len(cached_entities)} entity from cache file")
                return cached_entities
        
        # Fetch and parse single page
        entity = self._fetch_page(url, settings, force)
        
        if not entity:
            return []
        
        entities = [entity]
        
        # Save to cache file if specified
        if cache_file and entities:
            self._save_to_cache(cache_file, entities)
            log.info(f"✓ Cache file updated: {self.yaml_cache_path}")
        
        return entities
    
    def _fetch_page(
        self,
        url: str,
        settings: Dict[str, Any],
        force: bool
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch and parse single HTML page.
        
        Args:
            url: Page URL to fetch
            settings: CSS selectors from connector-setup
            force: Force re-fetch ignoring cache
        
        Returns:
            Entity dictionary or None if fetch failed
        """
        # Check if we should fetch
        if not self.should_fetch(url, force):
            log.info(f"Skipping {url} (already cached)")
            return None
        
        log.info(f"Fetching HTML page: {url}")
        
        html = self._fetch_url(url)
        if not html:
            log.error(f"Failed to fetch page: {url}")
            return None
        
        soup = BeautifulSoup(html, "html.parser")
        parsed_url = urlparse(url)
        
        # Extract title
        title_sel = settings.get("post-title-selector", "title")
        title_el = soup.select_one(title_sel)
        title = title_el.get_text(strip=True) if title_el else parsed_url.netloc
        
        # Extract description/content
        content_sel = settings.get("post-content-selector", "body")
        content_el = soup.select_one(content_sel)
        description = content_el.get_text(separator=" ", strip=True)[:5000] if content_el else ""
        
        # Meta description overlay (preferred if available)
        desc_sel = settings.get("post-description-selector", 'meta[name="description"]')
        meta = soup.select_one(desc_sel)
        if meta and meta.get("content"):
            description = meta.get("content")
        
        return {
            "flavor": "oeuvre",
            "category": self.config.get("sub_type_override", "website"),
            "title": title,
            "url": url,
            "source": self.name,
            "source_url": url,
            "description": description,
            "ext": {
                "platform": self.name,
                "domain": parsed_url.netloc
            }
        }
    
    def _load_from_cache(
        self,
        cache_file: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Load entity from YAML cache using yaml_sync module.
        
        Args:
            cache_file: Path to cache file (file:// URL or path string)
        
        Returns:
            List with single entity or None if cache invalid/missing
        """
        if cache_file.startswith("file://"):
            file_path = cache_file[7:]
        else:
            file_path = cache_file
        
        cache_path = Path(file_path)
        if not cache_path.exists():
            log.debug(f"Cache file not found: {cache_path}")
            return None
        
        # Load with metadata tracking
        metadata, data = load_yaml_with_metadata(cache_path)
        
        if not data or "entity" not in data:
            log.warning(f"Invalid cache structure: missing 'entity' key in {cache_path}")
            return None
        
        entity = data.get("entity")
        if not isinstance(entity, dict):
            log.warning(f"Invalid cache structure: 'entity' must be a dict in {cache_path}")
            return None
        
        llm_enabled = self.config.get("llm-processing", False)
        
        # Check if LLM fields are empty and LLM is enabled
        if llm_enabled:
            has_tags = entity.get("tags") and len(entity.get("tags", [])) > 0
            has_skills = entity.get("skills") and len(entity.get("skills", [])) > 0
            has_tech = entity.get("technologies") and len(entity.get("technologies", [])) > 0
            
            if not (has_tags or has_skills or has_tech):
                # Mark for LLM reprocessing
                entity["needs_llm_enrichment"] = True
                log.debug(f"Entity '{entity.get('title')}' needs LLM enrichment")
            else:
                # Has LLM enrichment
                entity["llm_enriched"] = 1
                entity["llm_model"] = entity.get("llm_model")
                entity["llm_enriched_at"] = entity.get("llm_enriched_at")
        
        # Include entity_id if present (for DB sync)
        if "entity_id" in entity:
            entity["id"] = entity["entity_id"]
        
        last_synced = metadata.get('last_synced', 'never') if metadata else 'never'
        log.info(f"Loaded entity from cache: {cache_path.name} (last_synced: {last_synced})")
        return [entity]
    
    def _save_to_cache(self, cache_file: str, entities: List[Dict[str, Any]]):
        """
        Save single entity to YAML cache using yaml_sync module (atomic write).
        
        Args:
            cache_file: Path to cache file (file:// URL or path string)
            entities: List with single entity dictionary
        """
        if cache_file.startswith("file://"):
            file_path = cache_file[7:]
        else:
            file_path = cache_file
        
        cache_path = Path(file_path)
        
        if len(entities) != 1:
            log.warning(f"HTML scraper expected 1 entity, got {len(entities)}")
            if not entities:
                return
        
        entity = entities[0]
        
        # Convert entity to clean YAML format
        yaml_entity = {
            "title": entity.get("title"),
            "url": entity.get("url"),
            "description": entity.get("description", ""),
        }
        
        # Include entity_id if present (for DB sync)
        if "id" in entity:
            yaml_entity["entity_id"] = entity["id"]
        
        # Include LLM fields if present
        if entity.get("technologies"):
            yaml_entity["technologies"] = entity["technologies"]
        if entity.get("skills"):
            yaml_entity["skills"] = entity["skills"]
        if entity.get("tags"):
            yaml_entity["tags"] = entity["tags"]
        if entity.get("llm_enriched"):
            yaml_entity["llm_enriched"] = True
            yaml_entity["llm_model"] = entity.get("llm_model")
            yaml_entity["llm_enriched_at"] = entity.get("llm_enriched_at")
        
        # Include ext metadata
        if entity.get("ext"):
            yaml_entity["ext"] = entity["ext"]
        
        yaml_data = {"entity": yaml_entity}
        
        # Use atomic save from yaml_sync
        save_yaml_atomic(cache_path, yaml_data, self.name)
        log.info(f"Saved entity to cache: {cache_path}")
