"""
scripts/migrate_add_raw_data.py — Add raw_data Column Migration
================================================================
One-time migration script to add raw_data column to entities table.

Purpose:
- Add raw_data TEXT column to existing entities table
- Safe to run multiple times (uses ALTER TABLE IF NOT EXISTS logic)

Main functions:
- migrate(): Add raw_data column

Dependent files:
- db/models.py (database connection)
"""

import sqlite3
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger("mcp.migration")

DB_PATH = Path(__file__).parent.parent / "db" / "profile.db"


def migrate():
    """
    Add raw_data column to entities table.
    
    Process:
    1. Connect to database
    2. Check if column exists
    3. Add column if missing
    4. Commit changes
    
    Dependent functions: None
    """
    log.info("Starting migration: add raw_data column to entities table")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    try:
        # Check if column already exists
        cursor = conn.execute("PRAGMA table_info(entities)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "raw_data" in columns:
            log.info("Column 'raw_data' already exists, skipping migration")
            return
        
        # Add the column
        log.info("Adding 'raw_data' column to entities table...")
        conn.execute("ALTER TABLE entities ADD COLUMN raw_data TEXT")
        conn.commit()
        
        log.info("Migration completed successfully")
        
    except Exception as e:
        log.error(f"Migration failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
