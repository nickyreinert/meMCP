import abc
import yaml
import logging
import sqlite3
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup

from db.models import get_db, DB_PATH

log = logging.getLogger("mcp.scrapers")

class BaseScraper(abc.ABC):
    def __init__(self, name: str, config: Dict[str, Any], db_path: Path = DB_PATH, llm=None):
        self.name = name
        self.config = config
        self.db_path = db_path
        self.llm = llm  # Optional LLM enricher for PDF parsing etc.
        self.enabled = config.get("enabled", False)
        self.limit = config.get("limit", 0)
        self.yaml_cache_path: Optional[Path] = None  # Subclasses can set this

    def should_fetch(self, url: str, force: bool = False) -> bool:
        """Check if URL should be fetched based on cache age and entity existence."""
        if force:
            return True
        conn = get_db(self.db_path)
        try:
            # Check if we have an entity with this source_url (strongest signal for "processed")
            try:
                cursor = conn.execute("SELECT 1 FROM entities WHERE source_url=?", (url,))
                if cursor.fetchone():
                    return False  # Already in database as entity
            except sqlite3.OperationalError:
                # Table doesn't exist yet, proceed with fetch
                pass

            # Check cache age against TTL
            cache_ttl_hours = self.config.get("cache_ttl_hours", 24)
            try:
                cursor = conn.execute(
                    """SELECT scraped_at FROM scrape_cache
                       WHERE url=?
                       AND datetime(scraped_at) > datetime('now', '-' || ? || ' hours')""",
                    (url, cache_ttl_hours)
                )
                if cursor.fetchone():
                    return False  # Cache is fresh
            except sqlite3.OperationalError:
                # Table doesn't exist yet, proceed with fetch
                pass

            return True  # Needs fetching
        finally:
            conn.close()

    @abc.abstractmethod
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        """Run the scraper and return a list of entity dictionaries."""
        pass
        
    def _fetch_url(self, url: str) -> Optional[str]:
        """Helper to fetch URL with simple caching."""
        # Check cache first
        conn = get_db(self.db_path)
        try:
            try:
                row = conn.execute("SELECT content FROM scrape_cache WHERE url=?", (url,)).fetchone()
                if row and row["content"] and len(row["content"]) > 100:  # Sanity check
                    log.debug(f"Cache hit: {url}")
                    return row["content"]
            except sqlite3.OperationalError:
                # Table doesn't exist yet, proceed with fetch
                pass
        finally:
            conn.close()

        # Standard requests fetch
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Personal-MCP-Scraper/1.0"})
            resp.raise_for_status()
            content = resp.text
            self._save_to_cache(url, content, resp.status_code)
            log.info(f"Fetched {resp.status_code}: {url}")
            return content
        except Exception as e:
            log.error(f"Failed to fetch {url}: {e}")
            return None

    def _save_to_cache(self, url: str, content: str, status_code: int):
        """Save fetched content to cache."""
        conn = get_db(self.db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO scrape_cache (url, content, scraped_at, status_code) VALUES (?, ?, datetime('now'), ?)",
                (url, content, status_code)
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            log.debug(f"Could not save to cache (table may not exist yet): {e}")
        finally:
            conn.close()

class ScraperFactory:
    @staticmethod
    def create(name: str, config: Dict[str, Any], db_path: Path = DB_PATH, llm=None) -> Optional[BaseScraper]:
        if not config.get("enabled"):
            return None

        connector = config.get("connector")

        if connector == "github_api":
            from .github import GithubScraper
            return GithubScraper(name, config, db_path, llm=llm)
        elif connector == "rss":
            from .rss import RSSScraper
            return RSSScraper(name, config, db_path, llm=llm)
        elif connector == "manual":
            from .manual import ManualScraper
            return ManualScraper(name, config, db_path, llm=llm)
        elif connector == "sitemap":
            from .sitemap import SitemapScraper
            return SitemapScraper(name, config, db_path, llm=llm)
        elif connector == "html":
            from .html import HTMLScraper
            return HTMLScraper(name, config, db_path, llm=llm)
        elif connector == "medium_raw":
            from .medium_raw import MediumRawScraper
            return MediumRawScraper(name, config, db_path, llm=llm)
        elif connector == "linkedin_pdf":
            from .linkedin_pdf_scraper import LinkedInPDFScraper
            return LinkedInPDFScraper(name, config, db_path, llm=llm)
        elif connector == "identity":
            from .identity import IdentityScraper
            return IdentityScraper(name, config, db_path, llm=llm)
        else:
            log.warning(f"Unknown connector '{connector}' for source '{name}'")
            return None
