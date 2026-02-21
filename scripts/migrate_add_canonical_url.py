"""
scripts/migrate_add_canonical_url.py — Add canonical_url Column
================================================================
Adds canonical_url column to entities table for cross-post detection.

Usage:
  python scripts/migrate_add_canonical_url.py
"""

import logging
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "db" / "profile.db"


def migrate():
    """Add canonical_url column and index to entities table."""
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # Check if column already exists
        cursor = conn.execute("PRAGMA table_info(entities)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'canonical_url' in columns:
            log.info("canonical_url column already exists, skipping migration")
            return
        
        log.info("Adding canonical_url column to entities table...")
        
        # Add column
        conn.execute("ALTER TABLE entities ADD COLUMN canonical_url TEXT;")
        
        # Add index
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entities_canonical 
            ON entities(canonical_url) WHERE canonical_url IS NOT NULL;
        """)
        
        conn.commit()
        log.info("✓ Migration complete: canonical_url column and index added")
        
    except Exception as e:
        conn.rollback()
        log.error(f"Migration failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
