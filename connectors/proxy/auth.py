"""
connectors/proxy/auth.py — SQLite session store with token encryption
======================================================================

Manages per-chat session state:
  - state:   needs_token | awaiting_token | active
  - token:   meMCP bearer token — stored Fernet-encrypted when PROXY_SECRET is set,
             plaintext otherwise (development mode)
  - history: JSON list of {role, content} for LLM conversation history

Call init_db(path, secret) once on startup.
Pass secret="" or None to disable encryption (tokens stored plaintext with a warning).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_conn: Optional[sqlite3.Connection] = None
_fernet = None  # cryptography.fernet.Fernet instance or None


# ── Encryption helpers ────────────────────────────────────────────────────────

def _init_encryption(secret: str) -> None:
    global _fernet
    if not secret:
        logger.warning(
            "PROXY_SECRET is not set — meMCP tokens will be stored plaintext in proxy.db"
        )
        _fernet = None
        return
    try:
        from cryptography.fernet import Fernet
        # Derive a 32-byte key from the secret (URL-safe base64-encoded, as Fernet requires)
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        _fernet = Fernet(key)
    except ImportError:
        logger.error("cryptography package not installed — tokens stored plaintext")
        _fernet = None


def _encrypt(value: str) -> str:
    if _fernet is None:
        return value
    return _fernet.encrypt(value.encode()).decode()


def _decrypt(stored: str) -> str:
    if _fernet is None:
        return stored
    try:
        return _fernet.decrypt(stored.encode()).decode()
    except Exception:
        # Backward-compat: if decryption fails the value was likely stored plaintext
        logger.debug("Token decryption failed — treating as plaintext")
        return stored


# ── DB init ───────────────────────────────────────────────────────────────────

def init_db(path: str, secret: str = "") -> None:
    """Open (or create) the SQLite database, ensure schema exists, init encryption."""
    global _conn
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            chat_id    TEXT PRIMARY KEY,
            token      TEXT,
            state      TEXT NOT NULL DEFAULT 'needs_token',
            history    TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
    """)
    _conn.commit()
    _init_encryption(secret)


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("auth.init_db() has not been called")
    return _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public API ─────────────────────────────────────────────────────────────────

def get_session(chat_id: str) -> Optional[dict]:
    """Return the session dict for *chat_id*, or None. Token is decrypted."""
    row = _db().execute(
        "SELECT chat_id, token, state, history, updated_at FROM sessions WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "chat_id":    row["chat_id"],
        "token":      _decrypt(row["token"]) if row["token"] else None,
        "state":      row["state"],
        "history":    json.loads(row["history"]),
        "updated_at": row["updated_at"],
    }


def upsert_session(
    chat_id: str,
    *,
    token: Optional[str] = None,
    state: Optional[str] = None,
    history: Optional[list] = None,
) -> dict:
    """
    Create or update a session for *chat_id*. Token is encrypted before storage.
    Only provided keyword arguments are updated; omitted ones keep existing values.
    Returns the final session dict (token decrypted).
    """
    existing = get_session(chat_id)
    encrypted_token = _encrypt(token) if token is not None else None

    if existing is None:
        _db().execute(
            "INSERT INTO sessions (chat_id, token, state, history, updated_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, encrypted_token, state or "needs_token", json.dumps(history or []), _now()),
        )
    else:
        updates: dict = {"updated_at": _now()}
        if token is not None:
            updates["token"] = encrypted_token
        if state is not None:
            updates["state"] = state
        if history is not None:
            updates["history"] = json.dumps(history)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        _db().execute(
            f"UPDATE sessions SET {set_clause} WHERE chat_id = ?",
            list(updates.values()) + [chat_id],
        )

    _db().commit()
    return get_session(chat_id)  # type: ignore[return-value]


def clear_session(chat_id: str) -> None:
    """Delete the session for *chat_id* entirely."""
    _db().execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))
    _db().commit()
