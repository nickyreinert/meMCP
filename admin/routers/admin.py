# Admin Router
# Purpose: Define all admin-specific API endpoints with real business logic
# Main functions: token management, DB browser, source management, scrape trigger
# Dependent files: admin/dependencies/access_control.py, db/models.py

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

import yaml

from admin.dependencies.access_control import get_current_admin_user
from db.models import get_db, DB_PATH, list_entities, list_all_tags, list_tag_metrics

logger = logging.getLogger(__name__)

# Project root (one level up from admin/)
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


# --- In-memory job tracking ---

_running_jobs: dict[str, dict] = {}


# --- Helpers ---

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db_conn():
    """Get a connection to profile.db."""
    return get_db(DB_PATH)


def _content_config_path() -> Path:
    return ROOT / "config.content.yaml"


def _load_content_config() -> dict:
    path = _content_config_path()
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_content_config(data: dict):
    path = _content_config_path()
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


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
    """List available log files."""
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
    """Download specific log file content."""
    log_dir = ROOT / "logs"
    filepath = log_dir / filename

    # Prevent path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    content = filepath.read_text(errors="replace")
    return {"filename": filename, "content": content}


# ============================================================================
# TOKEN MANAGEMENT — wired to real db/profile.db
# ============================================================================

@router.get("/tokens")
async def list_tokens(_user: dict = Depends(get_current_admin_user)):
    """List all tokens with usage counts (mirrors `manage_tokens.py list`)."""
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
    """Create a new access token (mirrors `manage_tokens.py add`)."""
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
        "token": token,  # plaintext — shown once, never stored
        "owner": body.owner,
        "tier": body.tier,
        "expires_at": expires,
        "created_at": created,
    }


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: int, _user: dict = Depends(get_current_admin_user)):
    """Soft-revoke a token (set is_active=0)."""
    conn = _get_db_conn()
    try:
        row = conn.execute(
            "SELECT id, owner_name FROM tokens WHERE id = ?", (token_id,)
        ).fetchone()
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
    token_id: int,
    body: TokenBudget,
    _user: dict = Depends(get_current_admin_user),
):
    """Update per-token budget overrides (mirrors `manage_tokens.py budget`)."""
    conn = _get_db_conn()
    try:
        row = conn.execute(
            "SELECT id, owner_name FROM tokens WHERE id = ?", (token_id,)
        ).fetchone()
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
            # Return current budget
            brow = conn.execute(
                "SELECT tier, max_tokens_per_session, max_calls_per_day, "
                "max_input_chars, max_output_chars FROM tokens WHERE id = ?",
                (token_id,),
            ).fetchone()
            return {
                "token_id": token_id,
                "owner": row["owner_name"],
                "budget": {
                    "tier": brow["tier"] or "mcp",
                    "max_tokens_per_session": brow["max_tokens_per_session"],
                    "max_calls_per_day": brow["max_calls_per_day"],
                    "max_input_chars": brow["max_input_chars"],
                    "max_output_chars": brow["max_output_chars"],
                },
                "message": "No changes — showing current budget",
            }

        set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
        values = [v for _, v in updates] + [token_id]
        conn.execute(f"UPDATE tokens SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Updated budget for token #{token_id}")
    return {
        "token_id": token_id,
        "owner": row["owner_name"],
        "updated": {col: val for col, val in updates},
    }


@router.get("/tokens/{token_id}/stats")
async def get_token_stats(
    token_id: int,
    _user: dict = Depends(get_current_admin_user),
):
    """Get usage stats for a specific token (mirrors `manage_tokens.py stats --id`)."""
    conn = _get_db_conn()
    try:
        token = conn.execute(
            "SELECT id, owner_name, expires_at, is_active, tier, "
            "max_tokens_per_session, max_calls_per_day, max_input_chars, max_output_chars "
            "FROM tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_id} not found")

        logs = conn.execute(
            """
            SELECT endpoint_called, timestamp, input_args,
                   api_provider, input_text, tokens_used
            FROM usage_logs
            WHERE token_id = ?
            ORDER BY timestamp DESC
            LIMIT 50
            """,
            (token_id,),
        ).fetchall()
    finally:
        conn.close()

    # Endpoint frequency
    freq: dict[str, int] = {}
    for log in logs:
        ep = log["endpoint_called"]
        freq[ep] = freq.get(ep, 0) + 1

    recent = []
    for log in logs[:20]:
        recent.append({
            "endpoint": log["endpoint_called"],
            "timestamp": log["timestamp"],
            "api_provider": log["api_provider"],
            "tokens_used": log["tokens_used"],
            "input_preview": (log["input_text"] or log["input_args"] or "")[:100],
        })

    # Determine status
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
        "token_id": token["id"],
        "owner": token["owner_name"],
        "status": token_status,
        "tier": token["tier"] or "mcp",
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
# DATABASE BROWSER
# ============================================================================

@router.get("/db")
async def browse_database(
    flavor: Optional[str] = Query(None, description="Filter by flavor: personal, stages, oeuvre, identity"),
    category: Optional[str] = Query(None, description="Filter by category"),
    source: Optional[str] = Query(None, description="Filter by source"),
    search: Optional[str] = Query(None, description="Full-text search"),
    tag: Optional[str] = Query(None, description="Filter by tag name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user: dict = Depends(get_current_admin_user),
):
    """Browse entities in the database with filters."""
    conn = _get_db_conn()
    try:
        # Build tag list if provided
        tags = [tag] if tag else None
        entities = list_entities(
            conn,
            flavor=flavor,
            category=category,
            source=source,
            search=search,
            tags=tags,
            limit=limit,
            offset=offset,
        )
        return {"entities": entities, "count": len(entities), "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.get("/db/stats")
async def database_stats(_user: dict = Depends(get_current_admin_user)):
    """Get database summary statistics."""
    conn = _get_db_conn()
    try:
        # Entity counts by flavor
        flavor_counts = conn.execute(
            "SELECT flavor, COUNT(*) as count FROM entities GROUP BY flavor ORDER BY flavor"
        ).fetchall()

        # Entity counts by category
        category_counts = conn.execute(
            "SELECT flavor, category, COUNT(*) as count FROM entities "
            "WHERE category IS NOT NULL GROUP BY flavor, category ORDER BY flavor, category"
        ).fetchall()

        # Tag counts by type
        tag_counts = conn.execute(
            "SELECT tag_type, COUNT(DISTINCT tag) as unique_tags, COUNT(*) as total_assignments "
            "FROM tags GROUP BY tag_type ORDER BY tag_type"
        ).fetchall()

        # Total entities
        total = conn.execute("SELECT COUNT(*) as count FROM entities").fetchone()["count"]

        # Token count
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
async def list_tags(
    tag_type: Optional[str] = Query(None, description="Filter: technology, skill, generic"),
    _user: dict = Depends(get_current_admin_user),
):
    """List all tags, optionally filtered by type."""
    conn = _get_db_conn()
    try:
        tags = list_all_tags(conn, tag_type=tag_type)
        return {"tags": tags, "count": len(tags)}
    finally:
        conn.close()


@router.get("/db/metrics")
async def get_metrics(
    tag_type: Optional[str] = Query(None, description="Filter: technology, skill, generic"),
    order_by: str = Query("relevance_score", description="Sort field"),
    limit: int = Query(100, ge=1, le=500),
    _user: dict = Depends(get_current_admin_user),
):
    """List tag metrics."""
    conn = _get_db_conn()
    try:
        metrics = list_tag_metrics(conn, tag_type=tag_type, order_by=order_by, limit=limit)
        return {"metrics": metrics, "count": len(metrics)}
    finally:
        conn.close()


# ============================================================================
# SOURCE MANAGEMENT — reads/writes config.content.yaml
# ============================================================================

@router.get("/sources")
async def list_sources(_user: dict = Depends(get_current_admin_user)):
    """List all configured sources from config.content.yaml."""
    config = _load_content_config()

    sources = []

    # Identity source
    identity = config.get("identity", {})
    if identity:
        sources.append({
            "id": "identity",
            "section": "identity",
            "source": identity.get("source"),
        })

    # Stages source
    stages = config.get("stages", {})
    if stages:
        sources.append({
            "id": "stages",
            "section": "stages",
            "connector": stages.get("connector"),
            "enabled": stages.get("enabled", True),
            "url": stages.get("url"),
            "llm_processing": stages.get("llm-processing", False),
        })

    # Oeuvre sources
    oeuvre = config.get("oeuvre", {})
    for name, cfg in oeuvre.items():
        sources.append({
            "id": f"oeuvre.{name}",
            "section": "oeuvre",
            "name": name,
            "connector": cfg.get("connector"),
            "enabled": cfg.get("enabled", True),
            "url": cfg.get("url"),
            "sub_type_override": cfg.get("sub_type_override"),
            "llm_processing": cfg.get("llm-processing", False),
            "limit": cfg.get("limit", 0),
        })

    return {"sources": sources, "count": len(sources)}


@router.post("/sources")
async def add_source(
    name: str = Query(..., description="Source key name (e.g. 'blog_2')"),
    section: str = Query("oeuvre", description="Config section: 'oeuvre' or 'stages'"),
    body: SourceConfig = ...,
    _user: dict = Depends(get_current_admin_user),
):
    """Add a new source to config.content.yaml."""
    config = _load_content_config()

    if section == "oeuvre":
        if "oeuvre" not in config:
            config["oeuvre"] = {}
        if name in config["oeuvre"]:
            raise HTTPException(status_code=409, detail=f"Source '{name}' already exists in oeuvre")

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

        config["oeuvre"][name] = source_data
    else:
        raise HTTPException(status_code=400, detail="Only 'oeuvre' section supports adding sources")

    _save_content_config(config)
    logger.info(f"Added source '{name}' to config.content.yaml")
    return {"message": f"Source '{name}' added", "source_id": f"oeuvre.{name}"}


@router.put("/sources/{source_id}")
async def update_source(
    source_id: str,
    body: SourceConfig,
    _user: dict = Depends(get_current_admin_user),
):
    """Update an existing source in config.content.yaml."""
    config = _load_content_config()

    if source_id.startswith("oeuvre."):
        name = source_id[len("oeuvre."):]
        oeuvre = config.get("oeuvre", {})
        if name not in oeuvre:
            raise HTTPException(status_code=404, detail=f"Source '{name}' not found in oeuvre")

        existing = oeuvre[name]
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

        config["oeuvre"][name] = existing
    elif source_id == "stages":
        stages = config.get("stages", {})
        if body.connector is not None:
            stages["connector"] = body.connector
        if body.url is not None:
            stages["url"] = body.url
        stages["enabled"] = body.enabled
        stages["llm-processing"] = body.llm_processing
        config["stages"] = stages
    else:
        raise HTTPException(status_code=400, detail=f"Unknown source_id format: {source_id}")

    _save_content_config(config)
    logger.info(f"Updated source '{source_id}' in config.content.yaml")
    return {"message": f"Source '{source_id}' updated"}


@router.delete("/sources/{source_id}")
async def remove_source(
    source_id: str,
    _user: dict = Depends(get_current_admin_user),
):
    """Remove a source from config.content.yaml."""
    config = _load_content_config()

    if source_id.startswith("oeuvre."):
        name = source_id[len("oeuvre."):]
        oeuvre = config.get("oeuvre", {})
        if name not in oeuvre:
            raise HTTPException(status_code=404, detail=f"Source '{name}' not found in oeuvre")
        del config["oeuvre"][name]
    else:
        raise HTTPException(
            status_code=400,
            detail="Only oeuvre sources can be deleted. Use PUT to disable stages/identity.",
        )

    _save_content_config(config)
    logger.info(f"Removed source '{source_id}' from config.content.yaml")
    return {"message": f"Source '{source_id}' removed"}


# ============================================================================
# SCRAPING & ENRICHMENT — launches ingest.py as subprocess
# ============================================================================

@router.post("/scrape")
async def trigger_scraping(
    body: ScrapeRequest,
    _user: dict = Depends(get_current_admin_user),
):
    """Trigger a scraping/enrichment job by launching ingest.py as a subprocess."""
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

    job_id = str(uuid.uuid4())[:8]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _running_jobs[job_id] = {
            "process": proc,
            "command": " ".join(cmd),
            "started_at": _now_utc(),
            "status": "running",
            "output": "",
        }
    except Exception as e:
        logger.error(f"Failed to start scraping job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start job: {e}")

    logger.info(f"Started scraping job {job_id}: {' '.join(cmd)}")
    return {
        "job_id": job_id,
        "command": " ".join(cmd),
        "status": "running",
        "started_at": _running_jobs[job_id]["started_at"],
    }


@router.get("/jobs")
async def list_jobs(_user: dict = Depends(get_current_admin_user)):
    """List all running/completed scraping jobs."""
    jobs = []
    for job_id, info in _running_jobs.items():
        proc = info["process"]
        if proc.poll() is not None and info["status"] == "running":
            # Process finished — capture output
            info["status"] = "completed" if proc.returncode == 0 else "failed"
            info["return_code"] = proc.returncode
            try:
                info["output"] = proc.stdout.read() if proc.stdout else ""
            except Exception:
                pass

        jobs.append({
            "job_id": job_id,
            "command": info["command"],
            "started_at": info["started_at"],
            "status": info["status"],
            "return_code": info.get("return_code"),
        })
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, _user: dict = Depends(get_current_admin_user)):
    """Get status and output of a specific job."""
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
        "job_id": job_id,
        "command": info["command"],
        "started_at": info["started_at"],
        "status": info["status"],
        "return_code": info.get("return_code"),
        "output": info.get("output", ""),
    }


# ============================================================================
# FILE UPLOAD
# ============================================================================

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    _user: dict = Depends(get_current_admin_user),
):
    """Upload a PDF file to the data/ directory for processing."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    # Sanitize filename
    safe_name = os.path.basename(file.filename)
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)

    filepath = data_dir / safe_name
    content = await file.read()
    filepath.write_bytes(content)

    logger.info(f"Uploaded file: {filepath} ({len(content)} bytes)")
    return {
        "message": "File uploaded successfully",
        "filename": safe_name,
        "size": len(content),
        "path": str(filepath),
    }
