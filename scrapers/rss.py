import logging
import requests
import xml.etree.ElementTree as ET
from typing import List, Dict, Any
from .base import BaseScraper
import datetime
import calendar
import time

log = logging.getLogger("mcp.scrapers.rss")

class RSSScraper(BaseScraper):
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []
            
        log.info(f"Downloading RSS feed {url}")
        content = self._fetch_url(url)
        if not content:
            return []
            
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            log.error(f"Failed to parse XML from {url}")
            return []
            
        # Handle RSS (channel/item) vs Atom (feed/entry)
        items = root.findall(".//item")
        if not items:
            items = root.findall(".//entry") # Atom
            
        results = []
        count = 0
        limit = self.config.get("limit", 0)
        
        for entry in items:
            if limit and count >= limit:
                break
                
            link = entry.findtext("link") or entry.find("link").get("href")
            title = entry.findtext("title")
            desc = entry.findtext("description") or entry.findtext("summary") or entry.findtext("content")
            
            if not self.should_fetch(link, force):
                continue
                
            pub = entry.findtext("pubDate") or entry.findtext("published")
            published_at = None
            if pub:
                # Basic parsing, might need more robust datetime parser
                # Usually standard RFC822 for RSS.
                try:
                    # python email.utils.parsedate_to_datetime is good for this
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub)
                    published_at = dt.isoformat()
                except Exception:
                    pass
            
            item = {
                "flavor": "oeuvre",
                "category": self.config.get("sub_type_override", "article"),
                "sub_type": self.config.get("sub_type_override", "blog_post"),
                "title": title,
                "url": link,
                "source": self.name,
                "source_url": link,
                "description": desc[:5000] if desc else "", 
                "published_at": published_at,
                "ext": {
                    "platform": self.name,
                    "published_at": published_at
                }
            }
            results.append(item)
            count += 1
            
        return results
