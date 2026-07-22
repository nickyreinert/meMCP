"""
scripts/migrate_yaml_to_db.py — One-time YAML-to-DB migration
==============================================================

Reads config.tech.yaml (if present) and writes its values into the
DB config table. Safe to run multiple times — existing DB values
are preserved unless --force is passed.

Usage:
    python scripts/migrate_yaml_to_db.py [--force]
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


def migrate_yaml_to_db(yaml_path: Path, db_path: Path, force: bool = False) -> dict:
    """
    Read config.tech.yaml and seed its values into the DB config table.

    Returns:
        {"migrated": N, "skipped": N}
    """
    import yaml
    from db.models import get_db, init_db
    from db.config_store import get_config, set_config, init_encryption

    if not yaml_path.exists():
        logger.info("No config.tech.yaml found at %s — nothing to migrate", yaml_path)
        return {"migrated": 0, "skipped": 0}

    with open(yaml_path) as f:
        tech = yaml.safe_load(f) or {}

    init_db(db_path)

    secret = os.environ.get("CONFIG_SECRET", "")
    init_encryption(secret)

    conn = get_db(db_path)
    stats = {"migrated": 0, "skipped": 0}

    def _set(namespace: str, key: str, value):
        if not force and get_config(conn, namespace, key) is not None:
            stats["skipped"] += 1
            return
        set_config(conn, namespace, key, value, updated_by="yaml_migration")
        stats["migrated"] += 1

    # server
    if "server" in tech:
        for k, v in tech["server"].items():
            _set("server", k, v)

    # security
    if "security" in tech:
        for k, v in tech["security"].items():
            _set("security", k, v)

    # session
    if "session" in tech:
        for k, v in tech["session"].items():
            _set("session", k, v)

    # llm
    if "llm" in tech:
        for k, v in tech["llm"].items():
            _set("llm", k, v)

    # cache-related top-level keys → cache namespace
    for key in ("cache_dir", "cache_ttl_hours", "watch_interval_minutes"):
        if key in tech:
            _set("cache", key, tech[key])

    # db_path is bootstrap-only (env var), skip it

    # protected_endpoints → endpoints namespace
    if "protected_endpoints" in tech:
        for k, v in tech["protected_endpoints"].items():
            _set("endpoints", k, v)

    conn.commit()
    conn.close()
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    force = "--force" in sys.argv

    yaml_path = ROOT / "config.tech.yaml"
    db_path_str = os.environ.get("MEMCP_DB_PATH", "db/profile.db")
    db_path = Path(db_path_str)
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    result = migrate_yaml_to_db(yaml_path, db_path, force=force)
    logger.info("Migration complete: %s", result)
