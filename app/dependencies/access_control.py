"""
app/dependencies/access_control.py — Tri-Stage Access Control
=============================================================

Implements a three-stage access system for the meMCP API:

  - Public:   No token supplied → metadata-only (tool/resource/prompt listings).
  - Private:  Valid Bearer token → full data access, all calls tracked.
  - Elevated: Valid Bearer token with tier='elevated' → all Private access plus
              Intelligence Hub tools (Groq/Perplexity LLM calls).

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

Require Elevated tier specifically::

    from app.dependencies.access_control import require_elevated_access

    @router.post("/mcp/intelligence/simulate_interview")
    async def simulate_interview(
        ...,
        token_info: TokenInfo = Depends(require_elevated_access),
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

import fnmatch
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, HTTPException, Request

from db.models import get_db, DB_PATH


# ── Stage constants ───────────────────────────────────────────────────────────

STAGE_PUBLIC   = "public"
STAGE_PRIVATE  = "private"
STAGE_ELEVATED = "elevated"


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
        ``STAGE_ELEVATED`` — path is in ``elevated_required`` list.
        ``STAGE_PRIVATE``  — path is in ``private_required`` list.
        ``None``           — not listed; no config-level restriction (public fallback).
    """
    if _path_matches_any(path, protected_config.get("elevated_required", [])):
        return STAGE_ELEVATED
    if _path_matches_any(path, protected_config.get("private_required", [])):
        return STAGE_PRIVATE
    return None


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class TokenInfo:
    """Validated token metadata returned by access dependencies."""
    id:         int
    owner_name: str
    stage:      str          # STAGE_PRIVATE | STAGE_ELEVATED
    # Per-token intelligence budget overrides (None = use global defaults from config.yaml)
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
    Look up *token_value* in the tokens table.

    Returns ``TokenInfo`` if the token exists, is active, and has not expired.
    Returns ``None`` otherwise (caller decides whether to raise 403 or not).
    """
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

    # Map DB tier value to stage constant
    db_tier = (row["tier"] or "private").lower()
    stage = STAGE_ELEVATED if db_tier == "elevated" else STAGE_PRIVATE

    return TokenInfo(
        id=row["id"],
        owner_name=row["owner_name"],
        stage=stage,
        max_tokens_per_session=row["max_tokens_per_session"],
        max_calls_per_day=row["max_calls_per_day"],
        max_input_chars=row["max_input_chars"],
        max_output_chars=row["max_output_chars"],
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


def require_elevated_access(
    token_info: TokenInfo = Depends(require_private_access),
) -> TokenInfo:
    """
    Dependency: raises HTTP 403 if the token does not have ELEVATED tier.

    Compose on top of ``require_private_access`` so that the basic token
    validation (existence, expiry, activity) is always checked first.

    Inject as ``token_info: TokenInfo = Depends(require_elevated_access)``.

    To upgrade a token to the Elevated tier, use::

        python scripts/manage_tokens.py upgrade --id <token_id>
    """
    if token_info.stage != STAGE_ELEVATED:
        raise HTTPException(
            status_code=403,
            detail={
                "stage": token_info.stage,
                "required_stage": STAGE_ELEVATED,
                "message": (
                    "Access Restricted: this endpoint requires an Elevated-tier token. "
                    "Your current token has Private-tier access only. "
                    "To unlock Intelligence Hub tools (AI reasoning, live search, "
                    "interview simulation, market analysis), upgrade your token via: "
                    "python scripts/manage_tokens.py upgrade --id <token_id>"
                ),
            },
        )
    return token_info


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
    - Path in ``elevated_required`` → validates that a valid Elevated-tier token
      is present; returns HTTP 403 otherwise.
    - Path in ``private_required``  → validates any valid token; HTTP 403 if absent.
    - Path not listed               → passes through (public, no config restriction).

    Existing route-level dependencies (``require_private_access`` etc.) continue
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
                STAGE_PUBLIC,
                (
                    "Access Restricted: this endpoint requires a valid token. "
                    "Provide it via 'Authorization: Bearer <token>' "
                    "or the '?token=<token>' query parameter."
                ),
            )

        conn = get_db(DB_PATH)
        try:
            token_info = _validate_token(conn, raw_token)

            if not token_info:
                return _forbidden_json(
                    STAGE_PUBLIC,
                    "Access Restricted: token is invalid, expired, or revoked.",
                )

            if required == STAGE_ELEVATED and token_info.stage != STAGE_ELEVATED:
                return _forbidden_json(
                    token_info.stage,
                    (
                        "Access Restricted: this endpoint requires an Elevated-tier token. "
                        "Your current token has Private-tier access only."
                    ),
                )

            # Log the access — the only logging point for routes that have no
            # require_private_access dependency (most routes in main.py).
            try:
                qp = {k: v for k, v in request.query_params.items() if k != "token"}
                log_usage(conn, token_info.id, path, qp or None)
            except Exception:
                pass
        finally:
            conn.close()

        return await call_next(request)

    return _guard
