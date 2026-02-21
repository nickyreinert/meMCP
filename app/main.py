"""
app/main.py — Personal MCP FastAPI Server v2.1
===============================================
All routes return structured JSON. Entity graph is the backbone.

Language support:
  Every data route accepts language via two mechanisms (priority order):
    1. ?lang=de  query parameter
    2. Accept-Language: de-DE,de;q=0.9  HTTP header
  Falls back to 'en' if the requested lang has no translations stored.
  Technology entity names are never translated (they are universal).
  Every response includes a "_lang" meta field.

Routes:
  GET /                          → API index
  GET /greeting                  → identity card (translated)
  GET /categories                → entity type list + counts
  GET /entities                  → paginated entity list (translated)
  GET /entities/{id}             → single entity + relations (translated)
  GET /entities/{id}/related     → graph neighbours (translated)
  GET /category/{type}           → entities by type (translated)
  GET /technology_stack          → all technology entities
  GET /technology_stack/{tag}    → tech cross-reference (translated)
  GET /tags                      → all tags + counts
  GET /search?q=                 → full-text search (translated)
  GET /graph                     → relation graph query
  GET /languages                 → supported languages + translation coverage
  GET /skills                    → all skills + entity counts
  GET /skills/{name}             → detail: entities with this skill
  GET /technologies              → all technologies + entity counts
  GET /technologies/{name}       → detail: entities that used this tech
  GET /stages                    → career timeline (jobs + education)
  GET /stages/{id}               → single stage detail
  GET /work                      → side projects + literature
  GET /work/{id}                 → single work item detail
  POST /admin/rebuild            → trigger scrape rebuild (token required)
  POST /admin/translate          → trigger translation run (token required)
  GET /health                    → liveness check
"""

import json
import os
import subprocess
import sys
import time
import yaml
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from db.models import (
    DB_PATH, get_db, init_db,
    get_entity, list_entities,
    list_all_tags,
    get_translation, get_greeting_translation,
    apply_translation, SUPPORTED_LANGS, DEFAULT_LANG,
    query_skills, query_skill_detail,
    query_technologies, query_technology_detail,
    query_tag_detail,
    query_stages,
    query_oeuvre,
    query_graph,
    get_tag_metrics,
    query_skills_with_metrics,
    query_technologies_with_metrics,
)

from app.session_tracker import SessionTracker


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG & TEMPLATES
# ─────────────────────────────────────────────────────────────────────────────

def load_config():
    """Load config.yaml from project root."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

CONFIG = load_config()
APP_VERSION = "2.2.0"

# Base URL for templates and documentation
server_cfg = CONFIG.get("server", {})
host = server_cfg.get('host', 'localhost')
port = server_cfg.get('port', 8000)
# Use localhost for display if host is 0.0.0.0 (bind-all)
display_host = 'localhost' if host == '0.0.0.0' else host
BASE_URL = server_cfg.get("base_url", f"http://{display_host}:{port}")

# Templates directory
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Session tracker
session_cfg = CONFIG.get("session", {})
if session_cfg.get("enabled", True):
    session_tracker = SessionTracker(
        timeout_hours=session_cfg.get("timeout_hours", 5.0),
        log_file=session_cfg.get("log_file", "logs/api_access.log"),
        db_path=session_cfg.get("db_path", "db/sessions.db"),
        relevant_endpoints=session_cfg.get("relevant_endpoints", {})
    )
else:
    session_tracker = None

# ─────────────────────────────────────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(DB_PATH)
    yield


app = FastAPI(
    title="Personal MCP Server",
    description="Entity-graph personal profile API with EN/DE multi-language support.",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION TRACKING MIDDLEWARE
# ─────────────────────────────────────────────────────────────────────────────

@app.middleware("http")
async def session_tracking_middleware(request: Request, call_next):
    """
    Track all requests and inject coverage metadata into responses.
    
    For each request:
      1. Extract IP and User-Agent
      2. Track in session (anonymized)
      3. Calculate coverage
      4. Inject metadata into response
    """
    # Get client info
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    endpoint = request.url.path
    method = request.method
    
    # Extract query params for pagination detection
    query_params = dict(request.query_params) if request.query_params else None
    
    # Track request if enabled
    coverage_meta = None
    if session_tracker:
        coverage_meta = session_tracker.track_request(
            ip_address=client_ip,
            user_agent=user_agent,
            endpoint=endpoint,
            method=method,
            query_params=query_params
        )
    
    # Process request
    response = await call_next(request)
    
    # Inject coverage metadata as headers (for easy access by LLM agents)
    if coverage_meta and "coverage" in coverage_meta:
        cov = coverage_meta["coverage"]
        response.headers["X-Session-Visitor-ID"] = coverage_meta["visitor_id"]
        response.headers["X-Session-Request-Count"] = str(coverage_meta["session"]["request_count"])
        response.headers["X-Coverage-Visited"] = str(cov.get("visited_count", 0))
        response.headers["X-Coverage-Total"] = str(cov.get("total_count", 0))
        response.headers["X-Coverage-Percentage"] = f"{cov.get('percentage', 0):.1f}"
        response.headers["X-Coverage-Earned-Points"] = f"{cov.get('earned_points', 0):.2f}"
        response.headers["X-Coverage-Total-Points"] = str(cov.get("total_points", 0))
    
    return response


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

LANG_LABELS = {"en": "English", "de": "Deutsch"}


def ok(data: Any, meta: dict = None) -> dict:
    resp = {"status": "success", "data": data}
    if meta:
        resp["meta"] = meta
    return resp


def err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"status": "error", "error": {"code": code, "message": msg}},
    )


def db():
    conn = get_db(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# LANGUAGE NEGOTIATION
# ─────────────────────────────────────────────────────────────────────────────

def resolve_lang(lang_param: Optional[str], accept_language: Optional[str]) -> str:
    """
    Priority: ?lang= > Accept-Language header > DEFAULT_LANG.
    Falls back silently for unsupported codes.
    """
    for candidate in (lang_param, _best_accept_lang(accept_language)):
        if candidate and candidate.lower() in SUPPORTED_LANGS:
            return candidate.lower()
    return DEFAULT_LANG


def _best_accept_lang(header: Optional[str]) -> Optional[str]:
    """Parse 'de-DE,de;q=0.9,en;q=0.8' → highest-weighted supported lang."""
    if not header:
        return None
    best_lang, best_q = None, -1.0
    for part in header.replace(" ", "").split(","):
        tag_q = part.split(";")
        tag = tag_q[0].split("-")[0].lower()
        q = 1.0
        if len(tag_q) > 1 and tag_q[1].startswith("q="):
            try:
                q = float(tag_q[1][2:])
            except ValueError:
                pass
        if tag in SUPPORTED_LANGS and q > best_q:
            best_lang, best_q = tag, q
    return best_lang


def _localise(conn, entity: dict, lang: str) -> dict:
    """Overlay stored translation. Skips technology/person (names are universal)."""
    if lang == DEFAULT_LANG or entity.get("type") in ("technology", "person"):
        return entity
    translation = get_translation(conn, entity["id"], lang)
    if not translation:
        return entity
    return apply_translation(entity, translation)


def _localise_many(conn, entities: list, lang: str) -> list:
    if lang == DEFAULT_LANG:
        return entities
    return [_localise(conn, e, lang) for e in entities]


def _lang_meta(lang: str) -> dict:
    return {"lang": lang, "lang_label": LANG_LABELS.get(lang, lang)}


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY / RELATION METADATA
# ─────────────────────────────────────────────────────────────────────────────

ENTITY_META = {
    "person":       {"label": "Person",        "description": "Profile owner identity"},
    "professional": {"label": "Professional",  "description": "Work experience & roles"},
    "company":      {"label": "Company",       "description": "Employers and clients"},
    "education":    {"label": "Education",     "description": "Degrees, courses, certifications"},
    "institution":  {"label": "Institution",   "description": "Universities, schools, orgs"},
    "side_project": {"label": "Side Project",  "description": "Personal & open-source projects"},
    "literature":   {"label": "Literature",    "description": "Articles, blog posts, books"},
    "technology":   {"label": "Technology",    "description": "Languages, frameworks, tools"},
    "skill":        {"label": "Skill",         "description": "Competency areas and expertise"},
    "achievement":  {"label": "Achievement",   "description": "Awards, certifications, milestones"},
    "event":        {"label": "Event",         "description": "Conferences, hackathons, talks"},
}

RELATION_META = {
    "worked_at":       "Person worked at a company",
    "studied_at":      "Person studied at an institution",
    "used_technology": "Entity used this technology",
    "authored":        "Person authored this content",
    "featured_in":     "Technology featured in this context",
    "related_to":      "Generic cross-link between entities",
    "part_of":         "Sub-project or sub-task relationship",
    "inspired_by":     "Inspired by another entity",
    "awarded_by":      "Achievement granted by this entity",
}


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", summary="API index", response_class=HTMLResponse)
@limiter.limit("120/minute")
async def index(request: Request):
    # Reset session when hitting root endpoint
    if session_tracker:
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")
        session_tracker.reset_session(client_ip, user_agent)
    
    session_info = {}
    if session_tracker:
        session_cfg = CONFIG.get("session", {})
        session_info = {
            "enabled": True,
            "timeout_hours": session_cfg.get("timeout_hours", 5.0),
            "reset_trigger": "Visiting this root endpoint (/) resets your session and coverage tracking",
            "tracked_via": "Response headers (X-Coverage-*, X-Session-*)",
            "coverage_explanation": "Coverage tracks metric-relevant endpoints: /greeting, /stages, /oeuvre, /skills, /technology, /tags/{name}",
            "tip": "Check response headers for X-Coverage-Percentage to see exploration progress",
        }
    
    data = {
        "name":    "Personal MCP Server",
        "version": APP_VERSION,
        "session": session_info,
        "languages": {
            "supported":   list(SUPPORTED_LANGS),
            "default":     DEFAULT_LANG,
            "negotiation": "?lang=de  or  Accept-Language: de header",
        },
        "routes": {
            "/greeting":               "Identity card — name, tagline, bio, links",
            "/coverage":               "Session coverage report — see which endpoints you've visited and what's missing",
            "/languages":              "Translation coverage per language",
            "/entities":               "Paginated entity list with filters (category (stage|oeuvre), sub_category(study|school|intern|part-time), skills, technology, tags)",
            "/entities/{id}":          "Single entity + extension data + relations",
            "/entities/{id}/related":  "Graph neighbours of an entity",
            "/skills":                 "Skills describe competencies and expertise areas like Data Analytics, SEO, GenAI, etc.",
            "/skills/{name}":          "All entities with this skill",
            "/technology":           "Technologies point to actual tech stacks, frameworks and tools like Python, Docker, Adobe Analytics, etc.",
            "/technology/{name}":    "All entities that used this technology",
            "/tags":                   "All tags with usage counts",
            "/stages":                 "Reflects career timeline: jobs + education with date ranges",
            "/stages/{id}":            "Single stage detail + skills + technologies",
            "/oeuvre":                 "Pet projects, side projects, articles or projects from different stages of the career",
            "/oeuvre/{id}":            "Single work item detail",
            "/health":                 "Liveness check",
            "/search":                 "Full-text search across entities",
            "/graph":                  "Query the relation graph directly"
        },
        "entity_types":   ENTITY_META,
        "relation_types": RELATION_META,
    }
    json_str = json.dumps(ok(data), indent=2)
    return f"<script>window.location.href='/human';</script>{json_str}"


@app.get("/human", response_class=HTMLResponse)
async def human_endpoint(request: Request):
    """Human-friendly instructions page."""
    return templates.TemplateResponse(
        "human.html",
        {"request": request, "base_url": BASE_URL}
    )

@app.get("/health")
async def health():
    return {"status": "ok", "ts": time.time(), "version": APP_VERSION}


@app.get("/coverage", summary="Session coverage report")
@limiter.limit("200/minute")
async def coverage_report(request: Request):
    """
    Returns detailed coverage report for the current session.
    Shows which endpoints have been visited and which are missing.
    
    Use this to understand your exploration progress and identify
    endpoints you haven't visited yet.
    
    Response includes:
      - Overall coverage percentage (weighted by endpoint importance)
      - List of missing endpoints
      - List of incomplete endpoints (paginated endpoints with partial coverage)
      - Detailed breakdown per endpoint with pages visited
    
    Note: Coverage is based on relevant endpoints that have metrics.
    Not all API endpoints count towards coverage.
    """
    if not session_tracker:
        return ok({
            "enabled": False,
            "message": "Session tracking is disabled in config.yaml"
        })
    
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    coverage_data = session_tracker.get_coverage(client_ip, user_agent)
    return ok(coverage_data)


# ── Language coverage ─────────────────────────────────────────────────────────

@app.get("/languages", summary="Language support + translation coverage")
@limiter.limit("120/minute")
async def languages(request: Request, conn=Depends(db)):
    """
    Returns each supported language with translation coverage percentage.
    Use this to decide whether you need to run /admin/translate.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE visibility='public' "
        "AND type NOT IN ('technology','person')"
    ).fetchone()[0]

    coverage = []
    for lang in sorted(SUPPORTED_LANGS):
        n_translated = conn.execute(
            "SELECT COUNT(DISTINCT entity_id) FROM entity_translations WHERE lang=?",
            (lang,),
        ).fetchone()[0]
        greeting_done = bool(
            conn.execute(
                "SELECT 1 FROM greeting_translations WHERE lang=?", (lang,)
            ).fetchone()
        )
        coverage.append({
            "lang":                 lang,
            "label":                LANG_LABELS.get(lang, lang),
            "is_default":           lang == DEFAULT_LANG,
            "entities_total":       total,
            "entities_translated":  n_translated,
            "coverage_pct":         round(n_translated / total * 100, 1) if total else 0,
            "greeting_translated":  greeting_done,
        })

    return ok({"languages": coverage})


# ── Greeting ──────────────────────────────────────────────────────────────────

@app.get("/greeting", summary="Identity card (translated)")
@limiter.limit("200/minute")
async def greeting(
    request: Request,
    conn=Depends(db),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """
    Returns the profile owner's identity card from identity entities.
    Supports multi-language via raw_data JSON field.
    Falls back to English if requested language not available.
    """
    resolved = resolve_lang(lang, accept_language)

    # Fetch all identity entities (basic, links, contact)
    identity_rows = list_entities(conn, flavor="identity", limit=10)
    if not identity_rows:
        raise HTTPException(404, "Identity entities not found — run ingest.py --source identity first.")
    
    # Organize by category
    identity_data = {}
    for row in identity_rows:
        category = row.get("category")
        if category:
            identity_data[category] = row
    
    # Get basic info
    basic_entity = identity_data.get("basic", {})
    basic_raw = basic_entity.get("raw_data", {})
    basic_lang = basic_raw.get(resolved, basic_raw.get(DEFAULT_LANG, {}))
    
    # Get links info
    links_entity = identity_data.get("links", {})
    links_raw = links_entity.get("raw_data", {})
    links_lang = links_raw.get(resolved, links_raw.get(DEFAULT_LANG, {}))
    
    # Get contact info  
    contact_entity = identity_data.get("contact", {})
    contact_raw = contact_entity.get("raw_data", {})
    contact_lang = contact_raw.get(resolved, contact_raw.get(DEFAULT_LANG, {}))
    
    return ok(
        {
            "name":        basic_lang.get("name", basic_entity.get("title", "")),
            "tagline":     basic_lang.get("tagline", ""),
            "description": basic_lang.get("description", ""),
            "location":    basic_lang.get("location", ""),
            "links":       links_lang,
            "contact": {
                "reason":    contact_lang.get("reason", ""),
                "preferred": contact_lang.get("preferred", ""),
                "email":     contact_lang.get("email", ""),
                "phone":     contact_lang.get("phone", ""),
                "telegram":  contact_lang.get("telegram", ""),
                "other":     contact_lang.get("other", ""),
            },
            "tags": basic_entity.get("tags", []),
        },
        meta=_lang_meta(resolved),
    )


# ── Categories ────────────────────────────────────────────────────────────────

@app.get("/categories", summary="Entity flavors + counts")
@limiter.limit("120/minute")
async def categories(request: Request, conn=Depends(db)):
    rows = conn.execute("""
        SELECT flavor, category, COUNT(*) as count FROM entities
        WHERE visibility='public'
        GROUP BY flavor, category ORDER BY flavor, count DESC
    """).fetchall()
    result = {"flavors": {}, "total": 0}
    for row in rows:
        flavor = row["flavor"]
        category = row["category"] or "uncategorized"
        count = row["count"]
        if flavor not in result["flavors"]:
            result["flavors"][flavor] = {"total": 0, "categories": {}}
        result["flavors"][flavor]["categories"][category] = count
        result["flavors"][flavor]["total"] += count
        result["total"] += count
    return ok(result)


# ── Entities list ─────────────────────────────────────────────────────────────

@app.get("/entities", summary="List entities (paginated, translated)")
@limiter.limit("120/minute")
async def entities_list(
    request: Request,
    conn=Depends(db),
    flavor: Optional[str]          = Query(None, description="Filter by flavor: personal|stages|oeuvre"),
    category: Optional[str]        = Query(None, description="Filter by category"),
    tags: Optional[str]            = Query(None, description="Comma-separated tags"),
    source: Optional[str]          = Query(None, description="Filter by source"),
    search: Optional[str]          = Query(None, description="Full-text search"),
    limit: int                     = Query(20, ge=1, le=100),
    offset: int                    = Query(0, ge=0),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    resolved  = resolve_lang(lang, accept_language)
    tag_list  = [t.strip() for t in tags.split(",")] if tags else None

    rows = list_entities(
        conn, flavor=flavor, category=category, tags=tag_list,
        source=source, search=search, limit=limit, offset=offset,
    )
    rows = _localise_many(conn, rows, resolved)

    count_sql = "SELECT COUNT(DISTINCT id) FROM entities WHERE visibility='public'"
    count_params = []
    if flavor:
        count_sql += " AND flavor=?"
        count_params.append(flavor)
    if category:
        count_sql += " AND category=?"
        count_params.append(category)
    total = conn.execute(count_sql, count_params).fetchone()[0]

    return ok(
        {"entries": rows},
        meta={
            "total": total, "limit": limit,
            "offset": offset, "returned": len(rows),
            **_lang_meta(resolved),
        },
    )


# ── Single entity ─────────────────────────────────────────────────────────────

@app.get("/entities/{entity_id}", summary="Single entity detail (translated)")
@limiter.limit("200/minute")
async def entity_detail(
    request: Request,
    entity_id: str,
    conn=Depends(db),
    include_relations: bool        = Query(True),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """Full entity detail + typed extension + graph relations, all localised."""
    resolved = resolve_lang(lang, accept_language)

    entity = get_entity(conn, entity_id)
    if not entity:
        return err(f"Entity '{entity_id}' not found", 404)

    entity = _localise(conn, entity, resolved)

    # Relations removed in simplified model
    if include_relations:
        entity["relations"] = []

    return ok(entity, meta=_lang_meta(resolved))


# ── Entity neighbours ─────────────────────────────────────────────────────────

@app.get("/entities/{entity_id}/related", summary="Entity graph neighbours (translated)")
@limiter.limit("120/minute")
async def entity_related(
    request: Request,
    entity_id: str,
    conn=Depends(db),
    rel_type: Optional[str]        = Query(None, description="Filter by relation type"),
    direction: str                  = Query("both", regex="^(in|out|both)$"),
    lang: Optional[str]            = Query(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    resolved = resolve_lang(lang, accept_language)

    if not conn.execute("SELECT id FROM entities WHERE id=?", (entity_id,)).fetchone():
        return err(f"Entity '{entity_id}' not found", 404)

    # Relations removed in simplified model
    return ok(
        {"entity_id": entity_id, "message": "Relations not supported in simplified model", "related": []},
        meta=_lang_meta(resolved),
    )


# ── Category ──────────────────────────────────────────────────────────────────

@app.get("/category/{entity_flavor}", summary="Entities by flavor (translated)")
@limiter.limit("120/minute")
async def category(
    request: Request,
    entity_flavor: str,
    conn=Depends(db),
    category: Optional[str]        = Query(None, description="Further filter by category"),
    tags: Optional[str]            = Query(None, description="Comma-separated tag filter"),
    search: Optional[str]          = Query(None),
    limit: int                     = Query(50, ge=1, le=100),
    offset: int                    = Query(0, ge=0),
    lang: Optional[str]            = Query(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    resolved = resolve_lang(lang, accept_language)
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    rows = list_entities(
        conn, flavor=entity_flavor, category=category, tags=tag_list,
        search=search, limit=limit, offset=offset,
    )
    rows = _localise_many(conn, rows, resolved)

    return ok(
        {
            "flavor":  entity_flavor,
            "category": category,
            "entries": rows,
            "count":   len(rows),
        },
        meta=_lang_meta(resolved),
    )


# ── Tags ──────────────────────────────────────────────────────────────────────

@app.get("/tags", summary="All tags + counts")
@limiter.limit("120/minute")
async def tags_route(request: Request, conn=Depends(db)):
    all_tags = list_all_tags(conn)
    return ok({"tags": all_tags, "count": len(all_tags)})


@app.get("/tags/{tag_name}", summary="Entities with a specific generic tag")
@limiter.limit("120/minute")
async def tag_detail(
    request: Request,
    tag_name: str,
    conn=Depends(db),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """
    Returns all entities that have a specific generic tag.
    Generic tags include: industry sectors, topics, methodologies, etc.
    Entity titles/descriptions are localised.
    """
    resolved = resolve_lang(lang, accept_language)
    detail = query_tag_detail(conn, tag_name)
    if not detail["entities"]:
        return err(f"Tag '{tag_name}' not found or has no entities", 404)
    detail["entities"] = _localise_many(conn, detail["entities"], resolved)
    for key in detail["by_flavor"]:
        detail["by_flavor"][key] = _localise_many(conn, detail["by_flavor"][key], resolved)
    return ok(detail, meta=_lang_meta(resolved))


# ── Search ────────────────────────────────────────────────────────────────────

@app.get("/search", summary="Full-text search (translated results)")
@limiter.limit("60/minute")
async def search(
    request: Request,
    conn=Depends(db),
    q: str                         = Query(..., min_length=2),
    type: Optional[str]            = Query(None),
    limit: int                     = Query(20, ge=1, le=50),
    lang: Optional[str]            = Query(None),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """Searches base (English) text; returns results localised into requested lang."""
    resolved  = resolve_lang(lang, accept_language)

    rows = list_entities(conn, search=q, limit=limit)
    rows = _localise_many(conn, rows, resolved)

    return ok(
        {"query": q, "entries": rows, "count": len(rows)},
        meta=_lang_meta(resolved),
    )


# ── Relation graph ────────────────────────────────────────────────────────────

@app.get("/graph", summary="Entity-tag graph visualization")
@limiter.limit("60/minute")
async def graph(
    request: Request,
    conn=Depends(db),
):
    """
    Returns the complete entity-tag graph as nodes and links for visualization.
    Nodes include all entities (with flavor/category) and all tags (with tag_type).
    Links represent entity-to-tag relationships.
    
    Response format:
      {
        "nodes": [
          {"id": entity_id, "label": title, "type": "entity", "flavor": ..., "category": ...},
          {"id": "tag:Python", "label": "Python", "type": "tag", "tag_type": "technology"}
        ],
        "links": [
          {"source": entity_id, "target": "tag:Python", "type": "tagged"}
        ]
      }
    """
    graph_data = query_graph(conn)
    return ok({
        "nodes": graph_data["nodes"],
        "links": graph_data["links"],
        "node_count": len(graph_data["nodes"]),
        "link_count": len(graph_data["links"])
    })


# ─────────────────────────────────────────────────────────────────────────────
# PRIMARY ROUTES — SKILLS / TECHNOLOGIES / STAGES / OEUVRE
# ─────────────────────────────────────────────────────────────────────────────

# ── Skills ────────────────────────────────────────────────────────────────────

@app.get("/skills", summary="All skills with entity counts and metrics")
@limiter.limit("120/minute")
async def skills_list(
    request: Request,
    conn=Depends(db),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
    order_by: Optional[str]        = Query("relevance_score", description="Sort by: relevance_score | proficiency | entity_count | experience_years"),
    limit: int                     = Query(100, description="Maximum results"),
):
    """
    Returns every distinct skill tag with entity counts and calculated metrics.
    Skills are broad competencies: 'Data Analytics', 'Project Management',
    'GenAI', 'SEO', 'Automation', etc.
    
    Metrics include:
      - proficiency: Expertise level (0-100) based on recency and duration
      - experience_years: Total years of experience
      - frequency: How often skill appears across entities (0-1)
      - last_used: Most recent usage date
      - diversity_score: Variety of contexts (0-1)
      - growth_trend: increasing | stable | decreasing
      - distribution: Breakdown by entity flavor and category
      - relevance_score: Composite weighted score (0-100)
    
    Different from /technologies which lists specific tools & frameworks.
    Use /skills/{name} to drill into all entities with a given skill.
    """
    resolved = resolve_lang(lang, accept_language)
    skills = query_skills_with_metrics(conn, order_by=order_by, limit=limit)
    return ok(
        {"skills": skills, "count": len(skills)},
        meta=_lang_meta(resolved),
    )


@app.get("/skills/{skill_name}", summary="Entities with a specific skill and metrics")
@limiter.limit("120/minute")
async def skill_detail(
    request: Request,
    skill_name: str,
    conn=Depends(db),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """
    Returns all entities (jobs, projects, articles, education entries) that
    carry the requested skill, grouped by entity type.
    Also includes calculated metrics for this skill.
    Results are localised into the requested language.
    """
    resolved = resolve_lang(lang, accept_language)
    detail = query_skill_detail(conn, skill_name)
    if not detail["entities"]:
        return err(f"No entities found with skill '{skill_name}'", 404)
    detail["entities"] = _localise_many(conn, detail["entities"], resolved)
    for key in detail["by_flavor"]:
        detail["by_flavor"][key] = _localise_many(conn, detail["by_flavor"][key], resolved)
    
    # Add metrics
    metrics = get_tag_metrics(conn, skill_name, "skill")
    if metrics:
        detail["metrics"] = metrics
    
    return ok(detail, meta=_lang_meta(resolved))


# ── Technology ───────────────────────────────────────────────────────────────

@app.get("/technology", summary="All technologies with entity counts and metrics")
@limiter.limit("120/minute")
async def technologies_list(
    request: Request,
    conn=Depends(db),
    category: Optional[str] = Query(
        None,
        description="Filter by tech category: language | framework | platform | tool | cloud | database",
    ),
    order_by: Optional[str] = Query("relevance_score", description="Sort by: relevance_score | proficiency | entity_count | experience_years"),
    limit: int = Query(100, description="Maximum results"),
):
    """
    Returns every distinct technology tag with entity counts and calculated metrics.
    Technologies are specific tools, frameworks, and platforms:
    'Adobe Analytics', 'Python', 'Docker', 'FastAPI', etc.
    
    Metrics include proficiency, experience_years, frequency, last_used,
    diversity_score, growth_trend, distribution, and relevance_score.

    Optionally filter by category. Use /technology/{name} for full context.
    Note: technology names are universal — no translation applied.
    """
    technologies = query_technologies_with_metrics(conn, category=category, order_by=order_by, limit=limit)
    return ok({"technologies": technologies, "count": len(technologies)})


@app.get("/technology/{tech_name}", summary="Entities that used a technology with metrics")
@limiter.limit("120/minute")
async def technology_detail(
    request: Request,
    tech_name: str,
    conn=Depends(db),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """
    Returns all entities that used this technology, plus the technology
    entity itself (with category, proficiency). Grouped by entity type.
    Also includes calculated metrics for this technology.
    Entity titles/descriptions are localised; tech name stays in its original form.
    """
    resolved = resolve_lang(lang, accept_language)
    detail = query_technology_detail(conn, tech_name)
    if not detail["entities"] and not detail["tech_entity"]:
        return err(f"Technology '{tech_name}' not found", 404)
    detail["entities"] = _localise_many(conn, detail["entities"], resolved)
    for key in detail["by_flavor"]:
        detail["by_flavor"][key] = _localise_many(conn, detail["by_flavor"][key], resolved)
    
    # Add metrics
    metrics = get_tag_metrics(conn, tech_name, "technology")
    if metrics:
        detail["metrics"] = metrics
    
    return ok(detail, meta=_lang_meta(resolved))


# ── Stages ────────────────────────────────────────────────────────────────────

@app.get("/stages", summary="Career + education timeline")
@limiter.limit("120/minute")
async def stages_list(
    request: Request,
    conn=Depends(db),
    category: Optional[str] = Query(
        None,
        description="Filter by category: education | job",
    ),
    tag: Optional[str] = Query(
        None,
        description="Filter by generic tag",
    ),
    skill: Optional[str] = Query(
        None,
        description="Filter by skill tag",
    ),
    technology: Optional[str] = Query(
        None,
        description="Filter by technology tag",
    ),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """
    Returns the complete career and education timeline — every job, role,
    degree, and course. Each stage includes:
      - title, flavor, category
      - start_date / end_date  (ISO-8601)
      - is_current flag
      - skills: list of competencies applied or learned
      - technologies: list of tools/frameworks used
      - url + source

    Ordered newest-first. Filter by category for just jobs or just education.
    Filter by tag/skill/technology to find stages with specific attributes.
    Use /stages/{id} for full detail.
    """
    resolved = resolve_lang(lang, accept_language)
    stages = query_stages(conn, category=category, tag=tag, skill=skill, technology=technology)
    stages = _localise_many(conn, stages, resolved)
    return ok(
        {"stages": stages, "count": len(stages)},
        meta=_lang_meta(resolved),
    )


@app.get("/stages/{entity_id}", summary="Single career/education stage")
@limiter.limit("200/minute")
async def stage_detail(
    request: Request,
    entity_id: str,
    conn=Depends(db),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """
    Full detail for one career or education stage:
      - All base fields + typed extension
      - Skills and technologies (typed tags, not mixed with general tags)
      - Related entities (company, institution, connected projects)
    """
    resolved = resolve_lang(lang, accept_language)
    detail = get_entity(conn, entity_id)
    if not detail or detail.get("flavor") != "stages":
        raise HTTPException(404, "Stage not found")
    
    # Apply translation
    detail = _localise(conn, detail, resolved)
    
    return ok(detail, meta=_lang_meta(resolved))


# ── Oeuvre ───────────────────────────────────────────────────────────────────

@app.get("/oeuvre", summary="Side projects + literature")
@limiter.limit("120/minute")
async def oeuvre_list(
    request: Request,
    conn=Depends(db),
    category: Optional[str] = Query(
        None,
        description="Filter by category: coding | blog_post | article | book | website",
    ),
    tag: Optional[str] = Query(
        None,
        description="Filter by generic tag",
    ),
    skill: Optional[str] = Query(
        None,
        description="Filter by skill tag",
    ),
    technology: Optional[str] = Query(
        None,
        description="Filter by technology tag",
    ),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """
    Returns all oeuvre: GitHub projects, blog posts, Medium articles,
    books, podcasts. Each item includes:
      - title, category, description
      - url (direct link to the work)
      - source (github | medium | blog | manual)
      - date (publish date or repo creation)
      - skills: competencies this work demonstrates
      - technologies: tools used or discussed

    Use /oeuvre/{id} for full detail.
    Filter by tag/skill/technology to find work with specific attributes.
    """
    resolved = resolve_lang(lang, accept_language)
    items = query_oeuvre(conn, category=category, tag=tag, skill=skill, technology=technology)
    items = _localise_many(conn, items, resolved)
    return ok(
        {"oeuvre": items, "count": len(items)},
        meta=_lang_meta(resolved),
    )


@app.get("/oeuvre/{entity_id}", summary="Single oeuvre item detail")
@limiter.limit("200/minute")
async def oeuvre_detail(
    request: Request,
    entity_id: str,
    conn=Depends(db),
    lang: Optional[str]            = Query(None, description="en | de"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
):
    """
    Full detail for one oeuvre item (project or article):
      - All base fields + typed extension (stars, forks, platform, etc.)
      - Skills demonstrated
      - Technologies used
      - Related career stages (e.g. built while working at VML)
      - Source URL and any related URLs
    """
    resolved = resolve_lang(lang, accept_language)
    detail = get_entity(conn, entity_id)
    if not detail or detail.get("flavor") != "oeuvre":
        raise HTTPException(404, "Oeuvre item not found")
    
    # Apply translation
    detail = _localise(conn, detail, resolved)
    
    return ok(detail, meta=_lang_meta(resolved))
