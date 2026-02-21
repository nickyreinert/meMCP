"""
scripts/cleanup_duplicate_entities.py — Remove Duplicate Entities
===================================================================
Removes duplicate entities based on natural keys.

Strategy:
  1. Group entities by natural key (source, url) or (source, flavor, title)
  2. Keep the OLDEST entity (by created_at)
  3. Delete newer duplicates
  4. Preserve all tags from duplicates in the kept entity

Usage:
  python scripts/cleanup_duplicate_entities.py --dry-run  # Preview
  python scripts/cleanup_duplicate_entities.py           # Execute cleanup
"""

import argparse
import sqlite3
import logging
from pathlib import Path
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "db" / "profile.db"


def find_duplicates_by_url(conn: sqlite3.Connection) -> dict:
    """
    Find duplicate entities with same (source, url).
    
    Returns:
        Dict mapping (source, url) -> [entity_ids] (sorted by created_at)
    """
    cursor = conn.execute("""
        SELECT id, source, url, created_at, title
        FROM entities
        WHERE url IS NOT NULL AND url != '' AND source IS NOT NULL
        ORDER BY source, url, created_at ASC
    """)
    
    groups = defaultdict(list)
    for row in cursor:
        key = (row[1], row[2])  # (source, url)
        groups[key].append({
            'id': row[0],
            'created_at': row[3],
            'title': row[4]
        })
    
    # Filter to only duplicates (2+ entities)
    return {k: v for k, v in groups.items() if len(v) > 1}


def find_duplicates_by_title(conn: sqlite3.Connection) -> dict:
    """
    Find duplicate entities with same (source, flavor, title) but no URL.
    
    Returns:
        Dict mapping (source, flavor, title) -> [entity_ids]
    """
    cursor = conn.execute("""
        SELECT id, source, flavor, title, created_at
        FROM entities
        WHERE (url IS NULL OR url = '') AND source IS NOT NULL
        ORDER BY source, flavor, title, created_at ASC
    """)
    
    groups = defaultdict(list)
    for row in cursor:
        key = (row[1], row[2], row[3])  # (source, flavor, title)
        groups[key].append({
            'id': row[0],
            'created_at': row[4],
            'title': row[3]
        })
    
    return {k: v for k, v in groups.items() if len(v) > 1}


def merge_tags(conn: sqlite3.Connection, keep_id: str, delete_ids: list[str]):
    """
    Merge tags from duplicate entities into the kept entity.
    
    Args:
        conn: Database connection
        keep_id: Entity ID to keep
        delete_ids: Entity IDs to delete (tags will be merged)
    """
    for delete_id in delete_ids:
        # Copy tags from duplicate to keeper
        conn.execute("""
            INSERT OR IGNORE INTO tags (entity_id, tag, tag_type)
            SELECT ?, tag, tag_type FROM tags WHERE entity_id = ?
        """, (keep_id, delete_id))


def cleanup_duplicates(dry_run: bool = True):
    """
    Remove duplicate entities, keeping oldest by created_at.
    
    Args:
        dry_run: If True, only preview without deleting
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    try:
        # Find duplicates by URL
        url_dupes = find_duplicates_by_url(conn)
        log.info(f"Found {len(url_dupes)} duplicate groups by (source, url)")
        
        # Find duplicates by title (no URL)
        title_dupes = find_duplicates_by_title(conn)
        log.info(f"Found {len(title_dupes)} duplicate groups by (source, flavor, title)")
        
        total_to_delete = 0
        
        # Process URL duplicates
        for (source, url), entities in url_dupes.items():
            keep = entities[0]  # Oldest
            delete = entities[1:]  # Newer ones
            total_to_delete += len(delete)
            
            log.info(f"  [{source}] {keep['title']}")
            log.info(f"    Keep: {keep['id'][:8]} (created: {keep['created_at']})")
            for dup in delete:
                log.info(f"    Delete: {dup['id'][:8]} (created: {dup['created_at']})")
            
            if not dry_run:
                # Merge tags from duplicates
                merge_tags(conn, keep['id'], [d['id'] for d in delete])
                
                # Delete duplicates
                for dup in delete:
                    conn.execute("DELETE FROM entities WHERE id=?", (dup['id'],))
                    log.info(f"      ✓ Deleted {dup['id'][:8]}")
        
        # Process title duplicates
        for (source, flavor, title), entities in title_dupes.items():
            keep = entities[0]
            delete = entities[1:]
            total_to_delete += len(delete)
            
            log.info(f"  [{source}/{flavor}] {title}")
            log.info(f"    Keep: {keep['id'][:8]}")
            for dup in delete:
                log.info(f"    Delete: {dup['id'][:8]}")
            
            if not dry_run:
                merge_tags(conn, keep['id'], [d['id'] for d in delete])
                for dup in delete:
                    conn.execute("DELETE FROM entities WHERE id=?", (dup['id'],))
                    log.info(f"      ✓ Deleted {dup['id'][:8]}")
        
        if not dry_run:
            conn.commit()
            log.info(f"✓ Deleted {total_to_delete} duplicate entities")
        else:
            log.info(f"DRY RUN: Would delete {total_to_delete} duplicate entities")
            log.info("Run without --dry-run to execute cleanup")
    
    except Exception as e:
        conn.rollback()
        log.error(f"Cleanup failed: {e}")
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Clean up duplicate entities")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview duplicates without deleting")
    args = parser.parse_args()
    
    log.info("Starting duplicate cleanup...")
    cleanup_duplicates(dry_run=args.dry_run)
    log.info("Cleanup complete")


if __name__ == "__main__":
    main()
