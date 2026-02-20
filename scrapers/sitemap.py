import logging
import xml.etree.ElementTree as ET
import yaml
from typing import List, Dict, Any
from pathlib import Path
from .base import BaseScraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse

log = logging.getLogger("mcp.scrapers.sitemap")

class SitemapScraper(BaseScraper):
    """
    Scraper that parses sitemap.xml.
    
    Modes:
      - single_entity=True: Treats entire site as single entity (uses frontpage only)
      - single_entity=False: Treats each page as separate entity (default)
    
    Cache File Workflow:
      - If cache-file exists: Load from cache, skip fetching
      - If LLM fields empty + LLM enabled: Flag for LLM reprocessing
      - If cache-file missing: Fetch and save to cache file
    """

    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []

        single_entity = self.config.get("single-entity", False)
        settings = self.config.get("connector-setup", {})
        cache_file = self.config.get("cache-file")
        
        # Check if cache file exists and should be used
        if cache_file and not force:
            cached_entities = self._load_from_cache(cache_file)
            if cached_entities is not None:
                log.info(f"Loaded {len(cached_entities)} entities from cache file")
                return cached_entities
        
        # Single entity mode: treat whole site as one entity
        if single_entity:
            log.info(f"Single-entity mode: fetching frontpage from sitemap domain")
            entities = self._run_single_entity_mode(url, settings, force)
        else:
            # Multi-entity mode: each page is a separate entity
            entities = self._run_multi_entity_mode(url, settings, force)
        
        # Save to cache file if specified
        if cache_file and entities:
            self._save_to_cache(cache_file, entities)
        
        return entities
    
    def _run_single_entity_mode(self, sitemap_url: str, settings: Dict[str, Any], force: bool) -> List[Dict[str, Any]]:
        """Treat the whole site as a single entity using frontpage content."""
        # Extract base URL from sitemap URL
        parsed = urlparse(sitemap_url)
        frontpage_url = f"{parsed.scheme}://{parsed.netloc}/"
        
        # Check if we should fetch
        if not self.should_fetch(frontpage_url, force):
            log.info(f"Skipping {frontpage_url} (already cached)")
            return []
        
        log.info(f"Fetching frontpage: {frontpage_url}")
        
        html = self._fetch_url(frontpage_url)
        if not html:
            log.error(f"Failed to fetch frontpage: {frontpage_url}")
            return []
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Extract title
        title_sel = settings.get("post-title-selector", "title")
        title_el = soup.select_one(title_sel)
        title = title_el.get_text(strip=True) if title_el else parsed.netloc
        
        # Extract description
        content_sel = settings.get("post-content-selector", "body")
        content_el = soup.select_one(content_sel)
        description = content_el.get_text(separator=" ", strip=True)[:5000] if content_el else ""
        
        # Meta description overlay
        desc_sel = settings.get("post-description-selector", 'meta[name="description"]')
        meta = soup.select_one(desc_sel)
        if meta and meta.get("content"):
            description = meta.get("content")
        
        # Check sitemap for page count (metadata)
        sitemap_urls = self._fetch_sitemap(sitemap_url)
        page_count = len(sitemap_urls)
        
        return [{
            "flavor": "oeuvre",
            "category": self.config.get("sub_type_override", "website"),
            "title": title,
            "url": frontpage_url,
            "source": self.name,
            "source_url": frontpage_url,
            "description": description,
            "ext": {
                "platform": self.name,
                "sitemap_url": sitemap_url,
                "page_count": page_count,
                "single_entity_mode": True
            }
        }]
    
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
    
    def _load_from_cache(self, cache_file: str) -> List[Dict[str, Any]]:
        """
        Load entities from cache file.
        
        Returns None if cache doesn't exist or is invalid.
        Checks if LLM reprocessing is needed based on empty classification fields.
        """
        if cache_file.startswith("file://"):
            file_path = cache_file[7:]
        else:
            file_path = cache_file
        
        cache_path = Path(file_path)
        if not cache_path.exists():
            log.debug(f"Cache file not found: {cache_path}")
            return None
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            log.error(f"Failed to parse cache file {cache_path}: {e}")
            return None
        
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
            
            results.append(entity)
        
        log.info(f"Loaded {len(results)} entities from cache: {cache_path.name}")
        return results
    
    def _save_to_cache(self, cache_file: str, entities: List[Dict[str, Any]]):
        """Save entities to cache file in YAML format."""
        if cache_file.startswith("file://"):
            file_path = cache_file[7:]
        else:
            file_path = cache_file
        
        cache_path = Path(file_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            data = {"entities": entities}
            with open(cache_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            log.info(f"Saved {len(entities)} entities to cache: {cache_path}")
        except Exception as e:
            log.error(f"Failed to save cache file {cache_path}: {e}")
