"""
scrapers/medium_raw.py — Medium Raw HTML Parser
================================================
Parses a raw HTML dump of Medium profile page to extract all published articles.
Extracts article links, titles, and publication dates from DOM structure.

Depends on:
- BeautifulSoup4 for HTML parsing
- base.BaseScraper for common functionality
"""

import logging
import re
from typing import List, Dict, Any
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from .base import BaseScraper

log = logging.getLogger("mcp.scrapers.medium_raw")


class MediumRawScraper(BaseScraper):
    """
    Parses a raw HTML dump of a Medium profile page.
    Extracts all published articles from the DOM structure.
    
    Purpose:
    - Bypass Medium's Cloudflare protection by working with saved HTML
    - Extract complete article history (not limited to RSS feed)
    
    Input: HTML file path (file:// URL)
    Output: List of article entities with title, URL, date
    """
    
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Parse the raw HTML file and extract all Medium articles.
        
        Args:
            force: If True, re-process even if cached
        
        Returns:
            List of article dictionaries
        """
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []
        
        # Handle file:// URLs
        if url.startswith("file://"):
            file_path = url[7:]  # Remove 'file://' prefix
            html_path = Path(file_path)
            
            if not html_path.exists():
                log.error(f"File not found: {html_path}")
                return []
            
            log.info(f"Reading Medium HTML from {html_path}")
            try:
                content = html_path.read_text(encoding='utf-8')
            except Exception as e:
                log.error(f"Failed to read file {html_path}: {e}")
                return []
        else:
            log.error(f"Only file:// URLs supported for medium_raw connector, got: {url}")
            return []
        
        # Parse HTML
        try:
            soup = BeautifulSoup(content, 'html.parser')
        except Exception as e:
            log.error(f"Failed to parse HTML: {e}")
            return []
        
        # Find all article links - Medium uses href="/@username/article-slug"
        articles = []
        limit = self.config.get("limit", 0)
        count = 0
        
        # Find all links to articles (starting with /@nickyreinert/ or similar)
        article_links = soup.find_all('a', href=re.compile(r'^/@[^/]+/[^?]+'))
        
        # Track seen URLs to avoid duplicates
        seen_urls = set()
        
        for link in article_links:
            if limit and count >= limit:
                break
            
            href = link.get('href', '')
            
            # Clean URL - remove query params
            if '?' in href:
                href = href.split('?')[0]
            
            # Convert to full URL
            article_url = f"https://medium.com{href}"
            
            # Skip duplicates
            if article_url in seen_urls:
                continue
            
            # Check if we should fetch this article
            if not self.should_fetch(article_url, force):
                log.debug(f"Skipping already processed: {article_url}")
                continue
            
            seen_urls.add(article_url)
            
            # Extract title and date - look for containing <td> element
            title = None
            published_at = None
            date_text = None
            
            # Find the parent <td> that contains the full article entry
            parent_td = link.find_parent('td')
            if parent_td:
                # Find title in h2 within this td
                h2 = parent_td.find('h2')
                if h2:
                    title = h2.get_text(strip=True)
                
                # Find published date - iterate through <p> tags
                for p_tag in parent_td.find_all('p'):
                    p_text = p_tag.get_text(strip=True)
                    if 'Published' in p_text:
                        # Extract date from "Published <span>Jan 31, 2024</span>" format
                        span = p_tag.find('span')
                        if span:
                            date_text = span.get_text(strip=True)
                            try:
                                # Parse date like "Jan 31, 2024", "Oct 18, 2024", or "Aug 1, 2025"
                                dt = datetime.strptime(date_text, "%b %d, %Y")
                                published_at = dt.isoformat()
                            except ValueError:
                                log.debug(f"Could not parse date: {date_text}")
                        break
            
            # Fallback: use aria-label or link text for title
            if not title:
                aria_label = link.find('div', attrs={'aria-label': True})
                if aria_label:
                    title = aria_label.get('aria-label')
                else:
                    title = link.get_text(strip=True) or self._extract_title_from_url(href)
            
            # Create article entity
            article = {
                "flavor": "oeuvre",
                "category": "article",
                "sub_type": self.config.get("sub_type_override", "article"),
                "title": title or "Untitled",
                "url": article_url,
                "source": self.name,
                "source_url": article_url,
                "description": "",  # No description available in listing
                "published_at": published_at,
                "ext": {
                    "platform": "medium",
                    "published_at": published_at,
                    "published_date_text": date_text
                }
            }
            
            articles.append(article)
            count += 1
            log.debug(f"Extracted article: {title} ({article_url})")
        
        log.info(f"Extracted {len(articles)} articles from Medium HTML dump")
        return articles
    
    def _extract_title_from_url(self, url: str) -> str:
        """
        Extract a readable title from the URL slug.
        
        Args:
            url: Article URL path
        
        Returns:
            Title extracted from slug
        """
        # Extract slug from /@username/article-slug-hash
        parts = url.split('/')
        if len(parts) >= 3:
            slug = parts[-1]
            # Remove hash suffix (everything after last -)
            if '-' in slug:
                slug_parts = slug.rsplit('-', 1)
                if len(slug_parts[1]) in [12, 16]:  # Hash lengths
                    slug = slug_parts[0]
            # Convert dashes to spaces and title case
            return slug.replace('-', ' ').title()
        return "Untitled Article"
