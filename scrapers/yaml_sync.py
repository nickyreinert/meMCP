"""
scrapers/yaml_sync.py — YAML Cache Synchronization
====================================================
Single source of truth YAML files for local file sources (PDF, HTML).
Handles bidirectional sync between YAML cache and database.

Purpose:
  - YAML is the single point of truth for local file sources
  - Track sync state via timestamp comparison
  - Atomic writes to prevent corruption
  - Separate raw data from LLM-enriched data

Workflow:
  1. Scrape (PDF/HTML) → YAML (raw data)
  2. YAML → DB insert → Update YAML with entity_id
  3. LLM enrich DB → Update YAML with enriched fields
  4. User edits YAML → File mtime > last_synced → Reload to DB

Functions:
  - load_yaml_with_metadata(): Load YAML and check sync state
  - save_yaml_atomic(): Save YAML with atomic write
  - update_yaml_after_db_insert(): Add entity_id to YAML
  - update_yaml_after_llm(): Add LLM fields to YAML
  - needs_reload(): Check if YAML was manually edited
"""

import logging
import yaml
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

log = logging.getLogger("mcp.yaml_sync")


# --- METADATA HANDLING ---

def now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def get_file_mtime(file_path: Path) -> Optional[str]:
    """Get file modification time as ISO timestamp."""
    if not file_path.exists():
        return None
    mtime = file_path.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def needs_reload(file_path: Path, last_synced: Optional[str] = None) -> bool:
    """
    Check if YAML file needs reload based on modification time.
    
    Args:
        file_path: Path to YAML file
        last_synced: Last sync timestamp from metadata (ISO format)
    
    Returns:
        True if file was modified after last_synced
    """
    if not file_path.exists():
        return False
    
    if not last_synced:
        return True  # No sync record, needs reload
    
    file_mtime = get_file_mtime(file_path)
    if not file_mtime:
        return False
    
    # Compare timestamps
    try:
        file_dt = datetime.fromisoformat(file_mtime)
        sync_dt = datetime.fromisoformat(last_synced)
        return file_dt > sync_dt
    except Exception as e:
        log.warning(f"Failed to compare timestamps: {e}")
        return True  # Assume needs reload on error


# --- YAML LOADING ---

def load_yaml_with_metadata(file_path: Path) -> tuple[Optional[Dict], Optional[Dict]]:
    """
    Load YAML file with metadata header.
    
    Returns:
        Tuple of (metadata_dict, data_dict)
        metadata_dict contains: {last_synced, source}
        data_dict contains: {articles: [...]} or {experience: [...], ...}
    """
    if not file_path.exists():
        log.debug(f"YAML file not found: {file_path}")
        return None, None
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            full_data = yaml.safe_load(f)
        
        if not full_data:
            return None, None
        
        # Extract metadata if present
        metadata = full_data.get('_metadata', {})
        
        # Data is everything except _metadata
        data = {k: v for k, v in full_data.items() if k != '_metadata'}
        
        log.info(f"Loaded YAML from {file_path} (last_synced: {metadata.get('last_synced', 'never')})")
        return metadata, data
    
    except Exception as e:
        log.error(f"Failed to load YAML from {file_path}: {e}")
        return None, None


# --- YAML SAVING ---

def save_yaml_atomic(
    file_path: Path,
    data: Dict[str, Any],
    source: str,
    metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Save YAML file with atomic write (temp file + rename).
    Adds/updates metadata header with last_synced timestamp.
    
    Args:
        file_path: Target YAML file path
        data: Data to save (articles, experience, etc.)
        source: Source name (medium, linkedin)
        metadata: Optional existing metadata to preserve
    
    Returns:
        True if save successful
    """
    try:
        # Build metadata
        meta = metadata or {}
        meta['last_synced'] = now_iso()
        meta['source'] = source
        
        # Build full structure
        full_data = {'_metadata': meta}
        full_data.update(data)
        
        # Write to temp file first (atomic operation)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp"
        )
        
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                yaml.dump(
                    full_data,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                    width=120
                )
            
            # Atomic rename
            os.replace(temp_path, file_path)
            log.info(f"Saved YAML to {file_path} (atomic write)")
            return True
        
        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise e
    
    except Exception as e:
        log.error(f"Failed to save YAML to {file_path}: {e}")
        return False


# --- DB SYNC OPERATIONS ---

def update_yaml_after_db_insert(
    file_path: Path,
    url_to_entity_id: Dict[str, str]
) -> bool:
    """
    Update YAML file after DB insert to add entity_id to each article.
    
    Args:
        file_path: Path to YAML file
        url_to_entity_id: Mapping of URL → entity_id
    
    Returns:
        True if update successful
    """
    metadata, data = load_yaml_with_metadata(file_path)
    if not data:
        log.warning(f"No data found in {file_path}")
        return False
    
    # Update articles/items with entity_id
    updated = False
    for key in data:
        if isinstance(data[key], list):
            for item in data[key]:
                if isinstance(item, dict) and 'url' in item:
                    url = item['url']
                    if url in url_to_entity_id:
                        item['entity_id'] = url_to_entity_id[url]
                        updated = True
    
    if updated:
        source = metadata.get('source', 'unknown') if metadata else 'unknown'
        return save_yaml_atomic(file_path, data, source, metadata)
    
    return False


def update_yaml_after_llm(
    file_path: Path,
    entity_id_to_enrichment: Dict[str, Dict[str, Any]]
) -> bool:
    """
    Update YAML file after LLM enrichment to add enriched fields.
    
    Args:
        file_path: Path to YAML file
        entity_id_to_enrichment: Mapping of entity_id → enrichment data
            enrichment data: {description, technologies, skills, tags, llm_model}
    
    Returns:
        True if update successful
    """
    metadata, data = load_yaml_with_metadata(file_path)
    if not data:
        log.warning(f"No data found in {file_path}")
        return False
    
    # Update items with LLM fields
    updated = False
    for key in data:
        if isinstance(data[key], list):
            for item in data[key]:
                if isinstance(item, dict) and 'entity_id' in item:
                    entity_id = item['entity_id']
                    if entity_id in entity_id_to_enrichment:
                        enrichment = entity_id_to_enrichment[entity_id]
                        
                        # Update fields
                        if enrichment.get('description'):
                            item['description'] = enrichment['description']
                        if enrichment.get('technologies'):
                            item['technologies'] = enrichment['technologies']
                        if enrichment.get('skills'):
                            item['skills'] = enrichment['skills']
                        if enrichment.get('tags'):
                            item['tags'] = enrichment['tags']
                        
                        item['llm_enriched'] = True
                        item['llm_model'] = enrichment.get('llm_model', 'unknown')
                        item['llm_enriched_at'] = now_iso()
                        
                        updated = True
    
    if updated:
        source = metadata.get('source', 'unknown') if metadata else 'unknown'
        return save_yaml_atomic(file_path, data, source, metadata)
    
    return False


# --- YAML TO DB RELOAD ---

def get_entities_from_yaml(file_path: Path, source_name: str) -> List[Dict[str, Any]]:
    """
    Load entities from YAML for re-import to DB.
    Used when YAML was manually edited (file mtime > last_synced).
    
    Args:
        file_path: Path to YAML file
        source_name: Source name for entity assignment
    
    Returns:
        List of entity dicts ready for DB insert
    """
    metadata, data = load_yaml_with_metadata(file_path)
    if not data:
        return []
    
    entities = []
    
    # Convert YAML structure to entity format
    # Handle different structures (articles, experience, etc.)
    for key, items in data.items():
        if not isinstance(items, list):
            continue
        
        for item in items:
            if not isinstance(item, dict):
                continue
            
            # Build entity from YAML data
            entity = {
                'flavor': 'oeuvre' if key == 'articles' else 'stages',
                'category': _infer_category(key),
                'title': item.get('title', ''),
                'description': item.get('description', ''),
                'url': item.get('url', ''),
                'source': source_name,
                'source_url': item.get('url', ''),
                'date': item.get('published_at') or item.get('date'),
                'start_date': item.get('start_date'),
                'end_date': item.get('end_date'),
                'is_current': item.get('is_current', 0),
                'language': item.get('language', 'en'),
                'visibility': item.get('visibility', 'public'),
                'technologies': item.get('technologies', []),
                'skills': item.get('skills', []),
                'tags': item.get('tags', []),
                'llm_enriched': 1 if item.get('llm_enriched') else 0,
                'llm_model': item.get('llm_model'),
                'llm_enriched_at': item.get('llm_enriched_at'),
            }
            
            # Include entity_id if present (for updates)
            if 'entity_id' in item:
                entity['id'] = item['entity_id']
            
            entities.append(entity)
    
    return entities


def _infer_category(key: str) -> Optional[str]:
    """Infer entity category from YAML key."""
    mapping = {
        'articles': 'article',
        'experience': 'job',
        'education': 'education',
        'projects': 'coding',
        'certifications': 'certification',
    }
    return mapping.get(key)
