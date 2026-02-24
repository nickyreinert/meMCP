"""
scrapers/identity.py — Identity Data Scraper
============================================
Loads identity data from identity.yaml and creates four entities:
- identity/basic   → name, tagline, description, location
- identity/links   → github, medium, blog, linkedin, etc.
- identity/contact → reason, preferred, email, phone, telegram, other
- identity/career  → status, preferred_roles, industries_of_interest, location_preferences, workload_preference, salary_expectation

Purpose:
- Transform identity.yaml into database entities
- Support multi-language (de, en, etc.)
- Store data as JSON in description field for frontend parsing

Main functions:
- run(): Fetch and parse identity.yaml
- _create_identity_entities(): Create three category entities

Dependent files:
- base.py (BaseScraper)
- identity.yaml (data source)
"""

import logging
import yaml
import json
from typing import List, Dict, Any
from pathlib import Path
from .base import BaseScraper

log = logging.getLogger("mcp.scrapers.identity")


class IdentityScraper(BaseScraper):
    """
    Load identity data from identity.yaml file.
    
    Expected YAML structure:
    ```yaml
    identity:
      de:
        basic:
          name: "Jane Doe"
          tagline: "..."
          description: "..."
          location: "Berlin, Germany"
        links:
          github: "https://github.com/..."
          medium: "https://..."
          blog: "https://..."
          linkedin: "https://..."
        contact:
          reason: "..."
          preferred: "email"
          email: "..."
          phone: "..."
          telegram: "..."
          other: "..."
      en:
        basic: {...}
        links: {...}
        contact: {...}
    ```
    """
    
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Load identity data and create four entities (basic, links, contact, career).
        
        Args:
            force: If True, re-process even if cached
        
        Returns:
            List of 3 entity dictionaries
        
        Process:
        1. Read identity.yaml
        2. Extract multi-lang data for each category
        3. Create 3 entities with flavor='identity' and category='basic|links|contact'
        4. Store multi-lang data as JSON in description field
        
        Dependent functions:
        - _load_identity_file()
        - _create_identity_entities()
        """
        log.info(f"Running identity scraper for {self.name}")
        
        # Get source path from config
        source_path = self.config.get("source")
        if not source_path:
            log.error(f"Missing 'source' path for identity in config")
            return []
        
        identity_data = self._load_identity_file(source_path)
        if not identity_data:
            return []
        
        entities = self._create_identity_entities(identity_data)
        log.info(f"Created {len(entities)} identity entities")
        
        return entities
    
    def _load_identity_file(self, file_path: str) -> Dict[str, Any]:
        """
        Load and parse identity.yaml file.
        
        Args:
            file_path: Path to identity.yaml
        
        Returns:
            Dict with identity data structure
        
        Process:
        1. Load YAML file
        2. Validate structure
        3. Return identity data
        """
        yaml_path = Path(file_path)
        if not yaml_path.exists():
            log.error(f"Identity file not found: {yaml_path}")
            return {}
        
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data or 'identity' not in data:
                log.error(f"Invalid identity.yaml structure - missing 'identity' key")
                return {}
            
            log.info(f"Loaded identity data from {yaml_path}")
            return data['identity']
            
        except Exception as e:
            log.error(f"Failed to load {yaml_path}: {e}")
            return {}
    
    def _create_identity_entities(self, identity_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Create three identity entities from multi-lang data.
        
        Args:
            identity_data: Dict with language keys (de, en) containing categories
        
        Returns:
            List of 3 entity dicts (basic, links, contact)
        
        Process:
        1. For each category (basic, links, contact)
        2. Collect data from all languages
        3. Create entity with JSON-dumped multi-lang data
        4. Use English as primary title/description
        
        Dependent functions: None
        """
        entities = []
        categories = ['basic', 'links', 'contact', 'career']

        for category in categories:
            # Collect multi-lang data for this category
            multi_lang_data = {}
            title = category.capitalize()
            description_text = ""

            for lang, lang_data in identity_data.items():
                # Skip the 'people' section (future feature)
                if lang == 'people':
                    continue

                category_data = lang_data.get(category, {})
                if category_data:
                    multi_lang_data[lang] = category_data

                    # Use first available language for title/description
                    if lang == 'en' or not description_text:
                        if category == 'basic':
                            title = category_data.get('name', title)
                            description_text = category_data.get('tagline', '')
                        elif category == 'links':
                            title = "Links"
                            description_text = "Professional links and social profiles"
                        elif category == 'contact':
                            title = "Contact"
                            description_text = category_data.get('reason', '')
                        elif category == 'career':
                            title = "Career"
                            description_text = category_data.get('status', '')
            
            # Create entity with multi-lang data stored as JSON
            entity = {
                "flavor": "identity",
                "category": category,
                "title": title,
                "description": description_text,
                "source": "identity",
                "url": None,
                "tags": [],
                "raw_data": multi_lang_data  # Store multi-lang data here
            }
            
            entities.append(entity)
            log.debug(f"Created identity entity: {category}")
        
        return entities
