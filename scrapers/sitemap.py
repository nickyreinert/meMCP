"""
scrapers/sitemap.py — Sitemap XML Parser
==========================================
Parses sitemap.xml files to extract URLs and scrape page content.

Modes:
- single_entity=True: Treats entire site as single entity (uses frontpage only)
- single_entity=False: Treats each page as separate entity (default)

YAML Cache Workflow:
- If cache-file exists: Load from cache, skip fetching
- If LLM fields empty + LLM enabled: Flag for LLM reprocessing
- If cache-file missing: Fetch and save to cache file
- Manual editing: Edit cache file to refine content (preserved across runs)

Depends on:
- BeautifulSoup4 for HTML parsing
- base.BaseScraper for common functionality
- yaml_sync module for bidirectional YAML ↔ DB synchronization
"""

import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from pathlib import Path
from .base import BaseScraper
from .yaml_sync import (
    load_yaml_with_metadata,
    save_yaml_atomic,
    needs_reload
)
from bs4 import BeautifulSoup
from urllib.parse import urlparse

log = logging.getLogger("mcp.scrapers.sitemap")

class SitemapScraper(BaseScraper):
    """
    Scraper that parses sitemap.xml and scrapes multiple pages.
    Each page in sitemap becomes a separate entity (multi-entity only).
    
    Cache File Workflow:
      - If cache-file exists: Load from cache, skip fetching
      - If LLM fields empty + LLM enabled: Flag for LLM reprocessing
      - If cache-file missing: Fetch and save to cache file
    
    For single-page scraping, use the 'html' connector instead.
    """

    def run(self, force: bool = False) -> List[Dict[str, Any]]:
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
                log.info(f"Loaded {len(cached_entities)} entities from cache file")
                return cached_entities
        
        # Parse sitemap and scrape all pages (multi-entity mode)
        entities = self._run_multi_entity_mode(url, settings, force)
        
        # Save to cache file if specified
        if cache_file and entities:
            self._save_to_cache(cache_file, entities)
            log.info(f"✓ Cache file updated: {self.yaml_cache_path}")
        
        return entities
    
    def _run_multi_entity_mode(self, url: str, settings: Dict[str, Any], force: bool) -> List[Dict[str, Any]]:
        """Each page in sitemap becomes a separate entity."""
        # 1. Fetch and parse sitemap.xml
        log.info(f"Multi-entity mode: fetching sitemap from {url}")
        sitemap_urls = self._fetch_sitemap(url)
        if not sitemap_urls:
            log.warning(f"No URLs found in sitemap {url}")
            return []

        log.info(f"Found {len(sitemap_urls)} URLs in sitemap")

        # 2. Filter by should_fetch and limit
        urls_to_process = []
        for sitemap_url in sitemap_urls:
            if self.should_fetch(sitemap_url, force):
                urls_to_process.append(sitemap_url)

        limit = self.config.get("limit", 0)
        if limit and limit > 0:
            urls_to_process = urls_to_process[:limit]

        log.info(f"Processing {len(urls_to_process)} URLs from sitemap")

        # 3. Process each URL
        results = []
        errors = 0

        for url_to_scrape in urls_to_process:
            try:
                item = self._process_page(url_to_scrape, settings)
                if item:
                    results.append(item)
                    errors = 0  # Reset error counter on success
            except Exception as e:
                log.error(f"Failed to process {url_to_scrape}: {e}")
                errors += 1
                if errors >= 10:
                    log.error("Too many errors, stopping scraper")
                    break

        log.info(f"Scraped {len(results)} items from sitemap")
        return results

    def _fetch_sitemap(self, url: str) -> List[str]:
        """Fetch and parse sitemap.xml to extract URLs."""
        content = self._fetch_url(url)
        if not content:
            return []

        try:
            root = ET.fromstring(content)

            # Handle sitemap.org namespace
            ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            urls = [loc.text for loc in root.findall('.//ns:loc', ns) if loc.text]

            # Fallback for malformed sitemaps (missing namespace)
            if not urls:
                urls = [loc.text for loc in root.findall('.//loc') if loc.text]

            log.info(f"Extracted {len(urls)} URLs from sitemap")
            return urls
        except ET.ParseError as e:
            log.error(f"Failed to parse sitemap XML from {url}: {e}")
            return []
        except Exception as e:
            log.error(f"Error processing sitemap {url}: {e}")
            return []

    def _process_page(self, url: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single page URL using configured selectors."""
        log.info(f"Processing sitemap page: {url}")

        html = self._fetch_url(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # Extract canonical URL
        canonical_url = None
        canonical_tag = soup.find('link', {'rel': 'canonical'})
        if canonical_tag and canonical_tag.get('href'):
            canonical_url = canonical_tag['href']
            if canonical_url != url:
                log.debug(f"Found canonical URL: {canonical_url}")

        # Extract fields using configured selectors
        title_sel = settings.get("post-title-selector", "title")
        content_sel = settings.get("post-content-selector", "body")
        date_sel = settings.get("post-published-date-selector")
        desc_sel = settings.get("post-description-selector")

        title_el = soup.select_one(title_sel)
        title = title_el.get_text(strip=True) if title_el else "Untitled"

        # Extract content
        content_el = soup.select_one(content_sel)
        description = content_el.get_text(strip=True)[:5000] if content_el else ""

        # Meta description overlay
        if desc_sel:
            meta = soup.select_one(desc_sel)
            if meta and meta.get("content"):
                description = meta.get("content")

        # Published date
        published_at = None
        if date_sel:
            meta_date = soup.select_one(date_sel)
            if meta_date:
                # Try to get from 'content' attribute (meta tags)
                if meta_date.get("content"):
                    published_at = meta_date.get("content")
                # Try to get from 'datetime' attribute (time tags)
                elif meta_date.get("datetime"):
                    published_at = meta_date.get("datetime")
                # Otherwise get text content
                else:
                    published_at = meta_date.get_text(strip=True)

        return {
            "flavor": "oeuvre",
            "category": self.config.get("sub_type_override", "article"),
            "title": title,
            "url": url,
            "canonical_url": canonical_url if canonical_url and canonical_url != url else None,
            "source": self.name,
            "source_url": url,
            "description": description,
            "published_at": published_at,
            "ext": {
                "platform": self.name,
                "read_time": 5,  # Placeholder
                "published_at": published_at
            }
        }
    
    def _load_from_cache(
        self,
        cache_file: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Load entities from YAML cache using yaml_sync module.
        
        Returns None if cache doesn't exist or is invalid.
        Checks if LLM reprocessing is needed based on empty classification fields.
        
        Args:
            cache_file: Path to cache file (file:// URL or path string)
        
        Returns:
            List of entity dictionaries or None if cache invalid/missing
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
        
        if not data or "entities" not in data:
            log.warning(f"Invalid cache structure: missing 'entities' key in {cache_path}")
            return None
        
        entities_list = data.get("entities", [])
        if not isinstance(entities_list, list):
            log.warning(f"Invalid cache structure: 'entities' must be a list in {cache_path}")
            return None
        
        llm_enabled = self.config.get("llm-processing", False)
        results = []
        
        for entity in entities_list:
            if not isinstance(entity, dict):
                continue
            
            # Check if LLM fields are empty and LLM is enabled
            if llm_enabled:
                has_tags = entity.get("tags") and len(entity.get("tags", [])) > 0
                has_skills = entity.get("skills") and len(entity.get("skills", [])) > 0
                has_tech = entity.get("technologies") and len(entity.get("technologies", [])) > 0
                
                if not (has_tags or has_skills or has_tech):
                    # Mark for LLM reprocessing by setting flag
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
            
            results.append(entity)
        
        last_synced = metadata.get('last_synced', 'never') if metadata else 'never'
        log.info(f"Loaded {len(results)} entities from cache: {cache_path.name} (last_synced: {last_synced})")
        return results
    
    def _save_to_cache(self, cache_file: str, entities: List[Dict[str, Any]]):
        """
        Save entities to YAML cache using yaml_sync module (atomic write).
        
        Args:
            cache_file: Path to cache file (file:// URL or path string)
            entities: List of entity dictionaries to save
        """
        if cache_file.startswith("file://"):
            file_path = cache_file[7:]
        else:
            file_path = cache_file
        
        cache_path = Path(file_path)
        
        # Convert entities to clean YAML format
        yaml_entities = []
        for entity in entities:
            yaml_entity = {
                "title": entity.get("title"),
                "url": entity.get("url"),
                "description": entity.get("description", ""),
                "published_at": entity.get("published_at"),
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
            
            yaml_entities.append(yaml_entity)
        
        yaml_data = {"entities": yaml_entities}
        
        # Use atomic save from yaml_sync
        save_yaml_atomic(cache_path, yaml_data, self.name)
        log.info(f"Saved {len(yaml_entities)} entities to cache: {cache_path}")
