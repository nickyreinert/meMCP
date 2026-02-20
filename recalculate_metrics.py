#!/usr/bin/env python3
"""
recalculate_metrics.py — Standalone Tag Metrics Recalculation Script
=====================================================================

Purpose:
  Recalculate metrics for all tags (skills, technologies, generic tags)
  without fetching new data. Uses existing entity data in the database.

Usage:
  # Recalculate all metrics
  python recalculate_metrics.py
  
  # Recalculate only specific tag type
  python recalculate_metrics.py --type technology
  python recalculate_metrics.py --type skill
  python recalculate_metrics.py --type generic
  
  # Force recalculation (ignore metrics version)
  python recalculate_metrics.py --force
  
  # Verbose output
  python recalculate_metrics.py --verbose

Process:
  1. Load metrics configuration from config.yaml
  2. Connect to profile database
  3. Collect all distinct tags from entities
  4. Calculate metrics for each tag
  5. Store results in tag_metrics table
  6. Display summary statistics

Output:
  - Number of tags processed
  - Processing time
  - Top skills/technologies by relevance
"""

import argparse
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from db.models import get_db, init_db, DB_PATH
from metrics.calculator import calculate_all_metrics, CONFIG


# --- LOGGING ---

def log_message(msg: str, level: str = "INFO"):
    """Simple logging with timestamp."""
    print(f"[{level}] {msg}")


# --- MAIN LOGIC ---

def display_summary(conn, verbose: bool = False):
    """Display summary statistics of calculated metrics."""
    # Count by tag type
    type_counts = conn.execute("""
        SELECT tag_type, COUNT(*) as count
        FROM tag_metrics
        GROUP BY tag_type
        ORDER BY tag_type
    """).fetchall()
    
    log_message("Metrics Summary:", "INFO")
    for row in type_counts:
        log_message(f"  {row['tag_type']}: {row['count']} tags", "INFO")
    
    if verbose:
        # Top skills by relevance
        log_message("\nTop 10 Skills by Relevance:", "INFO")
        top_skills = conn.execute("""
            SELECT tag_name, relevance_score, entity_count, proficiency
            FROM tag_metrics
            WHERE tag_type = 'skill'
            ORDER BY relevance_score DESC
            LIMIT 10
        """).fetchall()
        
        for i, row in enumerate(top_skills, 1):
            log_message(
                f"  {i}. {row['tag_name']}: "
                f"relevance={row['relevance_score']:.1f}, "
                f"proficiency={row['proficiency']:.1f}, "
                f"entities={row['entity_count']}",
                "INFO"
            )
        
        # Top technologies by relevance
        log_message("\nTop 10 Technologies by Relevance:", "INFO")
        top_techs = conn.execute("""
            SELECT tag_name, relevance_score, entity_count, proficiency
            FROM tag_metrics
            WHERE tag_type = 'technology'
            ORDER BY relevance_score DESC
            LIMIT 10
        """).fetchall()
        
        for i, row in enumerate(top_techs, 1):
            log_message(
                f"  {i}. {row['tag_name']}: "
                f"relevance={row['relevance_score']:.1f}, "
                f"proficiency={row['proficiency']:.1f}, "
                f"entities={row['entity_count']}",
                "INFO"
            )


def recalculate(tag_type: str = None,
               force: bool = False,
               verbose: bool = False):
    """
    Main recalculation function.
    
    Args:
        tag_type: Filter by tag_type (technology|skill|generic), or None for all
        force: Force recalculation even if version matches
        verbose: Enable verbose logging
    """
    log_message("Starting metrics recalculation...", "INFO")
    
    if not CONFIG.get("enabled", True):
        log_message("Metrics calculation is disabled in config.yaml", "ERROR")
        return 1
    
    # Ensure database is initialized
    if not DB_PATH.exists():
        log_message(f"Database not found at {DB_PATH}", "ERROR")
        return 1
    
    # Connect to database
    conn = get_db(DB_PATH)
    
    # Initialize schema (creates tag_metrics table if missing)
    init_db(DB_PATH)
    
    # Check metrics version
    current_version = CONFIG.get("version", "1.0")
    if not force:
        stored_version_row = conn.execute("""
            SELECT DISTINCT metrics_version
            FROM tag_metrics
            LIMIT 1
        """).fetchone()
        
        if stored_version_row:
            stored_version = stored_version_row["metrics_version"]
            if stored_version == current_version:
                log_message(
                    f"Metrics version {current_version} already calculated. "
                    "Use --force to recalculate.",
                    "INFO"
                )
                if verbose:
                    display_summary(conn, verbose)
                conn.close()
                return 0
    
    # Start calculation
    start_time = time.time()
    
    tag_type_str = tag_type or "all tags"
    log_message(f"Calculating metrics for {tag_type_str}...", "INFO")
    
    try:
        count = calculate_all_metrics(conn, tag_type=tag_type)
        elapsed = time.time() - start_time
        
        log_message(
            f"Successfully calculated metrics for {count} tags in {elapsed:.2f}s",
            "INFO"
        )
        
        # Display summary
        if verbose:
            display_summary(conn, verbose)
        
    except Exception as e:
        log_message(f"Error during calculation: {e}", "ERROR")
        conn.close()
        return 1
    
    conn.close()
    return 0


# --- CLI ENTRYPOINT ---

def main():
    """CLI entrypoint with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Recalculate tag metrics (skills, technologies, tags)"
    )
    
    parser.add_argument(
        "--type",
        choices=["technology", "skill", "generic"],
        help="Recalculate only specific tag type (default: all)"
    )
    
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force recalculation even if version matches"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output with top metrics"
    )
    
    args = parser.parse_args()
    
    # Run recalculation
    exit_code = recalculate(
        tag_type=args.type,
        force=args.force,
        verbose=args.verbose
    )
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
