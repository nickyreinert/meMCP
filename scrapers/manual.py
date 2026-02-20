import logging
import requests
from typing import List, Dict, Any
from .base import BaseScraper
from bs4 import BeautifulSoup
import datetime

log = logging.getLogger("mcp.scrapers.manual")

class ManualScraper(BaseScraper):
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []

        settings = self.config.get("connector-setup", {})
        post_selector = settings.get("post_url_selector", "a")

        # 1. Fetch the main list page
        log.info(f"Fetching {url}")
        html = self._fetch_url(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")

        # 2. Extract links
        links = set()
        all_links = soup.select(post_selector)
        log.info(f"Found {len(all_links)} elements matching selector '{post_selector}'")
        for a in soup.select(post_selector):
            href = a.get("href")
            if href:
                # Handle relative URLs if needed, though usually medium ones are ok or absolute
                if href.startswith("/"):
                    # construct absolute url
                     # parsing the base url to find domain
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    href = base + href

                # Medium specific cleanup (remove query params for canonical checking?)
                # user said "href starting @nickyreinert/", so we trust the selector.

                # Check if we should process this link
                if self.should_fetch(href, force):
                     links.add(href)

        results = []
        count = 0
        limit = self.config.get("limit", 0)
        errors = 0

        for link in links:
            if limit and count >= limit:
                break

            try:
                item = self._process_page(link, settings)
                if item:
                    results.append(item)
                    count += 1
                    errors = 0  # Reset error counter on success
            except Exception as e:
                log.error(f"Failed to process {link}: {e}")
                errors += 1
                if errors >= 10:
                    log.error("Too many errors, stopping scraper")
                    break

        return results

    def _process_page(self, url: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        log.info(f"Processing manual page: {url}")
        html = self._fetch_url(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        title_sel = settings.get("post-title-selector", "title")
        content_sel = settings.get("post-content-selector", "body")
        date_sel = settings.get("post-published-date-selector")
        desc_sel = settings.get("post-description-selector")

        title_el = soup.select_one(title_sel)
        title = title_el.get_text(strip=True) if title_el else "Untitled"

        # Content (maybe getting text is enough for now)
        content_el = soup.select_one(content_sel)
        # We might want to keep HTML or just text. For LLM summarization, text is usually better and smaller.
        description = content_el.get_text(strip=True)[:5000] if content_el else "" # limit size

        # Meta description overlay
        if desc_sel:
            # Check for generic meta tag structure
            meta = soup.select_one(desc_sel)
            if meta and meta.get("content"):
                # Prefer meta description for the short description field
                description = meta.get("content")

        published_at = None
        if date_sel:
            meta_date = soup.select_one(date_sel)
            if meta_date and meta_date.get("content"):
                published_at = meta_date.get("content")

        return {
            "flavor": "oeuvre",
            "category": item.get("category", "article"),
            "title": title,
            "url": url,
            "source": self.name,
            "source_url": url,
            "description": description,
            "published_at": published_at,
             # We might want to store the full content somewhere if needed,
             # but 'description' is the main field in the entity model
             # The seeder usually enriches descriptions.
            "ext": {
                "platform": self.name,
                "read_time": 5, # Placeholder or calc from word count
                "published_at": published_at
            }
        }
