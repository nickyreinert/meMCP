"""
scrapers/linkedin_pdf_scraper.py — LinkedIn PDF Scraper with Smart Caching
===========================================================================
Parses LinkedIn profile PDF export with intelligent YAML caching.
Handles incremental updates and LLM enrichment detection.

YAML Sync Integration:
- First run: Parses PDF → creates `<file>.yaml` cache
- Subsequent runs: 
  - Checks PDF mtime vs YAML last_synced
  - If PDF modified OR --force flag → re-parse PDF
  - Loads YAML cache (preserves manual edits + LLM enrichment)
  - For each entity: checks if LLM enriched
    - If missing enrichment → flags for re-enrichment
    - If has enrichment → uses cached data
  - Updates YAML if changes detected
- Manual editing: Edit YAML to refine content (preserved during updates)

Depends on:
- pypdf for PDF text extraction
- base.BaseScraper for common functionality
- yaml_sync module for bidirectional YAML ↔ DB synchronization
- linkedin_pdf.LinkedInPDFParser for PDF parsing logic
"""

import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from .base import BaseScraper
from .yaml_sync import (
    load_yaml_with_metadata,
    save_yaml_atomic,
    needs_reload,
    get_file_mtime
)

log = logging.getLogger("mcp.scrapers.linkedin_pdf_scraper")


class LinkedInPDFScraper(BaseScraper):
    """
    Parses a LinkedIn profile PDF export with smart caching.
    
    Purpose:
    - Extract professional history from LinkedIn PDF
    - Cache results in YAML for manual editing
    - Detect when PDF has changed and re-process
    - Preserve LLM enrichment data across runs
    
    Input: PDF file path (file:// URL)
    Output: List of stage entities (jobs, education, certifications)
    """
    
    def run(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Parse the PDF file and extract all stages entities.
        Uses YAML cache with sync detection (file mtime vs last_synced).
        
        Args:
            force: If True, re-process PDF even if YAML cache exists
        
        Returns:
            List of stage entity dictionaries
        """
        url = self.config.get("url")
        if not url:
            log.error(f"Missing URL for source {self.name}")
            return []
        
        # Handle file:// URLs
        if url.startswith("file://"):
            file_path = url[7:]  # Remove 'file://' prefix
            pdf_path = Path(file_path)
            
            if not pdf_path.exists():
                log.error(f"File not found: {pdf_path}")
                return []
        else:
            log.error(f"Only file:// URLs supported for linkedin_pdf connector, got: {url}")
            return []
        
        # Check for YAML cache
        yaml_cache_path = Path(str(pdf_path) + ".yaml")
        self.yaml_cache_path = yaml_cache_path  # Expose for ingest.py
        
        # Determine if we need to re-parse PDF
        should_reparse = False
        metadata = None
        data = None
        
        if yaml_cache_path.exists():
            # Load existing YAML cache
            metadata, data = load_yaml_with_metadata(yaml_cache_path)
            
            # Check if PDF was modified after last sync
            last_synced = metadata.get('last_synced') if metadata else None
            if needs_reload(pdf_path, last_synced):
                log.info(f"PDF modified since last sync (mtime > last_synced)")
                should_reparse = True
            elif force:
                log.info(f"Force mode: re-parsing PDF")
                should_reparse = True
            else:
                log.info(f"Using YAML cache (PDF unchanged since {last_synced})")
        else:
            # No YAML cache exists - must parse PDF
            log.info(f"No YAML cache found at {yaml_cache_path}")
            should_reparse = True
        
        # Parse PDF if needed
        if should_reparse:
            entities = self._parse_pdf(pdf_path)
            
            if entities:
                # Save to YAML cache
                log.info(f"Saving {len(entities)} entities to YAML cache")
                self._save_to_yaml(entities, yaml_cache_path)
                log.info(f"✓ YAML cache created/updated at {yaml_cache_path}")
            
            return entities
        else:
            # Load from YAML cache
            if data:
                entities = self._load_from_yaml(yaml_cache_path, metadata, data)
                log.info(f"✓ Loaded {len(entities)} entities from YAML cache")
                return entities
            else:
                log.error(f"Failed to load YAML cache from {yaml_cache_path}")
                return []
    
    def _parse_pdf(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """
        Parse PDF using LinkedInPDFParser.
        
        Args:
            pdf_path: Path to LinkedIn PDF export
        
        Returns:
            List of entity dictionaries
        """
        if not self.llm:
            log.error("LLM enricher required for PDF parsing")
            log.error(f"Cannot parse {pdf_path} without LLM")
            log.error(f"If you have a YAML cache, ensure it exists at {pdf_path}.yaml")
            return []
        
        log.info(f"Parsing LinkedIn PDF: {pdf_path}")
        
        try:
            from .linkedin_pdf import LinkedInPDFParser
            parser = LinkedInPDFParser(pdf_path, llm_enricher=self.llm)
            entities = parser.parse()
            
            log.info(f"Parsed {len(entities)} entities from PDF")
            return entities
            
        except Exception as e:
            log.error(f"Failed to parse PDF: {e}")
            return []
    
    def _load_from_yaml(
        self,
        yaml_path: Path,
        metadata: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        Load stages entities from YAML cache using yaml_sync module.
        
        Args:
            yaml_path: Path to YAML cache file
            metadata: Pre-loaded metadata (optional)
            data: Pre-loaded data (optional)
        
        Returns:
            List of entity dictionaries
        """
        if data is None:
            metadata, data = load_yaml_with_metadata(yaml_path)
        
        if not data:
            log.error(f"Invalid YAML structure in {yaml_path}")
            return []
        
        results = []
        
        # Process experience entries
        for job in data.get("experience", []):
            entity = {
                "flavor": "stages",
                "category": "job",
                "title": job.get("title") or f"{job.get('role', 'Role')} at {job.get('company', 'Company')}",
                "description": job.get("description", ""),
                "source": self.name,
                "source_url": "",
                "start_date": job.get("start_date"),
                "end_date": job.get("end_date"),
                "is_current": not bool(job.get("end_date")),
                "technologies": job.get("technologies", []),
                "skills": job.get("skills", []),
                "tags": job.get("tags", []),
                "ext": {
                    "company": job.get("company"),
                    "role": job.get("role"),
                    "employment_type": job.get("employment_type"),
                    "location": job.get("location"),
                }
            }
            
            # Include entity_id if present (for syncing back)
            if "entity_id" in job:
                entity["id"] = job["entity_id"]
            
            # Include LLM enrichment status
            if job.get("llm_enriched"):
                entity["llm_enriched"] = 1
                entity["llm_model"] = job.get("llm_model")
                entity["llm_enriched_at"] = job.get("llm_enriched_at")
            
            results.append(entity)
        
        # Process education entries
        for edu in data.get("education", []):
            entity = {
                "flavor": "stages",
                "category": "education",
                "title": edu.get("title") or f"{edu.get('degree', 'Degree')} at {edu.get('institution', 'Institution')}",
                "description": edu.get("description", ""),
                "source": self.name,
                "source_url": "",
                "start_date": edu.get("start_date"),
                "end_date": edu.get("end_date"),
                "technologies": edu.get("technologies", []),
                "skills": edu.get("skills", []),
                "tags": edu.get("tags", []),
                "ext": {
                    "institution": edu.get("institution"),
                    "degree": edu.get("degree"),
                    "field": edu.get("field"),
                }
            }
            
            # Include entity_id if present
            if "entity_id" in edu:
                entity["id"] = edu["entity_id"]
            
            # Include LLM enrichment status
            if edu.get("llm_enriched"):
                entity["llm_enriched"] = 1
                entity["llm_model"] = edu.get("llm_model")
                entity["llm_enriched_at"] = edu.get("llm_enriched_at")
            
            results.append(entity)
        
        # Process certifications
        for cert in data.get("certifications", []):
            entity = {
                "flavor": "stages",
                "category": "achievement",
                "title": cert.get("name", "Certification"),
                "description": cert.get("description", ""),
                "source": self.name,
                "source_url": cert.get("credential_url", ""),
                "start_date": cert.get("issued"),
                "technologies": cert.get("technologies", []),
                "skills": cert.get("skills", []),
                "tags": cert.get("tags", []),
                "ext": {
                    "issuer": cert.get("issuer"),
                    "credential_id": cert.get("credential_id"),
                    "credential_url": cert.get("credential_url"),
                }
            }
            
            # Include entity_id if present
            if "entity_id" in cert:
                entity["id"] = cert["entity_id"]
            
            # Include LLM enrichment status
            if cert.get("llm_enriched"):
                entity["llm_enriched"] = 1
                entity["llm_model"] = cert.get("llm_model")
                entity["llm_enriched_at"] = cert.get("llm_enriched_at")
            
            results.append(entity)
        
        log.info(f"Loaded {len(results)} entities from YAML cache")
        return results
    
    def _save_to_yaml(self, entities: List[Dict[str, Any]], yaml_path: Path):
        """
        Save entities to YAML cache using yaml_sync module (atomic write).
        
        Args:
            entities: List of entity dictionaries
            yaml_path: Path to save YAML cache
        """
        # Group entities by type
        experience = []
        education = []
        certifications = []
        
        for entity in entities:
            category = entity.get("category")
            
            if category == "job":
                yaml_item = {
                    "company": entity.get("ext", {}).get("company"),
                    "role": entity.get("ext", {}).get("role"),
                    "title": entity.get("title"),
                    "employment_type": entity.get("ext", {}).get("employment_type"),
                    "location": entity.get("ext", {}).get("location"),
                    "start_date": entity.get("start_date"),
                    "end_date": entity.get("end_date"),
                    "description": entity.get("description", ""),
                }
                
                # Include entity_id if present
                if "id" in entity:
                    yaml_item["entity_id"] = entity["id"]
                
                # Include LLM fields if present
                if entity.get("technologies"):
                    yaml_item["technologies"] = entity["technologies"]
                if entity.get("skills"):
                    yaml_item["skills"] = entity["skills"]
                if entity.get("tags"):
                    yaml_item["tags"] = entity["tags"]
                if entity.get("llm_enriched"):
                    yaml_item["llm_enriched"] = True
                    yaml_item["llm_model"] = entity.get("llm_model")
                    yaml_item["llm_enriched_at"] = entity.get("llm_enriched_at")
                
                experience.append(yaml_item)
            
            elif category == "education":
                yaml_item = {
                    "institution": entity.get("ext", {}).get("institution"),
                    "degree": entity.get("ext", {}).get("degree"),
                    "field": entity.get("ext", {}).get("field"),
                    "title": entity.get("title"),
                    "start_date": entity.get("start_date"),
                    "end_date": entity.get("end_date"),
                    "description": entity.get("description", ""),
                }
                
                # Include entity_id if present
                if "id" in entity:
                    yaml_item["entity_id"] = entity["id"]
                
                # Include LLM fields if present
                if entity.get("technologies"):
                    yaml_item["technologies"] = entity["technologies"]
                if entity.get("skills"):
                    yaml_item["skills"] = entity["skills"]
                if entity.get("tags"):
                    yaml_item["tags"] = entity["tags"]
                if entity.get("llm_enriched"):
                    yaml_item["llm_enriched"] = True
                    yaml_item["llm_model"] = entity.get("llm_model")
                    yaml_item["llm_enriched_at"] = entity.get("llm_enriched_at")
                
                education.append(yaml_item)
            
            elif category == "achievement":
                yaml_item = {
                    "name": entity.get("title"),
                    "issuer": entity.get("ext", {}).get("issuer"),
                    "issued": entity.get("start_date"),
                    "credential_id": entity.get("ext", {}).get("credential_id"),
                    "credential_url": entity.get("ext", {}).get("credential_url"),
                    "description": entity.get("description", ""),
                }
                
                # Include entity_id if present
                if "id" in entity:
                    yaml_item["entity_id"] = entity["id"]
                
                # Include LLM fields if present
                if entity.get("technologies"):
                    yaml_item["technologies"] = entity["technologies"]
                if entity.get("skills"):
                    yaml_item["skills"] = entity["skills"]
                if entity.get("tags"):
                    yaml_item["tags"] = entity["tags"]
                if entity.get("llm_enriched"):
                    yaml_item["llm_enriched"] = True
                    yaml_item["llm_model"] = entity.get("llm_model")
                    yaml_item["llm_enriched_at"] = entity.get("llm_enriched_at")
                
                certifications.append(yaml_item)
        
        yaml_data = {}
        if experience:
            yaml_data["experience"] = experience
        if education:
            yaml_data["education"] = education
        if certifications:
            yaml_data["certifications"] = certifications
        
        # Use atomic save from yaml_sync
        save_yaml_atomic(yaml_path, yaml_data, self.name)
