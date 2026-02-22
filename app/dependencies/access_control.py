"""
app/dependencies/access_control.py — Tri-Stage Access Control
=============================================================

Implements a three-stage access system for the meMCP API:

  - Public:   No token supplied → metadata-only (tool/resource/prompt listings).
  - Private:  Valid Bearer token → full data access, all calls tracked.
  - Elevated: Reserved for future LLM features (same token mechanism, extended later).

Token lookup (checked in order):
  1. Authorization: Bearer <token>  header
  2. ?token=<value>                 query parameter

Usage
-----
Protect an endpoint that requires a valid token::

    from app.dependencies.access_control import require_private_access, TokenInfo

    @router.post("/mcp/tools/call")
    async def call_tool(
        ...,
        token_info: TokenInfo = Depends(require_private_access),
    ):
        ...

Optionally inspect the stage without hard-blocking::

    from app.dependencies.access_control import get_access_stage, TokenInfo

    @router.get("/mcp/tools")
    async def list_tools(
        token_info: TokenInfo | None = Depends(get_access_stage),
    ):
        is_private = token_info is not None

Log extra body args from a POST handler (one explicit call per protected POST)::

    from app.dependencies.access_control import log_usage

    log_usage(conn, token_info.id, request.url.path, input_args=tool_request)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request

from db.models import get_db, DB_PATH


# ── Stage constants ───────────────────────────────────────────────────────────

STAGE_PUBLIC   = "public"
STAGE_PRIVATE  = "private"
STAGE_ELEVATED = "elevated"  # Reserved — hook in later when LLM features arrive


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class TokenInfo:
    """Validated token metadata returned by access dependencies."""
    id:         int
    owner_name: str
    stage:      str   # STAGE_PRIVATE | STAGE_ELEVATED


# ── Internal DB helpers ───────────────────────────────────────────────────────

def _get_db_conn():
    """Scoped DB connection for access-control dependency."""
    conn = get_db(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def _validate_token(conn, token_value: str) -> Optional[TokenInfo]:
    """
    Look up *token_value* in the tokens table.

    Returns ``TokenInfo`` if the token exists, is active, and has not expired.
    Returns ``None`` otherwise (caller decides whether to raise 403 or not).
    """
    row = conn.execute(
        """
        SELECT id, owner_name, expires_at, is_active
        FROM tokens
        WHERE token_value = ?
        """,
        (token_value,),
    ).fetchone()

    if not row:
        return None

    if not row["is_active"]:
        return None

    # Parse expires_at; treat naive datetimes as UTC
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None  # Malformed date → treat as invalid

    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        return None

    return TokenInfo(
        id=row["id"],
        owner_name=row["owner_name"],
        stage=STAGE_PRIVATE,  # Elevated stage differentiation added later
    )


def log_usage(
    conn,
    token_id: int,
    endpoint: str,
    input_args: Optional[dict] = None,
) -> None:
    """
    Write one row to ``usage_logs``.

    Call this from route handlers to attach body args (e.g. POST tool calls).
    The ``require_private_access`` dependency already logs the basic endpoint
    hit; this function is only needed when you also want to record the parsed
    request body.
    """
    conn.execute(
        """
        INSERT INTO usage_logs (token_id, endpoint_called, timestamp, input_args)
        VALUES (?, ?, ?, ?)
        """,
        (
            token_id,
            endpoint,
            datetime.now(timezone.utc).isoformat(),
            json.dumps(input_args, ensure_ascii=False) if input_args is not None else None,
        ),
    )
    conn.commit()


# ── Token extraction ──────────────────────────────────────────────────────────

def _extract_raw_token(request: Request) -> Optional[str]:
    """
    Pull the raw token string from the request.

    Priority:
      1. ``Authorization: Bearer <token>`` header
      2. ``?token=<value>`` query parameter
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        candidate = auth[len("Bearer "):].strip()
        if candidate:
            return candidate

    param = request.query_params.get("token", "").strip()
    if param:
        return param

    return None


# ── Dependency factory ────────────────────────────────────────────────────────

class _AccessGate:
    """
    Callable FastAPI dependency that validates access tokens.

    Parameters
    ----------
    require_token:
        ``True``  → raises HTTP 403 when no token / invalid token (private gate).
        ``False`` → returns ``None`` for public access, raises 403 only for
                    tokens that are present but invalid (optional gate).
    """

    def __init__(self, require_token: bool) -> None:
        self._require = require_token

    def __call__(
        self,
        request: Request,
        conn=Depends(_get_db_conn),
    ) -> Optional[TokenInfo]:
        raw_token = _extract_raw_token(request)

        # ── No token provided ────────────────────────────────────────────────
        if not raw_token:
            if self._require:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "stage": STAGE_PUBLIC,
                        "message": (
                            "Access Restricted: this endpoint requires a valid token. "
                            "Provide it via 'Authorization: Bearer <token>' "
                            "or the '?token=<token>' query parameter."
                        ),
                    },
                )
            return None  # Public stage — caller handles gracefully

        # ── Token provided — validate ────────────────────────────────────────
        token_info = _validate_token(conn, raw_token)
        if not token_info:
            raise HTTPException(
                status_code=403,
                detail={
                    "stage": STAGE_PUBLIC,
                    "message": "Access Restricted: token is invalid, expired, or revoked.",
                },
            )

        # ── Log the access (query params captured; body args logged separately) ─
        try:
            qp = dict(request.query_params)
            qp.pop("token", None)  # Strip the token itself from logged args
            log_usage(conn, token_info.id, request.url.path, qp or None)
        except Exception:
            pass  # Never let logging failure block the actual request

        return token_info


# ── Public dependency instances ───────────────────────────────────────────────

require_private_access = _AccessGate(require_token=True)
"""
Dependency: raises HTTP 403 if no valid token is provided.
Inject as ``token_info: TokenInfo = Depends(require_private_access)``.
"""

get_access_stage = _AccessGate(require_token=False)
"""
Dependency: returns ``None`` (public) or ``TokenInfo`` (private/elevated).
Raises HTTP 403 only when a token IS present but is invalid/expired.
Inject as ``token_info: TokenInfo | None = Depends(get_access_stage)``.
"""
