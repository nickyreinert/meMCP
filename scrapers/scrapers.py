"""
scrapers/scrapers.py — Source Scrapers
=======================================
Each scraper produces a list of raw dicts that get passed to the seeder,
which normalises them into Entity + Extension + Relation records.

Scrapers:
  GitHubScraper     — repo list pages (no auth) + optional README fetch
  MediumScraper     — Medium profile + RSS feed
  BlogScraper       — HTML blog index + RSS feed
  LinkedInParser    — LinkedIn YAML export (no scraping — they block bots)
"""

import re
import logging
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
import feedparser
from bs4 import BeautifulSoup

log = logging.getLogger("mcp.scrapers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-mcp-builder/2.0; "
        "+https://github.com/nickyreinert)"
    )
}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP CACHE
# ─────────────────────────────────────────────────────────────────────────────

class HttpCache:
    def __init__(self, cache_dir: Path, ttl_hours: int = 6):
        self.dir = cache_dir
        self.ttl = ttl_hours * 3600
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, url: str) -> Path:
        h = hashlib.md5(url.encode()).hexdigest()
        return self.dir / f"{h}.json"

    def get(self, url: str) -> Optional[str]:
        p = self._key(url)
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        if time.time() - data["ts"] > self.ttl:
            return None
        return data["content"]

    def set(self, url: str, content: str):
        self._key(url).write_text(
            json.dumps({"ts": time.time(), "content": content})
        )


def fetch(url: str, cache: HttpCache, timeout: int = 15) -> Optional[str]:
    if (cached := cache.get(url)):
        log.debug(f"cache hit: {url}")
        return cached
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        cache.set(url, r.text)
        log.info(f"fetched {r.status_code}: {url}")
        return r.text
    except Exception as e:
        log.warning(f"fetch failed {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GITHUB SCRAPER
# ─────────────────────────────────────────────────────────────────────────────

class GitHubScraper:
    """
    Scrapes public GitHub repository list pages (no auth required).
    Produces entity dicts of type='side_project'.
    Optionally fetches README for richer descriptions.
    """

    def __init__(self, username: str, cache: HttpCache, pages: int = 3):
        self.username = username
        self.cache    = cache
        self.pages    = pages

    def scrape(self) -> list[dict]:
        all_repos = []
        seen = set()

        for page in range(1, self.pages + 1):
            url = f"https://github.com/{self.username}?page={page}&tab=repositories"
            html = fetch(url, self.cache)
            if not html:
                break
            soup = BeautifulSoup(html, "html.parser")

            # Repo links: /{username}/{repo-name}  (no sub-paths)
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if not re.match(rf"^/{self.username}/[^/?#]+$", href):
                    continue
                name = href.split("/")[-1]
                if name in seen or name in (
                    "followers","following","stars","repositories","projects","packages"
                ):
                    continue
                seen.add(name)

                container = a.find_parent("li") or a.find_parent("div")
                description = stars = language = updated = license_ = ""
                forks = 0
                is_fork = False

                if container:
                    # Fork indicator
                    is_fork = bool(container.find("span", string=re.compile(r"Forked from", re.I)))

                    desc_el = (container.find("p", itemprop="description")
                               or container.find("p"))
                    if desc_el:
                        description = desc_el.get_text(strip=True)

                    lang_el = (container.find(itemprop="programmingLanguage")
                               or container.find("span", attrs={"itemprop": "programmingLanguage"}))
                    if lang_el:
                        language = lang_el.get_text(strip=True)

                    star_a = container.find("a", href=lambda h: h and "/stargazers" in (h or ""))
                    if star_a:
                        try:
                            stars = int(star_a.get_text(strip=True).replace(",", ""))
                        except ValueError:
                            pass

                    fork_a = container.find("a", href=lambda h: h and "/forks" in (h or ""))
                    if fork_a:
                        try:
                            forks = int(fork_a.get_text(strip=True).replace(",", ""))
                        except ValueError:
                            pass

                    time_el = container.find("relative-time") or container.find("time")
                    if time_el:
                        updated = (time_el.get("datetime") or "")[:10]

                all_repos.append({
                    "type":        "side_project",
                    "title":       name,
                    "description": description,
                    "url":         f"https://github.com/{self.username}/{name}",
                    "source":      "github",
                    "source_url":  url,
                    "updated_at":  updated,
                    "tags":        [t for t in [language] if t],
                    "is_fork":     is_fork,
                    "ext": {
                        "repo_url":  f"https://github.com/{self.username}/{name}",
                        "stars":     stars,
                        "forks":     forks,
                        "language":  language,
                        "status":    "active",
                    },
                })
                log.debug(f"  github repo: {name}")

        log.info(f"GitHub: {len(all_repos)} repos scraped")
        return all_repos

    def fetch_readme(self, repo_name: str, cache: HttpCache) -> Optional[str]:
        """Fetch raw README.md for a repo to use as richer description source."""
        url = f"https://raw.githubusercontent.com/{self.username}/{repo_name}/main/README.md"
        content = fetch(url, cache)
        if not content:
            # try master branch
            url = url.replace("/main/", "/master/")
            content = fetch(url, cache)
        if content:
            # Strip markdown headers + badges, return first meaningful paragraph
            lines = [l for l in content.split("\n")
                     if l.strip() and not l.startswith("#")
                     and not l.startswith("![") and not l.startswith("[![")]
            return " ".join(lines[:3])[:800]
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MEDIUM SCRAPER
# ─────────────────────────────────────────────────────────────────────────────

class MediumScraper:
    """
    Scrapes Medium via RSS feed (more reliable than HTML scraping).
    Falls back to HTML profile page if RSS unavailable.
    Produces entity dicts of type='literature'.
    """

    def __init__(self, username: str, cache: HttpCache):
        self.username = username
        self.rss_url  = f"https://medium.com/feed/@{username}"
        self.profile_url = f"https://{username}.medium.com/"
        self.cache    = cache

    def scrape(self) -> list[dict]:
        # Try RSS first
        articles = self._scrape_rss()
        if not articles:
            articles = self._scrape_html()
        log.info(f"Medium: {len(articles)} articles")
        return articles

    def _scrape_rss(self) -> list[dict]:
        content = fetch(self.rss_url, self.cache)
        if not content:
            return []
        feed = feedparser.parse(content)
        results = []
        for entry in feed.entries:
            tags = [t.get("term", "") for t in getattr(entry, "tags", [])]
            # Extract text preview from summary
            summary = ""
            if hasattr(entry, "summary"):
                soup = BeautifulSoup(entry.summary, "html.parser")
                summary = soup.get_text(separator=" ")[:500]

            published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:3]).strftime("%Y-%m-%d")

            results.append({
                "type":        "literature",
                "title":       entry.get("title", ""),
                "description": summary,
                "url":         entry.get("link", ""),
                "source":      "medium",
                "source_url":  self.rss_url,
                "tags":        tags,
                "ext": {
                    "platform":     "medium",
                    "author":       self.username,
                    "published_at": published,
                    "lang":         "en",
                },
            })
        return results

    def _scrape_html(self) -> list[dict]:
        html = fetch(self.profile_url, self.cache)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        results = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not re.match(r"^/[a-z0-9-]+-[a-f0-9]{8,}$", href):
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 8 or href in seen:
                continue
            seen.add(href)
            results.append({
                "type":   "literature",
                "title":  title,
                "url":    f"https://medium.com{href}",
                "source": "medium",
                "source_url": self.profile_url,
                "tags":   [],
                "ext": {"platform": "medium", "author": self.username},
            })
        return results


# ─────────────────────────────────────────────────────────────────────────────
# BLOG SCRAPER  (Hugo / Jekyll / WordPress)
# ─────────────────────────────────────────────────────────────────────────────

class BlogScraper:
    """
    Scrapes a personal blog's index. Tries RSS first, falls back to HTML.
    Produces entity dicts of type='literature'.
    """

    def __init__(self, cfg: dict, cache: HttpCache):
        self.base_url    = cfg["url"].rstrip("/")
        self.url_pattern = cfg.get("url_pattern", r"\d{4}/")
        self.rss_paths   = cfg.get("rss_paths", ["/feed", "/feed.xml", "/rss.xml", "/index.xml"])
        self.cache       = cache

    def scrape(self) -> list[dict]:
        # Try RSS feeds
        for rss_path in self.rss_paths:
            url = self.base_url + rss_path
            content = fetch(url, self.cache)
            if content and ("<rss" in content or "<feed" in content):
                results = self._parse_rss(content, url)
                if results:
                    log.info(f"Blog RSS ({rss_path}): {len(results)} posts")
                    return results
        # Fall back to HTML
        results = self._scrape_html()
        log.info(f"Blog HTML: {len(results)} posts")
        return results

    def _parse_rss(self, content: str, feed_url: str) -> list[dict]:
        feed = feedparser.parse(content)
        results = []
        for entry in feed.entries:
            tags = [t.get("term", "") for t in getattr(entry, "tags", [])]
            summary = ""
            if hasattr(entry, "summary"):
                soup = BeautifulSoup(entry.summary or "", "html.parser")
                summary = soup.get_text(separator=" ")[:400]

            published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:3]).strftime("%Y-%m-%d")

            link = entry.get("link", "")
            lang = "en" if "/en/" in link else "de"

            results.append({
                "type":        "literature",
                "title":       entry.get("title", ""),
                "description": summary,
                "url":         link,
                "source":      "blog",
                "source_url":  feed_url,
                "tags":        tags,
                "language":    lang,
                "ext": {
                    "platform":     "blog",
                    "published_at": published,
                    "lang":         lang,
                },
            })
        return results

    def _scrape_html(self) -> list[dict]:
        html = fetch(self.base_url + "/", self.cache)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        results = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not re.search(self.url_pattern, href) or not title or len(title) < 5:
                continue
            full_url = href if href.startswith("http") else urljoin(self.base_url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", href)
            date = date_match.group(1) if date_match else ""
            lang = "en" if "/en/" in href else "de"

            results.append({
                "type":     "literature",
                "title":    title,
                "url":      full_url,
                "source":   "blog",
                "source_url": self.base_url,
                "language": lang,
                "tags":     [],
                "ext": {
                    "platform":     "blog",
                    "published_at": date,
                    "lang":         lang,
                },
            })
        return results


# ─────────────────────────────────────────────────────────────────────────────
# LINKEDIN EXPORT PARSER
# ─────────────────────────────────────────────────────────────────────────────

class LinkedInParser:
    """
    Parses a structured YAML export of LinkedIn data.
    LinkedIn cannot be scraped reliably — export via Settings → Data privacy.

    Expected YAML structure: see linkedin_profile.pdf.yaml (auto-generated cache)
    Produces entity dicts for: professional, company, education, institution, achievement.

    Can be initialized with either:
    - export_path: Path to YAML file
    - data: Pre-loaded dict (for manual JSON mode)
    """

    def __init__(self, export_path: Optional[Path] = None, data: Optional[dict] = None):
        self.path = export_path
        self.data = data

    def parse(self) -> list[dict]:
        """Parse LinkedIn export (YAML file or dict)."""
        data = None

        if self.data:
            # Use pre-loaded data
            data = self.data
        elif self.path and self.path.exists():
            # Load from YAML file
            import yaml
            with open(self.path) as f:
                data = yaml.safe_load(f)
        else:
            if self.path:
                log.info(f"No LinkedIn export found at {self.path}")
            else:
                log.error("LinkedInParser: Neither path nor data provided")
            return []

        return self._parse_data(data)

    def _parse_data(self, data: dict) -> list[dict]:
        """Core parsing logic (handles both YAML and JSON data)."""
        results = []

        # ── Experience ─────────────────────────────────────────────────────
        for job in data.get("experience", []):
            company_title = job.get("company", "")

            # Create company entity
            co = {
                "type":   "company",
                "title":  company_title,
                "source": "linkedin",
                "tags":   job.get("tags", []),
                "ext": {"industry": job.get("industry"), "hq": job.get("location")},
            }
            results.append(co)

            # Create professional entity
            prof = {
                "type":       "professional",
                "title":      f"{job.get('role', 'Role')} at {company_title}",
                "description": job.get("description"),
                "source":     "linkedin",
                "start_date": job.get("start_date"),
                "end_date":   job.get("end_date"),
                "is_current": not bool(job.get("end_date")),
                "tags":       job.get("tags", []),
                "ext": {
                    "role":            job.get("role"),
                    "employment_type": job.get("employment_type", "full_time"),
                    "location":        job.get("location"),
                    "_company_title":  company_title,   # resolved to FK by seeder
                },
            }
            # Sub-projects within a job
            for proj in job.get("projects", []):
                p = {
                    "type":        "side_project",
                    "title":       proj.get("title"),
                    "description": proj.get("description"),
                    "source":      "linkedin",
                    "tags":        proj.get("tags", []),
                    "ext": {
                        "status": "active",
                        "_part_of_job": prof["title"],   # seeder resolves to relation
                    },
                }
                results.append(p)

            results.append(prof)

        # ── Education ──────────────────────────────────────────────────────
        for edu in data.get("education", []):
            inst_title = edu.get("institution", "")

            results.append({
                "type":   "institution",
                "title":  inst_title,
                "source": "linkedin",
                "ext": {
                    "inst_type": edu.get("inst_type", "university"),
                    "country":   edu.get("country"),
                    "website":   edu.get("website"),
                },
            })

            results.append({
                "type":       "education",
                "title":      edu.get("degree") or edu.get("title"),
                "description": edu.get("description"),
                "source":     "linkedin",
                "start_date": edu.get("start_date"),
                "end_date":   edu.get("end_date"),
                "tags":       edu.get("tags", []),
                "ext": {
                    "degree":          edu.get("degree"),
                    "field":           edu.get("field"),
                    "grade":           edu.get("grade"),
                    "exchange":        1 if edu.get("exchange") else 0,
                    "_institution_title": inst_title,
                },
            })

        # ── Certifications ─────────────────────────────────────────────────
        for cert in data.get("certifications", []):
            results.append({
                "type":       "achievement",
                "title":      cert.get("name"),
                "source":     "linkedin",
                "start_date": cert.get("issued"),
                "tags":       cert.get("tags", []),
                "ext": {
                    "credential_id":  cert.get("credential_id"),
                    "credential_url": cert.get("credential_url"),
                    "expires_at":     cert.get("expires"),
                    "_issuer_title":  cert.get("issuer"),
                },
            })

        log.info(f"LinkedIn: parsed {len(results)} entities")
        return results
