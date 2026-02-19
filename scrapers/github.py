import logging
import requests
from typing import List, Dict, Any
from .base import BaseScraper
import datetime

log = logging.getLogger("mcp.scrapers.github")

class GithubScraper(BaseScraper):
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []
            
        limit = self.config.get("limit", 0)
        fetch_readmes = self.config.get("fetch_readmes", False)
        
        # GitHub API handles pagination via Link header, but let's start simple
        # If url is /users/.../repos, it returns a list
        
        log.info(f"Fetching GitHub repos from {url}")
        
        # No caching for the list endpoint usually, or short cache?
        # For now, let's fetch fresh list
        resp = requests.get(url, headers={"Accept": "application/vnd.github.v3+json"})
        if resp.status_code != 200:
            log.error(f"GitHub API error: {resp.status_code}")
            return []
            
        repos = resp.json()
        if not isinstance(repos, list):
            log.error("GitHub API returned non-list")
            return []
            
        results = []
        count = 0
        
        for repo in repos:
            if limit and count >= limit:
                break
                
            if repo.get("fork"):
                continue # Skip forks maybe? Or make configurable? Default behavior usually skip.
                
            repo_url = repo.get("html_url")
            
            # Check if processed
            if not self.should_fetch(repo_url, force):
                continue

            # Basic mapping
            item = {
                "type": "side_project", # Or 'project'
                "title": repo.get("name"),
                "description": repo.get("description") or "",
                "url": repo_url,
                "source": self.name,
                "source_url": repo_url,
                "created_at": repo.get("created_at"),
                "updated_at": repo.get("updated_at"),
                "tags": [repo.get("language")] if repo.get("language") else [],
                "ext": {
                    "repo_url": repo_url,
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "license": repo.get("license", {}).get("name") if repo.get("license") else None,
                    "language": repo.get("language"),
                    "status": "active" if not repo.get("archived") else "archived"
                }
            }
            
            # Fetch generic README if requested
            if fetch_readmes:
                readme_url = repo.get("url") + "/readme"
                try:
                    rm_resp = requests.get(readme_url, headers={"Accept": "application/vnd.github.raw"})
                    if rm_resp.status_code == 200:
                        item["ext"]["readme"] = rm_resp.text
                except Exception:
                    pass

            results.append(item)
            count += 1
            
        return results
