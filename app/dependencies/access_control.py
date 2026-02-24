"""
app/dependencies/access_control.py — Three-Tier Access Control
===============================================================

Implements a three-tier access system for the meMCP API:

  - Anonymous: No token supplied → metadata-only (tool/resource/prompt listings).
  - MCP:       Valid Bearer token with tier='mcp' → full data access, all calls tracked.
  - Chat:      Valid Bearer token with tier='chat' → chat proxy authentication only;
               proxy derives scoped MCP tokens for actual API calls.

Token lookup (checked in order):
  1. Authorization: Bearer <token>  header
  2. ?token=<value>                 query parameter (DEPRECATED — use header)

Usage
-----
Protect an endpoint that requires a valid token::

    from app.dependencies.access_control import require_mcp_access, TokenInfo

    @router.post("/mcp/tools/call")
    async def call_tool(
        ...,
        token_info: TokenInfo = Depends(require_mcp_access),
    ):
        ...

Optionally inspect the stage without hard-blocking::

    from app.dependencies.access_control import get_access_stage, TokenInfo

    @router.get("/mcp/tools")
    async def list_tools(
        token_info: TokenInfo | None = Depends(get_access_stage),
    ):
        has_token = token_info is not None

Log extra body args from a POST handler (one explicit call per protected POST)::

    from app.dependencies.access_control import log_usage

    log_usage(conn, token_info.id, request.url.path, input_args=tool_request)
"""

import fnmatch
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, HTTPException, Request

from db.models import get_db, DB_PATH

logger = logging.getLogger(__name__)


# ── Tier constants ────────────────────────────────────────────────────────────

STAGE_ANONYMOUS = "anonymous"
STAGE_MCP       = "mcp"
STAGE_CHAT      = "chat"

# Backward-compat aliases (used internally for mapping old DB values)
STAGE_PUBLIC   = STAGE_ANONYMOUS
STAGE_PRIVATE  = STAGE_MCP
STAGE_ELEVATED = STAGE_MCP


# ── Path-matching helpers ─────────────────────────────────────────────────────

def _normalize_path_pattern(pattern: str) -> str:
    """Convert FastAPI-style ``{param}`` placeholders to fnmatch wildcards."""
    return re.sub(r"\{[^}]+\}", "*", pattern)


def _path_matches_any(path: str, patterns: List[str]) -> bool:
    """Return True if *path* matches any pattern in *patterns*."""
    for pattern in patterns:
        if fnmatch.fnmatch(path, _normalize_path_pattern(pattern)):
            return True
    return False


def get_required_level(path: str, protected_config: dict) -> Optional[str]:
    """
    Determine the required access level for *path* from the
    ``protected_endpoints`` block in config.yaml.

    Returns:
        ``STAGE_MCP``  — path is in ``mcp_required`` (or legacy ``private_required``) list.
        ``None``       — not listed; no config-level restriction (anonymous fallback).
    """
    # Support both new key (mcp_required) and legacy key (private_required)
    mcp_paths = (
        protected_config.get("mcp_required", [])
        or protected_config.get("private_required", [])
    )
    if _path_matches_any(path, mcp_paths):
        return STAGE_MCP
    return None


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class TokenInfo:
    """Validated token metadata returned by access dependencies."""
    id:         int
    owner_name: str
    stage:      str          # STAGE_MCP | STAGE_CHAT
    # Per-token budget overrides (None = use global defaults from config.yaml)
    max_tokens_per_session:  Optional[int] = None
    max_calls_per_day:       Optional[int] = None
    max_input_chars:         Optional[int] = None
    max_output_chars:        Optional[int] = None


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
    Look up *token_value* in both the ``tokens`` and ``derived_tokens`` tables.

    For derived tokens, also verifies that the parent token is still active.

    Returns ``TokenInfo`` if the token exists, is active, and has not expired.
    Returns ``None`` otherwise (caller decides whether to raise 403 or not).
    """
    now = datetime.now(timezone.utc)

    # ── 1. Check regular tokens table (hashed lookup, with plaintext fallback)
    token_hash = hashlib.sha256(token_value.encode()).hexdigest()
    row = conn.execute(
        """
        SELECT id, owner_name, expires_at, is_active,
               tier, max_tokens_per_session, max_calls_per_day,
               max_input_chars, max_output_chars
        FROM tokens
        WHERE token_value = ?
        """,
        (token_hash,),
    ).fetchone()

    # Fallback: try plaintext lookup for legacy un-hashed tokens
    if not row:
        row = conn.execute(
            """
            SELECT id, owner_name, expires_at, is_active,
                   tier, max_tokens_per_session, max_calls_per_day,
                   max_input_chars, max_output_chars
            FROM tokens
            WHERE token_value = ?
            """,
            (token_value,),
        ).fetchone()

    if row:
        if not row["is_active"]:
            return None

        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return None

        # Map DB tier value to stage constant (with backward compat)
        db_tier = (row["tier"] or "mcp").lower()
        _TIER_MAP = {
            "mcp": STAGE_MCP,
            "chat": STAGE_CHAT,
            "private": STAGE_MCP,
            "elevated": STAGE_MCP,
        }
        stage = _TIER_MAP.get(db_tier, STAGE_MCP)

        return TokenInfo(
            id=row["id"],
            owner_name=row["owner_name"],
            stage=stage,
            max_tokens_per_session=row["max_tokens_per_session"],
            max_calls_per_day=row["max_calls_per_day"],
            max_input_chars=row["max_input_chars"],
            max_output_chars=row["max_output_chars"],
        )

    # ── 2. Check derived tokens table (token stored as SHA-256 hash) ─────────
    hashed = hashlib.sha256(token_value.encode()).hexdigest()
    drow = conn.execute(
        """
        SELECT d.id, d.parent_token_id, d.scope, d.expires_at, d.is_active,
               t.owner_name, t.is_active AS parent_active, t.expires_at AS parent_expires
        FROM derived_tokens d
        JOIN tokens t ON t.id = d.parent_token_id
        WHERE d.token_value = ?
        """,
        (hashed,),
    ).fetchone()

    if not drow:
        return None

    if not drow["is_active"]:
        return None

    # Check derived token expiry
    try:
        d_expires = datetime.fromisoformat(drow["expires_at"])
    except ValueError:
        return None
    if d_expires.tzinfo is None:
        d_expires = d_expires.replace(tzinfo=timezone.utc)
    if now > d_expires:
        return None

    # Also verify parent token is still valid
    if not drow["parent_active"]:
        return None
    try:
        p_expires = datetime.fromisoformat(drow["parent_expires"])
    except ValueError:
        return None
    if p_expires.tzinfo is None:
        p_expires = p_expires.replace(tzinfo=timezone.utc)
    if now > p_expires:
        return None

    # Derived tokens always get MCP stage (they are scoped for API access)
    return TokenInfo(
        id=drow["parent_token_id"],
        owner_name=drow["owner_name"],
        stage=STAGE_MCP,
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
        logger.warning(
            "Token supplied via query parameter (deprecated) for %s — "
            "use Authorization: Bearer header instead",
            request.url.path,
        )
        request.state._token_via_query_param = True
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
                        "stage": STAGE_ANONYMOUS,
                        "message": (
                            "Access Restricted: this endpoint requires a valid token. "
                            "Provide it via 'Authorization: Bearer <token>' header."
                        ),
                    },
                )
            return None  # Anonymous stage — caller handles gracefully

        # ── Token provided — validate ────────────────────────────────────────
        token_info = _validate_token(conn, raw_token)
        if not token_info:
            raise HTTPException(
                status_code=403,
                detail={
                    "stage": STAGE_ANONYMOUS,
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

require_mcp_access = _AccessGate(require_token=True)
"""
Dependency: raises HTTP 403 if no valid token is provided.
Inject as ``token_info: TokenInfo = Depends(require_mcp_access)``.
"""

# Backward-compat alias — existing code that imports require_private_access
# will continue to work without changes.
require_private_access = require_mcp_access

get_access_stage = _AccessGate(require_token=False)
"""
Dependency: returns ``None`` (anonymous) or ``TokenInfo`` (mcp/chat).
Raises HTTP 403 only when a token IS present but is invalid/expired.
Inject as ``token_info: TokenInfo | None = Depends(get_access_stage)``.
"""


# ── Config-driven middleware guard ────────────────────────────────────────────

def _forbidden_json(stage: str, message: str):
    """Return a plain 403 JSONResponse (no exception raised)."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=403,
        content={"detail": {"stage": stage, "message": message}},
    )


def build_endpoint_guard(protected_config: dict):
    """
    Build an async HTTP middleware function that enforces config-driven access
    control based on the ``protected_endpoints`` block from config.yaml.

    Behaviour
    ---------
    - Path in ``mcp_required`` (or legacy ``private_required``) → validates
      that a valid token is present; returns HTTP 403 if absent.
    - Path not listed → passes through (anonymous, no config restriction).

    Existing route-level dependencies (``require_mcp_access`` etc.) continue
    to apply after this middleware; the guard acts as an early-exit layer only.

    Usage in main.py::

        guard = build_endpoint_guard(CONFIG.get("protected_endpoints", {}))

        @app.middleware("http")
        async def endpoint_protection_middleware(request, call_next):
            return await guard(request, call_next)
    """
    async def _guard(request: Request, call_next):
        path = request.url.path
        required = get_required_level(path, protected_config)

        # Not listed in config → no restriction from this layer
        if required is None:
            return await call_next(request)

        raw_token = _extract_raw_token(request)
        if not raw_token:
            return _forbidden_json(
                STAGE_ANONYMOUS,
                (
                    "Access Restricted: this endpoint requires a valid token. "
                    "Provide it via 'Authorization: Bearer <token>' header."
                ),
            )

        conn = get_db(DB_PATH)
        try:
            token_info = _validate_token(conn, raw_token)

            if not token_info:
                return _forbidden_json(
                    STAGE_ANONYMOUS,
                    "Access Restricted: token is invalid, expired, or revoked.",
                )

            # Log the access — the only logging point for routes that have no
            # require_mcp_access dependency (most routes in main.py).
            try:
                qp = {k: v for k, v in request.query_params.items() if k != "token"}
                log_usage(conn, token_info.id, path, qp or None)
            except Exception:
                pass
        finally:
            conn.close()

        response = await call_next(request)

        # Add deprecation header when token was supplied via query parameter
        if getattr(request.state, "_token_via_query_param", False):
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = "2026-06-01"
            response.headers["X-Deprecation-Notice"] = (
                "Query parameter token (?token=...) is deprecated. "
                "Use Authorization: Bearer <token> header instead."
            )

        return response

    return _guard
