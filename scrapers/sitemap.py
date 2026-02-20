import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Any
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
    """

    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []

        single_entity = self.config.get("single-entity", False)
        settings = self.config.get("connector-setup", {})
        
        # Single entity mode: treat whole site as one entity
        if single_entity:
            log.info(f"Single-entity mode: fetching frontpage from sitemap domain")
            return self._run_single_entity_mode(url, settings, force)
        
        # Multi-entity mode: each page is a separate entity
        return self._run_multi_entity_mode(url, settings, force)
    
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
