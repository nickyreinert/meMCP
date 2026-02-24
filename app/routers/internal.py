"""
app/routers/internal.py — Internal / Token Management Endpoints
================================================================

Endpoints:
  GET  /token/info                   → Token metadata (tier, owner, expiry)
  POST /internal/tokens/derive       → Create a scoped, short-lived derived token

Security:
  /token/info           — requires a valid Bearer token (any tier)
  /internal/tokens/*    — requires X-Proxy-Secret header (proxy-only)
"""

import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from app.dependencies.access_control import (
    require_mcp_access, TokenInfo, STAGE_CHAT, STAGE_MCP,
    _extract_raw_token, _validate_token, _get_db_conn,
)
from db.models import get_db, DB_PATH

router = APIRouter(tags=["Internal"])

# ── Proxy secret (loaded once from env at import time) ──────────────────────

import os as _os
_PROXY_SECRET = _os.getenv("PROXY_SECRET", "")


# ── Models ──────────────────────────────────────────────────────────────────

class DeriveRequest(BaseModel):
    parent_token: str
    scope: str = "mcp_read"
    ttl_minutes: int = 60
    chat_id: Optional[str] = None


class DeriveResponse(BaseModel):
    derived_token: str
    expires_at: str
    scope: str


# ── Token info endpoint ─────────────────────────────────────────────────────

@router.get("/token/info", summary="Get token metadata")
async def token_info(
    token_info: TokenInfo = Depends(require_mcp_access),
):
    """
    Returns metadata about the authenticated token.
    Useful for the proxy to verify what tier a token has.
    """
    return {
        "status": "success",
        "data": {
            "tier": token_info.stage,
            "owner": token_info.owner_name,
            "token_id": token_info.id,
        },
    }


# ── Internal derive endpoint ────────────────────────────────────────────────

def _verify_proxy_secret(x_proxy_secret: str = Header("")) -> None:
    """Reject requests without valid proxy secret."""
    if not _PROXY_SECRET:
        raise HTTPException(503, "PROXY_SECRET not configured on server")
    if x_proxy_secret != _PROXY_SECRET:
        raise HTTPException(401, "Invalid or missing X-Proxy-Secret header")


@router.post(
    "/internal/tokens/derive",
    response_model=DeriveResponse,
    summary="Create a scoped derived token (proxy-only)",
)
async def derive_token(
    req: DeriveRequest,
    x_proxy_secret: str = Header(""),
    conn=Depends(_get_db_conn),
):
    """
    Create a short-lived, scoped token derived from a parent chat-tier token.

    Called by the chat proxy after a user authenticates. The derived token
    is used for MCP API calls instead of the original chat token.

    Requires X-Proxy-Secret header (same secret the bot adapters use).
    """
    _verify_proxy_secret(x_proxy_secret)

    # Validate parent token
    parent_info = _validate_token(conn, req.parent_token)
    if not parent_info:
        raise HTTPException(403, "Parent token is invalid, expired, or revoked")

    if parent_info.stage != STAGE_CHAT:
        raise HTTPException(
            403,
            f"Parent token must be 'chat' tier (got '{parent_info.stage}'). "
            "Use 'mcp' tokens directly for API access.",
        )

    # Generate derived token
    raw_token = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=req.ttl_minutes)
    ).isoformat()
    created_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO derived_tokens
            (token_value, parent_token_id, scope, expires_at, created_at, chat_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (hashed, parent_info.id, req.scope, expires_at, created_at, req.chat_id),
    )
    conn.commit()

    return DeriveResponse(
        derived_token=raw_token,
        expires_at=expires_at,
        scope=req.scope,
    )


# ── Internal revoke endpoint ────────────────────────────────────────────────

class RevokeRequest(BaseModel):
    derived_token: str


@router.post(
    "/internal/tokens/revoke",
    summary="Revoke a derived token (proxy-only)",
)
async def revoke_derived_token(
    req: RevokeRequest,
    x_proxy_secret: str = Header(""),
    conn=Depends(_get_db_conn),
):
    """Revoke a derived token by its raw value."""
    _verify_proxy_secret(x_proxy_secret)

    hashed = hashlib.sha256(req.derived_token.encode()).hexdigest()
    result = conn.execute(
        "UPDATE derived_tokens SET is_active = 0 WHERE token_value = ?",
        (hashed,),
    )
    conn.commit()

    if result.rowcount == 0:
        raise HTTPException(404, "Derived token not found")

    return {"status": "success", "message": "Derived token revoked"}
