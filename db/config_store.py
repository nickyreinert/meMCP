"""
db/config_store.py — DB-First Configuration Store
==================================================

CRUD operations for the `config`, `llm_prompts`, and `uploaded_files` tables.
The database is the single source of truth for ALL configuration — both
infrastructure (server, security, llm, session, cache, endpoints) and
content settings (sources, chat, metrics, i18n, identity).

Sensitive values (API keys) are Fernet-encrypted when CONFIG_SECRET is set.

Usage:
    from db.config_store import get_config, set_config, get_all_sources
    conn = get_db()
    sources = get_all_sources(conn)
"""

import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Encryption ───────────────────────────────────────────────────────────────

_fernet = None  # cryptography.fernet.Fernet instance or None

# (namespace, key) pairs whose values are stored encrypted
ENCRYPTED_KEYS = {
    ("llm", "groq_api_key"),
}


def init_encryption(secret: str) -> None:
    """Derive a Fernet key from CONFIG_SECRET. Call once on startup."""
    global _fernet
    if not secret:
        logger.warning("CONFIG_SECRET not set — sensitive config values stored plaintext")
        _fernet = None
        return
    try:
        from cryptography.fernet import Fernet
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        _fernet = Fernet(key)
    except ImportError:
        logger.error("cryptography package not installed — sensitive values stored plaintext")
        _fernet = None


def _encrypt_value(value: str) -> str:
    if _fernet is None:
        return value
    return _fernet.encrypt(value.encode()).decode()


def _decrypt_value(stored: str) -> str:
    if _fernet is None:
        return stored
    try:
        return _fernet.decrypt(stored.encode()).decode()
    except Exception:
        # Backward-compat: value was likely stored plaintext before encryption was enabled
        logger.debug("Config decryption failed — treating as plaintext")
        return stored

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Config CRUD ─────────────────────────────────────────────────────────────────

def get_config(conn, namespace: str, key: str) -> Any:
    """Get a single config value, JSON-decoded. Auto-decrypts sensitive keys."""
    row = conn.execute(
        "SELECT value FROM config WHERE namespace=? AND key=?",
        (namespace, key),
    ).fetchone()
    if not row:
        return None
    raw = row["value"]
    if (namespace, key) in ENCRYPTED_KEYS:
        raw = _decrypt_value(raw)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def get_config_section(conn, namespace: str) -> dict:
    """Get all keys in a namespace as a dict {key: decoded_value}. Auto-decrypts."""
    rows = conn.execute(
        "SELECT key, value FROM config WHERE namespace=?",
        (namespace,),
    ).fetchall()
    result = {}
    for row in rows:
        raw = row["value"]
        if (namespace, row["key"]) in ENCRYPTED_KEYS:
            raw = _decrypt_value(raw)
        try:
            result[row["key"]] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            result[row["key"]] = raw
    return result


def set_config(conn, namespace: str, key: str, value: Any,
               updated_by: str = "admin") -> None:
    """Set a config value (JSON-encodes it). Auto-encrypts sensitive keys. Upsert semantics."""
    json_val = json.dumps(value, ensure_ascii=False)
    encrypted = 0
    if (namespace, key) in ENCRYPTED_KEYS:
        json_val = _encrypt_value(json_val)
        encrypted = 1
    conn.execute(
        """INSERT INTO config (namespace, key, value, encrypted, updated_at, updated_by)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(namespace, key) DO UPDATE SET
               value=excluded.value,
               encrypted=excluded.encrypted,
               updated_at=excluded.updated_at,
               updated_by=excluded.updated_by""",
        (namespace, key, json_val, encrypted, _now_iso(), updated_by),
    )


def delete_config(conn, namespace: str, key: str) -> bool:
    """Delete a config entry. Returns True if a row was deleted."""
    cursor = conn.execute(
        "DELETE FROM config WHERE namespace=? AND key=?",
        (namespace, key),
    )
    return cursor.rowcount > 0


def config_section_exists(conn, namespace: str) -> bool:
    """Check if any rows exist for a namespace."""
    row = conn.execute(
        "SELECT 1 FROM config WHERE namespace=? LIMIT 1",
        (namespace,),
    ).fetchone()
    return row is not None


# ── Source-specific helpers ──────────────────────────────────────────────────────

def get_all_sources(conn) -> dict:
    """
    Reconstruct the old-style config dict from DB rows so scrapers need zero changes.

    Returns:
        {
            "identity": {...},
            "stages": {...},
            "oeuvre": {"github": {...}, "medium": {...}, ...},
            "i18n": {...}
        }
    """
    rows = conn.execute(
        "SELECT key, value FROM config WHERE namespace='sources'"
    ).fetchall()

    result: dict = {}
    for row in rows:
        key = row["key"]
        try:
            val = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue

        if key.startswith("oeuvre."):
            name = key[len("oeuvre."):]
            result.setdefault("oeuvre", {})[name] = val
        elif key == "stages":
            result["stages"] = val
        # identity source config (not the multilang data)
        elif key == "identity":
            result["identity"] = val

    return result


def list_sources_flat(conn) -> list[dict]:
    """
    List all source configs as a flat list for the admin UI.
    Returns list of dicts with: id, section, name, connector, enabled, url, etc.
    """
    rows = conn.execute(
        "SELECT key, value, updated_at, updated_by FROM config WHERE namespace='sources'"
    ).fetchall()

    sources = []
    for row in rows:
        key = row["key"]
        try:
            cfg = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue

        if key.startswith("oeuvre."):
            section = "oeuvre"
            name = key[len("oeuvre."):]
            source_id = f"oeuvre.{name}"
        elif key == "stages":
            section = "stages"
            name = "stages"
            source_id = "stages"
        elif key == "identity":
            section = "identity"
            name = "identity"
            source_id = "identity"
        else:
            continue

        sources.append({
            "id": source_id,
            "section": section,
            "name": name,
            "connector": cfg.get("connector"),
            "enabled": cfg.get("enabled", True),
            "url": cfg.get("url"),
            "sub_type_override": cfg.get("sub_type_override"),
            "llm_processing": cfg.get("llm-processing", False),
            "limit": cfg.get("limit", 0),
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        })

    return sources


# ── Prompt CRUD ──────────────────────────────────────────────────────────────────

def get_prompt(conn, prompt_id: str) -> Optional[dict]:
    """Get a single active prompt by ID. Returns None if not found or inactive."""
    row = conn.execute(
        "SELECT * FROM llm_prompts WHERE prompt_id=? AND is_active=1",
        (prompt_id,),
    ).fetchone()
    return dict(row) if row else None


def get_prompt_content(conn, prompt_id: str) -> Optional[str]:
    """Get just the content text of an active prompt. Returns None if missing."""
    row = conn.execute(
        "SELECT content FROM llm_prompts WHERE prompt_id=? AND is_active=1",
        (prompt_id,),
    ).fetchone()
    return row["content"] if row else None


def list_prompts(conn, category: str = None) -> list[dict]:
    """List all prompts, optionally filtered by category."""
    if category:
        rows = conn.execute(
            "SELECT * FROM llm_prompts WHERE category=? ORDER BY category, prompt_id",
            (category,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM llm_prompts ORDER BY category, prompt_id"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_prompt(conn, prompt_id: str, category: str, name: str,
                  content: str, updated_by: str = "admin") -> None:
    """Insert or update a prompt, auto-incrementing version on update."""
    ts = _now_iso()
    existing = conn.execute(
        "SELECT version FROM llm_prompts WHERE prompt_id=?", (prompt_id,)
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE llm_prompts SET category=?, name=?, content=?,
               version=version+1, updated_at=?, updated_by=?
               WHERE prompt_id=?""",
            (category, name, content, ts, updated_by, prompt_id),
        )
    else:
        conn.execute(
            """INSERT INTO llm_prompts
               (prompt_id, category, name, content, is_active, version,
                created_at, updated_at, updated_by)
               VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?)""",
            (prompt_id, category, name, content, ts, ts, updated_by),
        )


# ── File upload registry ─────────────────────────────────────────────────────────

def register_file(conn, filename: str, file_path: str, file_type: str,
                  size_bytes: int = None, linked_source: str = None,
                  uploaded_by: str = "admin") -> str:
    """Register an uploaded file. Returns the file ID."""
    file_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO uploaded_files
           (id, filename, file_path, file_type, size_bytes, linked_source,
            uploaded_at, uploaded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, filename, file_path, file_type, size_bytes,
         linked_source, _now_iso(), uploaded_by),
    )
    return file_id


def list_files(conn, linked_source: str = None) -> list[dict]:
    """List uploaded files, optionally filtered by linked source."""
    if linked_source:
        rows = conn.execute(
            "SELECT * FROM uploaded_files WHERE linked_source=? ORDER BY uploaded_at DESC",
            (linked_source,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM uploaded_files ORDER BY uploaded_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_file(conn, file_id: str) -> Optional[dict]:
    """Get a single uploaded file by ID."""
    row = conn.execute(
        "SELECT * FROM uploaded_files WHERE id=?", (file_id,)
    ).fetchone()
    return dict(row) if row else None


# ── Readiness check (foundation for setup wizard) ───────────────────────────

def check_config_readiness(conn) -> dict:
    """
    Check what config is present vs. missing.
    Foundation for a future setup wizard.

    Returns:
        {
            "ready": bool,
            "sections": {<namespace>: {"present": bool, "key_count": int}, ...},
            "warnings": [str, ...]
        }
    """
    all_sections = (
        "server", "security", "session", "llm", "cache", "endpoints",
        "sources", "chat", "metrics", "i18n", "identity",
    )

    sections = {}
    for ns in all_sections:
        section = get_config_section(conn, ns)
        sections[ns] = {
            "present": bool(section),
            "key_count": len(section),
        }

    warnings = []

    # LLM checks
    llm = get_config_section(conn, "llm")
    backend = llm.get("backend", "none")
    if backend == "none":
        warnings.append("LLM backend is 'none' — enrichment and translation disabled")
    elif backend == "groq" and not llm.get("groq_api_key"):
        warnings.append("LLM backend is groq but no API key configured")

    # Data checks
    if not config_section_exists(conn, "sources"):
        warnings.append("No data sources configured — add sources via admin UI")

    if not config_section_exists(conn, "identity"):
        warnings.append("No identity data configured — set up your profile in admin UI")

    i18n = get_config_section(conn, "i18n")
    if not i18n.get("target_languages"):
        warnings.append("No target languages configured — translations disabled")

    # Wizard needed when user hasn't configured identity or sources yet
    needs_wizard = (
        not sections.get("identity", {}).get("present", False)
        and not sections.get("sources", {}).get("present", False)
    )

    return {
        "ready": True,  # App can always start with bootstrap defaults
        "needs_wizard": needs_wizard,
        "sections": sections,
        "warnings": warnings,
    }
