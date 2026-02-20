"""
scrapers/github.py — GitHub API Scraper
========================================
Fetches repositories from GitHub API with full pagination support.

Features:
- Automatic pagination (fetches ALL repos)
- README fetching (optional)
- Fork filtering (skips forks by default)
- Language detection
- Stars and forks metadata

Depends on:
- requests for HTTP calls
- base.BaseScraper for common functionality
"""

import logging
import requests
from typing import List, Dict, Any, Optional
from .base import BaseScraper
import datetime

log = logging.getLogger("mcp.scrapers.github")

class GithubScraper(BaseScraper):
    """
    Scrapes GitHub repositories via GitHub REST API.
    
    Features:
    - Full pagination support (fetches all repos)
    - Optional README content fetching
    - Configurable limit
    - Fork filtering
    """
    
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []
            
        limit = self.config.get("limit", 0)
        fetch_readmes = self.config.get("fetch_readmes", False)
        
        log.info(f"Fetching GitHub repos from {url}")
        
        # Fetch all repos with pagination
        repos = self._fetch_all_repos(url)
        
        if not repos:
            log.warning("No repositories found or API error")
            return []
        
        log.info(f"Found {len(repos)} repositories total")
        
        results = []
        count = 0
        
        for repo in repos:
            if limit and count >= limit:
                log.info(f"Reached limit of {limit} repos")
                break
                
            # Skip forks (configurable)
            if repo.get("fork") and not self.config.get("include_forks", False):
                log.debug(f"Skipping fork: {repo.get('name')}")
                continue
                
            repo_url = repo.get("html_url")
            
            # Skip already processed repos only if not forcing and skip_cached is enabled
            skip_cached = self.config.get("skip_cached", False)
            if skip_cached and not force and not self.should_fetch(repo_url, force):
                log.debug(f"Skipping already cached: {repo.get('name')}")
                continue

            # Basic mapping
            item = {
                "flavor": "oeuvre",
                "category": self.config.get("sub_type_override", "coding"),
                "title": repo.get("name"),
                "description": repo.get("description") or "",
                "url": repo_url,
                "source": self.name,
                "source_url": repo_url,
                "date": repo.get("created_at"),
                "technologies": [repo.get("language")] if repo.get("language") else [],
                "ext": {
                    "platform": "github",
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "updated_at": repo.get("updated_at"),
                }
            }
            
            # Fetch README if requested
            if fetch_readmes:
                readme_content = self._fetch_readme(repo)
                if readme_content:
                    item["ext"]["readme"] = readme_content
                    # Append to description for LLM enrichment
                    item["description"] = f"{item['description']}\n\n{readme_content[:2000]}"

            results.append(item)
            count += 1
            
        log.info(f"Processed {len(results)} repositories")
        return results
    
    def _fetch_all_repos(self, url: str) -> List[Dict[str, Any]]:
        """
        Fetch all repositories with pagination support.
        GitHub API returns max 100 items per page.
        
        Args:
            url: GitHub API URL (e.g., https://api.github.com/users/USERNAME/repos)
        
        Returns:
            List of all repository dictionaries
        """
        all_repos = []
        page = 1
        per_page = 100  # GitHub maximum per page
        
        # Add pagination params to URL
        separator = "&" if "?" in url else "?"
        current_url = f"{url}{separator}per_page={per_page}&page={page}"
        
        while current_url:
            log.info(f"Fetching page {page} from GitHub API...")
            
            try:
                resp = requests.get(
                    current_url,
                    headers={
                        "Accept": "application/vnd.github.v3+json",
                        "User-Agent": "meMCP-Scraper/1.0"
                    },
                    timeout=30
                )
                
                if resp.status_code != 200:
                    log.error(f"GitHub API error: {resp.status_code} - {resp.text}")
                    break
                
                repos = resp.json()
                
                if not isinstance(repos, list):
                    log.error(f"GitHub API returned non-list: {type(repos)}")
                    break
                
                if not repos:
                    # Empty page, we're done
                    log.info(f"Reached end of repositories (empty page)")
                    break
                
                all_repos.extend(repos)
                log.info(f"  Fetched {len(repos)} repos (total: {len(all_repos)})")
                
                # Check for next page via Link header
                # Format: <https://...>; rel="next", <https://...>; rel="last"
                link_header = resp.headers.get("Link")
                next_url = self._parse_next_link(link_header)
                
                if next_url:
                    current_url = next_url
                    page += 1
                else:
                    # No more pages
                    log.info(f"No more pages (total: {len(all_repos)} repos)")
                    break
                    
            except requests.RequestException as e:
                log.error(f"Failed to fetch GitHub repos: {e}")
                break
        
        return all_repos
    
    def _parse_next_link(self, link_header: Optional[str]) -> Optional[str]:
        """
        Parse GitHub Link header to extract next page URL.
        
        Format: '<https://api.github.com/users/.../repos?page=2>; rel="next"'
        
        Args:
            link_header: HTTP Link header value
        
        Returns:
            Next page URL or None if no next page
        """
        if not link_header:
            return None
        
        # Split by comma to get individual links
        links = link_header.split(",")
        
        for link in links:
            # Each link is like: <URL>; rel="next"
            parts = link.split(";")
            if len(parts) != 2:
                continue
            
            url_part = parts[0].strip()
            rel_part = parts[1].strip()
            
            # Check if this is the "next" link
            if 'rel="next"' in rel_part or "rel='next'" in rel_part:
                # Extract URL from <...>
                if url_part.startswith("<") and url_part.endswith(">"):
                    return url_part[1:-1]
        
        return None
    
    def _fetch_readme(self, repo: Dict[str, Any]) -> Optional[str]:
        """
        Fetch README content for a repository.
        
        Args:
            repo: Repository dictionary from GitHub API
        
        Returns:
            README content as string or None if not available
        """
        readme_url = repo.get("url") + "/readme"
        
        try:
            resp = requests.get(
                readme_url,
                headers={
                    "Accept": "application/vnd.github.raw",
                    "User-Agent": "meMCP-Scraper/1.0"
                },
                timeout=10
            )
            
            if resp.status_code == 200:
                log.debug(f"Fetched README for {repo.get('name')}")
                return resp.text
            else:
                log.debug(f"No README for {repo.get('name')} ({resp.status_code})")
                return None
                
        except requests.RequestException as e:
            log.warning(f"Failed to fetch README for {repo.get('name')}: {e}")
            return None
