# Admin Router — DB-First Architecture
# All config (sources, prompts, chat, metrics, identity, i18n) stored in DB.
# YAML files serve as initial seed only.

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from typing import Optional
import hashlib
import json
import logging
import os
import secrets
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pydantic import BaseModel

from admin.dependencies.access_control import get_current_admin_user
from db.models import (
    get_db, DB_PATH, list_entities, list_all_tags, list_tag_metrics,
    get_entity, update_entity, update_entity_tags, delete_entity,
)
from db.config_store import (
    get_config, get_config_section, set_config, delete_config,
    config_section_exists, get_all_sources, list_sources_flat,
    get_prompt, get_prompt_content, list_prompts, upsert_prompt,
    register_file, list_files,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent

# --- Pydantic models ---


class TokenCreate(BaseModel):
    owner: str
    days: int = 30
    tier: str = "mcp"


class TokenBudget(BaseModel):
    max_tokens_per_session: Optional[int] = None
    max_calls_per_day: Optional[int] = None
    max_input_chars: Optional[int] = None
    max_output_chars: Optional[int] = None


class SourceConfig(BaseModel):
    connector: Optional[str] = None
    enabled: bool = True
    url: Optional[str] = None
    sub_type_override: Optional[str] = None
    limit: int = 0
    llm_processing: bool = True
    fetch_readmes: Optional[bool] = None
    fetch_content: Optional[bool] = None
    cache_ttl_hours: Optional[int] = None
    single_entity: Optional[bool] = None
    connector_setup: Optional[dict] = None


class ScrapeRequest(BaseModel):
    source: Optional[str] = None
    force: bool = False
    disable_llm: bool = False
    llm_only: bool = False
    export_yaml: bool = False


class EntityUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    flavor: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    date: Optional[str] = None
    is_current: Optional[bool] = None
    visibility: Optional[str] = None
    raw_data: Optional[dict] = None


class EntityTags(BaseModel):
    technologies: list[str] = []
    skills: list[str] = []
    tags: list[str] = []


class PromptUpdate(BaseModel):
    content: str
    name: Optional[str] = None
    category: Optional[str] = None


class McpPromptCreate(BaseModel):
    id: str
    name: str
    description: str
    use_case: str
    prompt_template: str


class McpPromptUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    use_case: Optional[str] = None
    prompt_template: Optional[str] = None


class ChatConfig(BaseModel):
    host: Optional[str] = None
    model: Optional[str] = None
    ollama_url: Optional[str] = None
    rate_limit_per_minute: Optional[int] = None
    max_history: Optional[int] = None
    max_input_chars: Optional[int] = None
    max_output_chars: Optional[int] = None
    persona: Optional[dict] = None
    starters: Optional[list] = None


class I18nConfig(BaseModel):
    target_languages: Optional[list[str]] = None
    batch_sleep_seconds: Optional[float] = None


# --- In-memory job tracking ---

_running_jobs: dict[str, dict] = {}


# --- Helpers ---

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db_conn():
    return get_db(DB_PATH)


# --- ROUTER SETUP ---

router = APIRouter()


@router.get("/")
async def admin_root():
    """Admin root — health check (no auth required for Docker healthcheck)."""
    return {"message": "MCP Admin Backend", "status": "active"}


# ============================================================================
# LOG MANAGEMENT
# ============================================================================

@router.get("/logs")
async def get_logs(_user: dict = Depends(get_current_admin_user)):
    log_dir = ROOT / "logs"
    if not log_dir.exists():
        return {"logs": []}
    files = []
    for filename in sorted(os.listdir(log_dir)):
        filepath = log_dir / filename
        if filepath.is_file():
            stat = filepath.stat()
            files.append({
                "name": filename,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {"logs": files}


@router.get("/logs/{filename}")
async def download_log(filename: str, _user: dict = Depends(get_current_admin_user)):
    log_dir = ROOT / "logs"
    filepath = log_dir / filename
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    content = filepath.read_text(errors="replace")
    return {"filename": filename, "content": content}


# ============================================================================
# TOKEN MANAGEMENT
# ============================================================================

@router.get("/tokens")
async def list_tokens(_user: dict = Depends(get_current_admin_user)):
    conn = _get_db_conn()
    try:
        rows = conn.execute("""
            SELECT
                t.id, t.owner_name, t.expires_at, t.is_active, t.created_at,
                t.tier,
                t.max_tokens_per_session, t.max_calls_per_day,
                t.max_input_chars, t.max_output_chars,
                COUNT(u.id) AS call_count
            FROM tokens t
            LEFT JOIN usage_logs u ON u.token_id = t.id
            GROUP BY t.id
            ORDER BY t.id
        """).fetchall()

        tokens = []
        now = datetime.now(timezone.utc)
        for row in rows:
            expires = datetime.fromisoformat(row["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if not row["is_active"]:
                token_status = "revoked"
            elif now > expires:
                token_status = "expired"
            else:
                token_status = "active"
            tokens.append({
                "id": row["id"],
                "owner_name": row["owner_name"],
                "tier": row["tier"] or "mcp",
                "status": token_status,
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
                "call_count": row["call_count"],
                "budget": {
                    "max_tokens_per_session": row["max_tokens_per_session"],
                    "max_calls_per_day": row["max_calls_per_day"],
                    "max_input_chars": row["max_input_chars"],
                    "max_output_chars": row["max_output_chars"],
                },
            })
        return {"tokens": tokens, "count": len(tokens)}
    finally:
        conn.close()


@router.post("/tokens")
async def create_token(body: TokenCreate, _user: dict = Depends(get_current_admin_user)):
    if body.tier not in ("mcp", "chat"):
        raise HTTPException(status_code=400, detail="tier must be 'mcp' or 'chat'")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=body.days)).isoformat()
    created = _now_utc()
    conn = _get_db_conn()
    try:
        cur = conn.execute(
            "INSERT INTO tokens (token_value, owner_name, expires_at, created_at, tier) "
            "VALUES (?, ?, ?, ?, ?)",
            (token_hash, body.owner, expires, created, body.tier),
        )
        conn.commit()
        token_id = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Created token #{token_id} for {body.owner} (tier={body.tier}, days={body.days})")
    return {
        "token_id": token_id,
        "token": token,
        "owner": body.owner,
        "tier": body.tier,
        "expires_at": expires,
        "created_at": created,
    }


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: int, _user: dict = Depends(get_current_admin_user)):
    conn = _get_db_conn()
    try:
        row = conn.execute("SELECT id, owner_name FROM tokens WHERE id = ?", (token_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Token {token_id} not found")
        conn.execute("UPDATE tokens SET is_active = 0 WHERE id = ?", (token_id,))
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Revoked token #{token_id} ({row['owner_name']})")
    return {"message": f"Token {token_id} revoked", "owner": row["owner_name"]}


@router.put("/tokens/{token_id}/budget")
async def update_token_budget(
    token_id: int, body: TokenBudget,
    _user: dict = Depends(get_current_admin_user),
):
    conn = _get_db_conn()
    try:
        row = conn.execute("SELECT id, owner_name FROM tokens WHERE id = ?", (token_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Token {token_id} not found")
        updates = []
        if body.max_tokens_per_session is not None:
            updates.append(("max_tokens_per_session", body.max_tokens_per_session))
        if body.max_calls_per_day is not None:
            updates.append(("max_calls_per_day", body.max_calls_per_day))
        if body.max_input_chars is not None:
            updates.append(("max_input_chars", body.max_input_chars))
        if body.max_output_chars is not None:
            updates.append(("max_output_chars", body.max_output_chars))
        if not updates:
            brow = conn.execute(
                "SELECT tier, max_tokens_per_session, max_calls_per_day, "
                "max_input_chars, max_output_chars FROM tokens WHERE id = ?",
                (token_id,),
            ).fetchone()
            return {
                "token_id": token_id, "owner": row["owner_name"],
                "budget": dict(brow), "message": "No changes — showing current budget",
            }
        set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
        values = [v for _, v in updates] + [token_id]
        conn.execute(f"UPDATE tokens SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()
    return {"token_id": token_id, "owner": row["owner_name"], "updated": {col: val for col, val in updates}}


@router.get("/tokens/{token_id}/stats")
async def get_token_stats(token_id: int, _user: dict = Depends(get_current_admin_user)):
    conn = _get_db_conn()
    try:
        token = conn.execute(
            "SELECT id, owner_name, expires_at, is_active, tier, "
            "max_tokens_per_session, max_calls_per_day, max_input_chars, max_output_chars "
            "FROM tokens WHERE id = ?", (token_id,),
        ).fetchone()
        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_id} not found")
        logs = conn.execute(
            "SELECT endpoint_called, timestamp, input_args, api_provider, input_text, tokens_used "
            "FROM usage_logs WHERE token_id = ? ORDER BY timestamp DESC LIMIT 50",
            (token_id,),
        ).fetchall()
    finally:
        conn.close()

    freq: dict[str, int] = {}
    for log in logs:
        ep = log["endpoint_called"]
        freq[ep] = freq.get(ep, 0) + 1
    recent = [{
        "endpoint": log["endpoint_called"],
        "timestamp": log["timestamp"],
        "api_provider": log["api_provider"],
        "tokens_used": log["tokens_used"],
        "input_preview": (log["input_text"] or log["input_args"] or "")[:100],
    } for log in logs[:20]]

    expires = datetime.fromisoformat(token["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if not token["is_active"]:
        token_status = "revoked"
    elif now > expires:
        token_status = "expired"
    else:
        token_status = "active"

    return {
        "token_id": token["id"], "owner": token["owner_name"],
        "status": token_status, "tier": token["tier"] or "mcp",
        "expires_at": token["expires_at"],
        "budget": {
            "max_tokens_per_session": token["max_tokens_per_session"],
            "max_calls_per_day": token["max_calls_per_day"],
            "max_input_chars": token["max_input_chars"],
            "max_output_chars": token["max_output_chars"],
        },
        "total_logged_calls": len(logs),
        "endpoint_breakdown": freq,
        "recent_requests": recent,
    }


# ============================================================================
# DATABASE BROWSER + ENTITY EDITING
# ============================================================================

@router.get("/db")
async def browse_database(
    flavor: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user: dict = Depends(get_current_admin_user),
):
    conn = _get_db_conn()
    try:
        tags = [tag] if tag else None
        entities = list_entities(conn, flavor=flavor, category=category, source=source,
                                 search=search, tags=tags, limit=limit, offset=offset)
        return {"entities": entities, "count": len(entities), "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.get("/db/stats")
async def database_stats(_user: dict = Depends(get_current_admin_user)):
    conn = _get_db_conn()
    try:
        flavor_counts = conn.execute(
            "SELECT flavor, COUNT(*) as count FROM entities GROUP BY flavor ORDER BY flavor"
        ).fetchall()
        category_counts = conn.execute(
            "SELECT flavor, category, COUNT(*) as count FROM entities "
            "WHERE category IS NOT NULL GROUP BY flavor, category ORDER BY flavor, category"
        ).fetchall()
        tag_counts = conn.execute(
            "SELECT tag_type, COUNT(DISTINCT tag) as unique_tags, COUNT(*) as total_assignments "
            "FROM tags GROUP BY tag_type ORDER BY tag_type"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) as count FROM entities").fetchone()["count"]
        token_count = conn.execute("SELECT COUNT(*) as count FROM tokens").fetchone()["count"]
        active_tokens = conn.execute(
            "SELECT COUNT(*) as count FROM tokens WHERE is_active = 1"
        ).fetchone()["count"]
        return {
            "total_entities": total,
            "by_flavor": {r["flavor"]: r["count"] for r in flavor_counts},
            "by_category": [
                {"flavor": r["flavor"], "category": r["category"], "count": r["count"]}
                for r in category_counts
            ],
            "tags": [
                {"tag_type": r["tag_type"], "unique_tags": r["unique_tags"],
                 "total_assignments": r["total_assignments"]}
                for r in tag_counts
            ],
            "tokens": {"total": token_count, "active": active_tokens},
        }
    finally:
        conn.close()


@router.get("/db/tags")
async def list_tags_endpoint(
    tag_type: Optional[str] = Query(None),
    _user: dict = Depends(get_current_admin_user),
):
    conn = _get_db_conn()
    try:
        tags = list_all_tags(conn, tag_type=tag_type)
        return {"tags": tags, "count": len(tags)}
    finally:
        conn.close()


@router.get("/db/metrics")
async def get_metrics(
    tag_type: Optional[str] = Query(None),
    order_by: str = Query("relevance_score"),
    limit: int = Query(100, ge=1, le=500),
    _user: dict = Depends(get_current_admin_user),
):
    conn = _get_db_conn()
    try:
        metrics = list_tag_metrics(conn, tag_type=tag_type, order_by=order_by, limit=limit)
        return {"metrics": metrics, "count": len(metrics)}
    finally:
        conn.close()


@router.get("/db/{entity_id}")
async def get_entity_detail(entity_id: str, _user: dict = Depends(get_current_admin_user)):
    """Get full entity detail with tags."""
    conn = _get_db_conn()
    try:
        entity = get_entity(conn, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
        return {"entity": entity}
    finally:
        conn.close()


@router.put("/db/{entity_id}")
async def update_entity_endpoint(
    entity_id: str, body: EntityUpdate,
    _user: dict = Depends(get_current_admin_user),
):
    """Update entity fields."""
    conn = _get_db_conn()
    try:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if not fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        ok = update_entity(conn, entity_id, fields)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
        conn.commit()
        return {"message": f"Entity {entity_id} updated", "fields": list(fields.keys())}
    finally:
        conn.close()


@router.put("/db/{entity_id}/tags")
async def update_entity_tags_endpoint(
    entity_id: str, body: EntityTags,
    _user: dict = Depends(get_current_admin_user),
):
    """Replace all tags for an entity."""
    conn = _get_db_conn()
    try:
        ok = update_entity_tags(conn, entity_id, body.model_dump())
        if not ok:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
        conn.commit()
        return {"message": f"Tags updated for entity {entity_id}"}
    finally:
        conn.close()


@router.delete("/db/{entity_id}")
async def delete_entity_endpoint(entity_id: str, _user: dict = Depends(get_current_admin_user)):
    """Delete an entity and its tags/translations."""
    conn = _get_db_conn()
    try:
        ok = delete_entity(conn, entity_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
        conn.commit()
        return {"message": f"Entity {entity_id} deleted"}
    finally:
        conn.close()


# ============================================================================
# SOURCE MANAGEMENT — DB-first (reads/writes config table)
# ============================================================================

@router.get("/sources")
async def list_sources_endpoint(_user: dict = Depends(get_current_admin_user)):
    """List all configured sources from DB."""
    conn = _get_db_conn()
    try:
        sources = list_sources_flat(conn)
        return {"sources": sources, "count": len(sources)}
    finally:
        conn.close()


@router.post("/sources")
async def add_source(
    name: str = Query(..., description="Source key name (e.g. 'blog_2')"),
    section: str = Query("oeuvre", description="Config section: 'oeuvre' or 'stages'"),
    body: SourceConfig = ...,
    _user: dict = Depends(get_current_admin_user),
):
    """Add a new source to the DB config table."""
    conn = _get_db_conn()
    try:
        if section == "oeuvre":
            key = f"oeuvre.{name}"
            if get_config(conn, "sources", key) is not None:
                raise HTTPException(status_code=409, detail=f"Source '{name}' already exists")
        elif section == "stages":
            key = "stages"
        else:
            raise HTTPException(status_code=400, detail="Section must be 'oeuvre' or 'stages'")

        source_data = {"enabled": body.enabled}
        if body.connector:
            source_data["connector"] = body.connector
        if body.url:
            source_data["url"] = body.url
        if body.sub_type_override:
            source_data["sub_type_override"] = body.sub_type_override
        source_data["limit"] = body.limit
        source_data["llm-processing"] = body.llm_processing
        if body.fetch_readmes is not None:
            source_data["fetch_readmes"] = body.fetch_readmes
        if body.fetch_content is not None:
            source_data["fetch_content"] = body.fetch_content
        if body.cache_ttl_hours is not None:
            source_data["cache_ttl_hours"] = body.cache_ttl_hours
        if body.single_entity is not None:
            source_data["single-entity"] = body.single_entity
        if body.connector_setup:
            source_data["connector-setup"] = body.connector_setup

        set_config(conn, "sources", key, source_data, updated_by="admin")
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Added source '{key}' to DB")
    return {"message": f"Source '{name}' added", "source_id": key}


@router.put("/sources/{source_id:path}")
async def update_source(
    source_id: str, body: SourceConfig,
    _user: dict = Depends(get_current_admin_user),
):
    """Update an existing source in the DB config table."""
    conn = _get_db_conn()
    try:
        # Determine the DB key
        if source_id.startswith("oeuvre."):
            key = source_id
        elif source_id == "stages":
            key = "stages"
        else:
            raise HTTPException(status_code=400, detail=f"Unknown source_id: {source_id}")

        existing = get_config(conn, "sources", key)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")
        if not isinstance(existing, dict):
            existing = {}

        if body.connector is not None:
            existing["connector"] = body.connector
        if body.url is not None:
            existing["url"] = body.url
        if body.sub_type_override is not None:
            existing["sub_type_override"] = body.sub_type_override
        existing["enabled"] = body.enabled
        existing["limit"] = body.limit
        existing["llm-processing"] = body.llm_processing
        if body.fetch_readmes is not None:
            existing["fetch_readmes"] = body.fetch_readmes
        if body.fetch_content is not None:
            existing["fetch_content"] = body.fetch_content
        if body.cache_ttl_hours is not None:
            existing["cache_ttl_hours"] = body.cache_ttl_hours
        if body.single_entity is not None:
            existing["single-entity"] = body.single_entity
        if body.connector_setup is not None:
            existing["connector-setup"] = body.connector_setup

        set_config(conn, "sources", key, existing, updated_by="admin")
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Updated source '{source_id}' in DB")
    return {"message": f"Source '{source_id}' updated"}


@router.delete("/sources/{source_id:path}")
async def remove_source(source_id: str, _user: dict = Depends(get_current_admin_user)):
    """Remove a source from the DB config table."""
    conn = _get_db_conn()
    try:
        if source_id.startswith("oeuvre."):
            key = source_id
        else:
            raise HTTPException(
                status_code=400,
                detail="Only oeuvre sources can be deleted. Use PUT to disable stages/identity.",
            )
        ok = delete_config(conn, "sources", key)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Removed source '{source_id}' from DB")
    return {"message": f"Source '{source_id}' removed"}


# ============================================================================
# LLM PROMPTS MANAGEMENT
# ============================================================================

@router.get("/prompts")
async def list_prompts_endpoint(
    category: Optional[str] = Query(None),
    _user: dict = Depends(get_current_admin_user),
):
    """List all LLM prompts."""
    conn = _get_db_conn()
    try:
        prompts = list_prompts(conn, category=category)
        return {"prompts": prompts, "count": len(prompts)}
    finally:
        conn.close()


@router.get("/prompts/{prompt_id}")
async def get_prompt_endpoint(prompt_id: str, _user: dict = Depends(get_current_admin_user)):
    """Get a single prompt by ID."""
    conn = _get_db_conn()
    try:
        prompt = get_prompt(conn, prompt_id)
        if not prompt:
            raise HTTPException(status_code=404, detail=f"Prompt '{prompt_id}' not found")
        return {"prompt": prompt}
    finally:
        conn.close()


@router.put("/prompts/{prompt_id}")
async def update_prompt_endpoint(
    prompt_id: str, body: PromptUpdate,
    _user: dict = Depends(get_current_admin_user),
):
    """Update a prompt's content (auto-increments version)."""
    conn = _get_db_conn()
    try:
        existing = get_prompt(conn, prompt_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Prompt '{prompt_id}' not found")
        name = body.name or existing["name"]
        category = body.category or existing["category"]
        upsert_prompt(conn, prompt_id, category, name, body.content, updated_by="admin")
        conn.commit()
        return {"message": f"Prompt '{prompt_id}' updated", "version": existing["version"] + 1}
    finally:
        conn.close()


# ============================================================================
# MCP PROMPT TEMPLATES (user-facing, for LLM agents)
# ============================================================================

@router.get("/mcp-prompts")
async def list_mcp_prompts(_user: dict = Depends(get_current_admin_user)):
    """List all MCP prompt templates."""
    conn = _get_db_conn()
    try:
        section = get_config_section(conn, "mcp_prompts")
        prompts = [{"id": k, **v} for k, v in section.items()]
        return {"prompts": prompts, "count": len(prompts)}
    finally:
        conn.close()


@router.get("/mcp-prompts/{prompt_id}")
async def get_mcp_prompt(prompt_id: str, _user: dict = Depends(get_current_admin_user)):
    """Get a single MCP prompt template."""
    conn = _get_db_conn()
    try:
        val = get_config(conn, "mcp_prompts", prompt_id)
        if val is None:
            raise HTTPException(status_code=404, detail=f"MCP prompt '{prompt_id}' not found")
        return {"prompt": {"id": prompt_id, **val}}
    finally:
        conn.close()


@router.post("/mcp-prompts")
async def create_mcp_prompt(body: McpPromptCreate, _user: dict = Depends(get_current_admin_user)):
    """Create a new MCP prompt template."""
    conn = _get_db_conn()
    try:
        existing = get_config(conn, "mcp_prompts", body.id)
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"MCP prompt '{body.id}' already exists")
        val = {
            "name": body.name,
            "description": body.description,
            "use_case": body.use_case,
            "prompt_template": body.prompt_template,
        }
        set_config(conn, "mcp_prompts", body.id, val, updated_by="admin")
        conn.commit()
        return {"message": f"MCP prompt '{body.id}' created"}
    finally:
        conn.close()


@router.put("/mcp-prompts/{prompt_id}")
async def update_mcp_prompt(
    prompt_id: str, body: McpPromptUpdate,
    _user: dict = Depends(get_current_admin_user),
):
    """Update an MCP prompt template."""
    conn = _get_db_conn()
    try:
        existing = get_config(conn, "mcp_prompts", prompt_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"MCP prompt '{prompt_id}' not found")
        if body.name is not None:
            existing["name"] = body.name
        if body.description is not None:
            existing["description"] = body.description
        if body.use_case is not None:
            existing["use_case"] = body.use_case
        if body.prompt_template is not None:
            existing["prompt_template"] = body.prompt_template
        set_config(conn, "mcp_prompts", prompt_id, existing, updated_by="admin")
        conn.commit()
        return {"message": f"MCP prompt '{prompt_id}' updated"}
    finally:
        conn.close()


@router.delete("/mcp-prompts/{prompt_id}")
async def delete_mcp_prompt(prompt_id: str, _user: dict = Depends(get_current_admin_user)):
    """Delete an MCP prompt template."""
    conn = _get_db_conn()
    try:
        deleted = delete_config(conn, "mcp_prompts", prompt_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"MCP prompt '{prompt_id}' not found")
        conn.commit()
        return {"message": f"MCP prompt '{prompt_id}' deleted"}
    finally:
        conn.close()


# ============================================================================
# CONFIG MANAGEMENT — chat, metrics, identity, i18n
# ============================================================================

@router.get("/config/{namespace}")
async def get_config_section_endpoint(
    namespace: str,
    _user: dict = Depends(get_current_admin_user),
):
    """Get all config values for a namespace."""
    allowed = {"chat", "metrics", "identity", "i18n", "server", "security", "session", "llm", "cache", "endpoints"}
    if namespace not in allowed:
        raise HTTPException(status_code=400, detail=f"Namespace must be one of: {allowed}")
    conn = _get_db_conn()
    try:
        section = get_config_section(conn, namespace)
        # Mask sensitive values for display
        if namespace == "llm" and "groq_api_key" in section:
            key = section["groq_api_key"]
            if key:
                section["groq_api_key"] = key[:8] + "****" if len(key) > 8 else "****"
        return {"namespace": namespace, "config": section}
    finally:
        conn.close()


@router.put("/config/{namespace}")
async def update_config_section_endpoint(
    namespace: str, body: dict,
    _user: dict = Depends(get_current_admin_user),
):
    """Update config values for a namespace. Accepts a flat dict of key: value pairs."""
    allowed = {"chat", "metrics", "identity", "i18n", "server", "security", "session", "llm", "cache", "endpoints"}
    if namespace not in allowed:
        raise HTTPException(status_code=400, detail=f"Namespace must be one of: {allowed}")
    conn = _get_db_conn()
    try:
        for key, value in body.items():
            set_config(conn, namespace, key, value, updated_by="admin")
        conn.commit()
        return {"message": f"Config '{namespace}' updated", "keys": list(body.keys())}
    finally:
        conn.close()


@router.put("/config/chat")
async def update_chat_config(body: ChatConfig, _user: dict = Depends(get_current_admin_user)):
    """Update chat proxy configuration."""
    conn = _get_db_conn()
    try:
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        for key, value in updates.items():
            set_config(conn, "chat", key, value, updated_by="admin")
        conn.commit()
        return {"message": "Chat config updated", "keys": list(updates.keys())}
    finally:
        conn.close()


@router.put("/config/i18n")
async def update_i18n_config(body: I18nConfig, _user: dict = Depends(get_current_admin_user)):
    """Update i18n configuration."""
    conn = _get_db_conn()
    try:
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        for key, value in updates.items():
            set_config(conn, "i18n", key, value, updated_by="admin")
        conn.commit()
        return {"message": "i18n config updated", "keys": list(updates.keys())}
    finally:
        conn.close()


# ============================================================================
# ADMIN ACTIONS — enrichment, translation, PDF extraction
# ============================================================================

def _launch_job(cmd: list[str], label: str) -> dict:
    """Launch a subprocess job and return job info."""
    job_id = str(uuid.uuid4())[:8]
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        _running_jobs[job_id] = {
            "process": proc, "command": " ".join(cmd),
            "started_at": _now_utc(), "status": "running", "output": "",
        }
    except Exception as e:
        logger.error(f"Failed to start {label} job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start job: {e}")
    logger.info(f"Started {label} job {job_id}: {' '.join(cmd)}")
    return {
        "job_id": job_id, "command": " ".join(cmd),
        "status": "running", "started_at": _running_jobs[job_id]["started_at"],
    }


@router.post("/scrape")
async def trigger_scraping(body: ScrapeRequest, _user: dict = Depends(get_current_admin_user)):
    """Trigger a scraping/enrichment job."""
    cmd = [sys.executable, str(ROOT / "ingest.py")]
    if body.source:
        cmd.extend(["--source", body.source])
    if body.force:
        cmd.append("--force")
    if body.disable_llm:
        cmd.append("--disable-llm")
    if body.llm_only:
        cmd.append("--llm-only")
    if body.export_yaml:
        cmd.append("--export-yaml")
    return _launch_job(cmd, "scrape")


@router.post("/enrich")
async def trigger_enrichment(
    source: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    _user: dict = Depends(get_current_admin_user),
):
    """Trigger LLM enrichment (re-enrich entities)."""
    cmd = [sys.executable, str(ROOT / "ingest.py"), "--llm-only"]
    if source:
        cmd.extend(["--source", source])
    if entity_id:
        cmd.extend(["--item", entity_id])
    return _launch_job(cmd, "enrich")


@router.post("/translate")
async def trigger_translation(
    lang: Optional[str] = Query(None),
    force: bool = Query(False),
    _user: dict = Depends(get_current_admin_user),
):
    """Trigger translation job."""
    cmd = [sys.executable, "-m", "llm.translator"]
    if lang:
        cmd.extend(["--lang", lang])
    if force:
        cmd.append("--force")
    return _launch_job(cmd, "translate")


@router.post("/extract-pdf")
async def trigger_pdf_extraction(_user: dict = Depends(get_current_admin_user)):
    """Trigger PDF extraction (re-parse LinkedIn PDF)."""
    cmd = [sys.executable, str(ROOT / "ingest.py"), "--source", "stages", "--force"]
    return _launch_job(cmd, "extract-pdf")


@router.post("/recalculate-metrics")
async def trigger_metrics_recalculation(_user: dict = Depends(get_current_admin_user)):
    """Trigger metrics recalculation."""
    cmd = [sys.executable, str(ROOT / "recalculate_metrics.py")]
    return _launch_job(cmd, "recalculate-metrics")


@router.get("/jobs")
async def list_jobs(_user: dict = Depends(get_current_admin_user)):
    jobs = []
    for job_id, info in _running_jobs.items():
        proc = info["process"]
        if proc.poll() is not None and info["status"] == "running":
            info["status"] = "completed" if proc.returncode == 0 else "failed"
            info["return_code"] = proc.returncode
            try:
                info["output"] = proc.stdout.read() if proc.stdout else ""
            except Exception:
                pass
        jobs.append({
            "job_id": job_id, "command": info["command"],
            "started_at": info["started_at"], "status": info["status"],
            "return_code": info.get("return_code"),
        })
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, _user: dict = Depends(get_current_admin_user)):
    if job_id not in _running_jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    info = _running_jobs[job_id]
    proc = info["process"]
    if proc.poll() is not None and info["status"] == "running":
        info["status"] = "completed" if proc.returncode == 0 else "failed"
        info["return_code"] = proc.returncode
        try:
            info["output"] = proc.stdout.read() if proc.stdout else ""
        except Exception:
            pass
    return {
        "job_id": job_id, "command": info["command"],
        "started_at": info["started_at"], "status": info["status"],
        "return_code": info.get("return_code"), "output": info.get("output", ""),
    }


# ============================================================================
# FILE UPLOAD — PDF + HTML, registers in uploaded_files table
# ============================================================================

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    linked_source: Optional[str] = Query(None, description="Source key to link file to"),
    _user: dict = Depends(get_current_admin_user),
):
    """Upload a PDF or HTML file to the data/ directory."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = file.filename.lower().rsplit(".", 1)[-1] if "." in file.filename else ""
    if ext not in ("pdf", "html", "htm"):
        raise HTTPException(status_code=400, detail="Only PDF and HTML files are allowed")

    safe_name = os.path.basename(file.filename)
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)

    filepath = data_dir / safe_name
    content = await file.read()
    filepath.write_bytes(content)

    file_type = "html" if ext in ("html", "htm") else ext

    # Register in DB
    conn = _get_db_conn()
    try:
        file_id = register_file(
            conn, safe_name, f"data/{safe_name}", file_type,
            size_bytes=len(content), linked_source=linked_source,
        )
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Uploaded file: {filepath} ({len(content)} bytes, id={file_id})")
    return {
        "message": "File uploaded successfully",
        "file_id": file_id,
        "filename": safe_name,
        "size": len(content),
        "path": str(filepath),
    }


@router.get("/files")
async def list_uploaded_files(
    linked_source: Optional[str] = Query(None),
    _user: dict = Depends(get_current_admin_user),
):
    """List uploaded files."""
    conn = _get_db_conn()
    try:
        files = list_files(conn, linked_source=linked_source)
        return {"files": files, "count": len(files)}
    finally:
        conn.close()


# ============================================================================
# CONFIG READINESS (foundation for setup wizard)
# ============================================================================

@router.get("/config/readiness")
async def config_readiness(_user: dict = Depends(get_current_admin_user)):
    """Check what config is present vs. missing. Foundation for setup wizard."""
    from db.config_store import check_config_readiness
    conn = _get_db_conn()
    try:
        return check_config_readiness(conn)
    finally:
        conn.close()


# ============================================================================
# DB SEED STATUS
# ============================================================================

@router.get("/seed-status")
async def get_seed_status(_user: dict = Depends(get_current_admin_user)):
    """Check if config DB has been seeded."""
    # Sections populated by seed_config_db.py (not user-specific data)
    SEEDABLE = ("chat", "metrics", "server", "security", "session", "llm", "cache", "endpoints")
    conn = _get_db_conn()
    try:
        sections = {ns: config_section_exists(conn, ns) for ns in SEEDABLE}
        prompts_count = len(list_prompts(conn))
        return {
            "sections": sections,
            "prompts_count": prompts_count,
            "all_seeded": all(sections.values()) and prompts_count > 0,
        }
    finally:
        conn.close()


@router.post("/seed")
async def trigger_seed(
    force: bool = Query(False),
    _user: dict = Depends(get_current_admin_user),
):
    """Seed DB with default config values."""
    cmd = [sys.executable, str(ROOT / "scripts" / "seed_config_db.py")]
    if force:
        cmd.append("--force")
    return _launch_job(cmd, "seed")
