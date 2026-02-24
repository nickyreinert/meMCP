"""
app/main.py — Personal MCP FastAPI Server v2.2
===============================================
All routes return structured JSON. Entity graph is the backbone.

MCP Compatibility:
  /schema                        → Explicit data model definition
  /index                         → Deterministic discovery root (all entity links)
  /coverage                      → Machine-readable coverage contract
  X-Coverage-* headers           → Coverage metadata on every response

Language support:
  Every data route accepts language via two mechanisms (priority order):
    1. ?lang=de  query parameter
    2. Accept-Language: de-DE,de;q=0.9  HTTP header
  Falls back to 'en' if the requested lang has no translations stored.
  Technology entity names are never translated (they are universal).
  Every response includes a "_lang" meta field.

Routes:
  GET /                          → API index (JSON)
  GET /human                     → Human-friendly doc (hidden endpoint, not in API index)
  GET /schema                    → Data model schema (MCP)
  GET /index                     → Discovery root with all entity links (MCP)
  GET /coverage                  → Coverage contract (MCP)
  GET /prompts                   → List all MCP prompt templates
  GET /prompts/{id}              → Get specific prompt template
  GET /mcp/tools                 → List available MCP tools for programmatic queries
  POST /mcp/tools/call           → Execute an MCP tool with arguments
  GET /greeting                  → identity card (translated)
  GET /categories                → entity type list + counts
  GET /entities                  → paginated entity list (translated)
  GET /entities/{id}             → single entity + relations (translated)
  GET /entities/{id}/related     → graph neighbours (translated)
  GET /category/{type}           → entities by type (translated)
  GET /technology                → all technology entities
  GET /technology/{tag}          → tech cross-reference (translated)
  GET /tags                      → all tags + counts
  GET /search?q=                 → full-text search (translated)
  GET /graph                     → relation graph query
  GET /languages                 → supported languages + translation coverage
  GET /skills                    → all skills + entity counts
  GET /skills/{name}             → detail: entities with this skill
  GET /technologies              → all technologies + entity counts (alias for /technology)
  GET /stages                    → career timeline (jobs + education)
  GET /stages/{id}               → single stage detail
  GET /oeuvre                    → side projects + literature
  GET /oeuvre/{id}               → single work item detail
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
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
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
from app.routers import mcp, internal
from app.dependencies.access_control import require_private_access, TokenInfo, build_endpoint_guard


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG & TEMPLATES
# ─────────────────────────────────────────────────────────────────────────────

def load_config():
    """Load and merge config.tech.yaml + config.content.yaml from project root."""
    from config_loader import load_config as _load
    return _load(root=Path(__file__).parent.parent)

def load_prompts():
    """Load prompts.yaml from project root."""
    prompts_path = Path(__file__).parent.parent / "prompts.yaml"
    with open(prompts_path) as f:
        data = yaml.safe_load(f)
        return data.get("prompts", [])

CONFIG = load_config()
PROMPTS = load_prompts()
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

def _rate_limit_key(request: Request) -> str:
    """Use token hash as rate limit key for authenticated requests, IP otherwise."""
    import hashlib
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):].strip()
        if token:
            return f"token:{hashlib.sha256(token.encode()).hexdigest()[:16]}"
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key, default_limits=["120/minute"])


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

# Security settings from config (with safe defaults)
_security_cfg = CONFIG.get("security", {})
_trusted_proxies = _security_cfg.get("trusted_proxies", ["127.0.0.1", "::1"])
_cors_origins = _security_cfg.get("cors_origins", [BASE_URL])

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_proxies)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Include routers
app.include_router(mcp.router)
app.include_router(internal.router)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT PROTECTION MIDDLEWARE  (config-driven, runs before session tracking)
# ─────────────────────────────────────────────────────────────────────────────

_endpoint_guard = build_endpoint_guard(CONFIG.get("protected_endpoints", {}))


@app.middleware("http")
async def endpoint_protection_middleware(request: Request, call_next):
    """
    Config-driven access control gate.

    Reads ``protected_endpoints`` from config.yaml and enforces the required
    token tier for each listed path before any route handler or session-tracking
    middleware runs. Unlisted paths pass through without restriction.
    """
    return await _endpoint_guard(request, call_next)


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

@app.get("/", summary="API index")
@limiter.limit("120/minute")
async def index(request: Request):
    """
    API root endpoint — returns machine-readable JSON index.
    
    For human-friendly documentation, visit /human (not linked in API responses).
    Resets session tracking when accessed.
    """
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
        "mcp": {
            "schema":    "/schema — Explicit data model definition (MCP-compliant)",
            "index":     "/index — Discovery root with all entity links",
            "coverage":  "/coverage — Coverage contract (JSON)",
            "prompts":   "/prompts — MCP prompt templates for guided LLM interactions",
            "tools":     "/mcp/tools — Programmatic database query tools (read-only)",
            "resources": "/mcp/resources — Browsable data resources mapped to REST endpoints",
        },
        "routes": {
            "/greeting":               "Identity card — name, tagline, bio, links",
            "/coverage":               "Session coverage report — see which endpoints you've visited and what's missing",
            "/prompts":                "List all available MCP prompt templates (summarized)",
            "/prompts/{id}":           "Get specific prompt template with full text",
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
    return JSONResponse(content=ok(data), media_type="application/json")


@app.get("/human", response_class=HTMLResponse)
async def human_endpoint(request: Request):
    """
    Human-friendly instructions page.
    
    Note: This endpoint is intentionally not listed in API index responses.
    It's documented here only as 'human doc' for manual browsing.
    """
    return templates.TemplateResponse(
        "human.html",
        {"request": request, "base_url": BASE_URL}
    )


@app.get("/index", summary="MCP discovery root")
@app.get("/root", summary="Discovery root (alias for /index)", include_in_schema=False)
@app.get("/discover", summary="Discovery endpoint (alias for /index)", include_in_schema=False)
@limiter.limit("120/minute")
async def index_endpoint(request: Request, conn=Depends(db)):
    """
    Deterministic discovery root for MCP (Machine Context Protocol).
    
    Available at: /index, /root, /discover (all return the same discovery data)
    
    Returns direct links to all entities across all flavors.
    No hidden entities. No pagination (all entities returned).
    
    This endpoint provides complete entity graph enumeration for
    machine traversal and coverage verification.
    """
    # Get all public entities
    stages = conn.execute(
        "SELECT id, title FROM entities WHERE flavor='stages' AND visibility='public' ORDER BY start_date DESC"
    ).fetchall()
    
    oeuvre = conn.execute(
        "SELECT id, title FROM entities WHERE flavor='oeuvre' AND visibility='public' ORDER BY date DESC"
    ).fetchall()
    
    # Get all unique skills
    skills = conn.execute(
        "SELECT DISTINCT tag FROM tags WHERE tag_type='skill' ORDER BY tag"
    ).fetchall()
    
    # Get all unique technologies
    technologies = conn.execute(
        "SELECT DISTINCT tag FROM tags WHERE tag_type='technology' ORDER BY tag"
    ).fetchall()
    
    # Get all unique generic tags
    tags = conn.execute(
        "SELECT DISTINCT tag FROM tags WHERE tag_type='generic' ORDER BY tag"
    ).fetchall()
    
    return ok({
        "discovery_root": True,
        "version": APP_VERSION,
        "total_entities": len(stages) + len(oeuvre),
        "stages": [
            {"id": row['id'], "title": row['title'], "url": f"/stages/{row['id']}"}
            for row in stages
        ],
        "oeuvre": [
            {"id": row['id'], "title": row['title'], "url": f"/oeuvre/{row['id']}"}
            for row in oeuvre
        ],
        "skills": [
            {"name": row['tag'], "url": f"/skills/{row['tag']}"}
            for row in skills
        ],
        "technologies": [
            {"name": row['tag'], "url": f"/technology/{row['tag']}"}
            for row in technologies
        ],
        "tags": [
            {"name": row['tag'], "url": f"/tags/{row['tag']}"}
            for row in tags
        ],
        "collections": {
            "stages": "/stages",
            "oeuvre": "/oeuvre",
            "skills": "/skills",
            "technologies": "/technology",
            "tags": "/tags"
        }
    })

@app.get("/health")
async def health():
    return {"status": "ok", "ts": time.time(), "version": APP_VERSION}


# ─────────────────────────────────────────────────────────────────────────────
# MCP PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/prompts", summary="List all MCP prompt templates")
@limiter.limit("120/minute")
async def list_prompts(request: Request):
    """
    Returns all available MCP prompt templates (summarized, without full template).
    
    Prompts are reusable templates that guide LLMs and users on how to effectively
    interact with the meMCP server to accomplish specific tasks like building resumes,
    analyzing skills, or generating visualizations.
    
    Use GET /prompts/{prompt_id} to retrieve the full template.
    """
    prompts_summary = [
        {
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "use_case": p["use_case"],
            "url": f"/prompts/{p['id']}"
        }
        for p in PROMPTS
    ]
    return ok({
        "prompts": prompts_summary,
        "count": len(prompts_summary)
    })


@app.get("/prompts/{prompt_id}", summary="Get specific MCP prompt template")
@limiter.limit("120/minute")
async def get_prompt(
    request: Request,
    prompt_id: str,
    token_info: TokenInfo = Depends(require_private_access),
):
    """
    Returns the complete prompt template for a specific prompt ID.

    Requires a valid token (Private stage).

    The response includes:
      - id: unique identifier
      - name: human-readable name
      - description: brief explanation
      - use_case: when/why to use this prompt
      - prompt_template: the full template text to use

    This template can be directly used by LLMs or adapted by users for their needs.
    """
    prompt = next((p for p in PROMPTS if p["id"] == prompt_id), None)
    if not prompt:
        raise HTTPException(404, f"Prompt '{prompt_id}' not found")

    return ok(prompt, meta={"access_stage": token_info.stage})


@app.get("/schema", summary="MCP data model schema")
@app.get("/openapi.json", summary="OpenAPI schema (alias for /schema)", include_in_schema=False)
@app.get("/model", summary="Data model (alias for /schema)", include_in_schema=False)
@limiter.limit("120/minute")
async def schema(request: Request):
    """
    Machine-discoverable data model for MCP (Machine Context Protocol) compatibility.
    
    Available at: /schema, /openapi.json, /model (all return the same schema)
    
    Defines the complete object graph, relations, and analytics fields.
    This endpoint is canonical for understanding the structure of all data returned by this API.
    """
    return ok({
        "version": "1.0",
        "description": "Personal profile entity graph with multi-language support",
        "scoring_semantics": {
            "description": "Field definitions for analytics and scoring calculations",
            "fields": {
                "proficiency": {
                    "type": "number",
                    "range": [0, 100],
                    "unit": "score",
                    "description": "Recency-weighted experience level",
                    "calculation": "Composite of experience_years × recency_weight × frequency_weight",
                    "usage": "Primary skill/technology mastery indicator"
                },
                "experience_years": {
                    "type": "number",
                    "unit": "years",
                    "description": "Total cumulative experience duration",
                    "calculation": "Sum of all stage durations (end_date - start_date) where tag appears",
                    "usage": "Raw experience metric without recency adjustment"
                },
                "frequency": {
                    "type": "number",
                    "range": [0, 1],
                    "unit": "ratio",
                    "description": "Relative occurrence rate",
                    "calculation": "entity_count / total_entities_with_tags",
                    "usage": "How common this skill/technology is across portfolio"
                },
                "recency": {
                    "type": "number",
                    "unit": "days",
                    "description": "Time since last usage",
                    "calculation": "current_date - last_used (ISO-8601 date field)",
                    "usage": "Currency indicator; lower = more recent"
                },
                "last_used": {
                    "type": "string",
                    "format": "ISO-8601",
                    "description": "Most recent entity date using this tag",
                    "calculation": "MAX(entity.date or entity.end_date) for all entities with this tag",
                    "usage": "Source field for recency calculation"
                },
                "diversity_score": {
                    "type": "number",
                    "range": [0, 1],
                    "unit": "score",
                    "description": "Variety of contexts in which tag appears",
                    "calculation": "Normalized entropy across entity flavors and categories",
                    "usage": "Breadth indicator; 1.0 = used across many contexts"
                },
                "growth_trend": {
                    "type": "string",
                    "enum": ["increasing", "stable", "decreasing"],
                    "description": "Usage trajectory over time",
                    "calculation": "Linear regression of usage frequency over time windows",
                    "usage": "Identifies emerging vs declining skills"
                },
                "active": {
                    "type": "boolean",
                    "description": "Whether skill/technology is currently in use",
                    "calculation": "EXISTS(entity with this tag WHERE is_current=1 OR recency < 365)",
                    "usage": "Filters for current skillset"
                },
                "relevance_score": {
                    "type": "number",
                    "range": [0, 100],
                    "unit": "score",
                    "description": "Composite relevance ranking",
                    "calculation": "weighted_sum(proficiency × 0.4, frequency × 0.3, recency_adjusted × 0.3)",
                    "usage": "Primary sorting metric for skills/technologies"
                },
                "entity_count": {
                    "type": "integer",
                    "unit": "count",
                    "description": "Number of entities tagged with this skill/technology",
                    "calculation": "COUNT(DISTINCT entity_id) WHERE tag = this_tag",
                    "usage": "Absolute usage metric"
                }
            },
            "reference_date": {
                "description": "All time-based calculations use current UTC date as reference",
                "format": "ISO-8601",
                "source": "datetime.now(timezone.utc)"
            }
        },
        "entity_types": {
            "personal": {
                "description": "Static identity information",
                "primary_key": "id",
                "categories": None,
                "fields": {
                    "id": {"type": "string", "format": "uuid", "required": True},
                    "flavor": {"type": "string", "enum": ["personal"], "required": True},
                    "title": {"type": "string", "required": True},
                    "description": {"type": "string", "nullable": True},
                    "url": {"type": "string", "format": "uri", "nullable": True},
                    "canonical_url": {"type": "string", "format": "uri", "nullable": True},
                    "source": {"type": "string", "nullable": True},
                    "visibility": {"type": "string", "enum": ["public", "private"], "default": "public"},
                    "tags": {"type": "array", "items": {"$ref": "#/tag_types"}},
                    "created_at": {"type": "string", "format": "date-time", "required": True},
                    "updated_at": {"type": "string", "format": "date-time", "required": True}
                }
            },
            "stages": {
                "description": "Career and education timeline with temporal data",
                "primary_key": "id",
                "categories": ["education", "job"],
                "temporal_semantics": {
                    "start_date": {
                        "field": "start_date",
                        "format": "ISO-8601 (full or partial: YYYY, YYYY-MM, YYYY-MM-DD)",
                        "description": "When this stage began"
                    },
                    "end_date": {
                        "field": "end_date",
                        "format": "ISO-8601 or null for ongoing",
                        "description": "When this stage ended (null = ongoing)"
                    },
                    "is_current": {
                        "field": "is_current",
                        "type": "boolean",
                        "description": "Whether this stage is currently active (derived: end_date IS NULL)"
                    },
                    "recency": {
                        "description": "Calculated from end_date (or current date if ongoing)",
                        "unit": "days",
                        "reference_date": "current UTC date"
                    },
                    "duration": {
                        "description": "Time between start_date and end_date (or current date)",
                        "unit": "years",
                        "used_for": "experience_years calculation"
                    }
                },
                "fields": {
                    "id": {"type": "string", "format": "uuid", "required": True},
                    "flavor": {"type": "string", "enum": ["stages"], "required": True},
                    "category": {"type": "string", "enum": ["education", "job"], "nullable": True},
                    "title": {"type": "string", "required": True},
                    "description": {"type": "string", "nullable": True},
                    "url": {"type": "string", "format": "uri", "nullable": True},
                    "canonical_url": {"type": "string", "format": "uri", "nullable": True},
                    "source": {"type": "string", "nullable": True},
                    "start_date": {"type": "string", "format": "ISO-8601", "nullable": True},
                    "end_date": {"type": "string", "format": "ISO-8601", "nullable": True, "description": "null = ongoing"},
                    "is_current": {"type": "integer", "enum": [0, 1], "default": 0},
                    "visibility": {"type": "string", "enum": ["public", "private"], "default": "public"},
                    "tags": {"type": "array", "items": {"$ref": "#/tag_types"}},
                    "created_at": {"type": "string", "format": "date-time", "required": True},
                    "updated_at": {"type": "string", "format": "date-time", "required": True}
                },
                "analytics_fields": ["start_date", "end_date", "is_current"]
            },
            "oeuvre": {
                "description": "Work portfolio: projects, articles, publications",
                "primary_key": "id",
                "categories": ["coding", "blog_post", "article", "book", "website"],
                "temporal_semantics": {
                    "date": {
                        "field": "date",
                        "format": "ISO-8601",
                        "description": "Publication or creation date"
                    },
                    "recency": {
                        "description": "Days since publication",
                        "unit": "days",
                        "reference_date": "current UTC date"
                    }
                },
                "fields": {
                    "id": {"type": "string", "format": "uuid", "required": True},
                    "flavor": {"type": "string", "enum": ["oeuvre"], "required": True},
                    "category": {"type": "string", "enum": ["coding", "blog_post", "article", "book", "website"], "nullable": True},
                    "title": {"type": "string", "required": True},
                    "description": {"type": "string", "nullable": True},
                    "url": {"type": "string", "format": "uri", "nullable": True},
                    "canonical_url": {"type": "string", "format": "uri", "nullable": True},
                    "source": {"type": "string", "nullable": True},
                    "date": {"type": "string", "format": "ISO-8601", "nullable": True},
                    "visibility": {"type": "string", "enum": ["public", "private"], "default": "public"},
                    "tags": {"type": "array", "items": {"$ref": "#/tag_types"}},
                    "created_at": {"type": "string", "format": "date-time", "required": True},
                    "updated_at": {"type": "string", "format": "date-time", "required": True}
                },
                "analytics_fields": ["date"]
            },
            "identity": {
                "description": "Profile owner identity (name, bio, contact, links)",
                "primary_key": "id",
                "categories": ["basic", "links", "contact"],
                "fields": {
                    "id": {"type": "string", "format": "uuid", "required": True},
                    "flavor": {"type": "string", "enum": ["identity"], "required": True},
                    "category": {"type": "string", "enum": ["basic", "links", "contact"], "nullable": True},
                    "title": {"type": "string", "required": True},
                    "raw_data": {"type": "object", "description": "Multi-language structured data (JSON)"},
                    "created_at": {"type": "string", "format": "date-time", "required": True},
                    "updated_at": {"type": "string", "format": "date-time", "required": True}
                }
            }
        },
        "tag_types": {
            "technology": {
                "description": "Programming languages, frameworks, tools (e.g., Python, Docker, React)",
                "fields": {
                    "tag": {"type": "string", "required": True},
                    "tag_type": {"type": "string", "enum": ["technology"], "required": True}
                },
                "analytics_endpoint": "/technology/{name}",
                "analytics_fields": {
                    "proficiency": {
                        "type": "number",
                        "range": [0, 100],
                        "description": "Recency-weighted experience score",
                        "algorithm": "Weighted by usage recency and duration"
                    },
                    "experience_years": {
                        "type": "number",
                        "description": "Total years of experience with this technology",
                        "algorithm": "Sum of stage durations where this tech was tagged"
                    },
                    "entity_count": {
                        "type": "integer",
                        "description": "Number of entities tagged with this technology"
                    },
                    "frequency": {
                        "type": "number",
                        "range": [0, 1],
                        "description": "Occurrence rate across all entities"
                    },
                    "last_used": {
                        "type": "string",
                        "format": "ISO-8601",
                        "description": "Date of most recent entity using this technology"
                    },
                    "diversity_score": {
                        "type": "number",
                        "range": [0, 1],
                        "description": "Variety of contexts in which this technology appears"
                    },
                    "growth_trend": {
                        "type": "string",
                        "enum": ["increasing", "stable", "decreasing"],
                        "description": "Usage trend over time"
                    },
                    "relevance_score": {
                        "type": "number",
                        "range": [0, 100],
                        "description": "Composite score combining proficiency, recency, and frequency"
                    }
                }
            },
            "skill": {
                "description": "Competencies and expertise areas (e.g., Data Analytics, SEO, Project Management)",
                "fields": {
                    "tag": {"type": "string", "required": True},
                    "tag_type": {"type": "string", "enum": ["skill"], "required": True}
                },
                "analytics_endpoint": "/skills/{name}",
                "analytics_fields": {
                    "proficiency": {
                        "type": "number",
                        "range": [0, 100],
                        "description": "Recency-weighted experience score"
                    },
                    "experience_years": {
                        "type": "number",
                        "description": "Total years of experience with this skill"
                    },
                    "entity_count": {
                        "type": "integer",
                        "description": "Number of entities tagged with this skill"
                    },
                    "frequency": {
                        "type": "number",
                        "range": [0, 1],
                        "description": "Occurrence rate across all entities"
                    },
                    "last_used": {
                        "type": "string",
                        "format": "ISO-8601",
                        "description": "Date of most recent entity using this skill"
                    },
                    "diversity_score": {
                        "type": "number",
                        "range": [0, 1],
                        "description": "Variety of contexts in which this skill appears"
                    },
                    "growth_trend": {
                        "type": "string",
                        "enum": ["increasing", "stable", "decreasing"],
                        "description": "Usage trend over time"
                    },
                    "relevance_score": {
                        "type": "number",
                        "range": [0, 100],
                        "description": "Composite score combining proficiency, recency, and frequency"
                    }
                }
            },
            "generic": {
                "description": "General tags and topics",
                "fields": {
                    "tag": {"type": "string", "required": True},
                    "tag_type": {"type": "string", "enum": ["generic"], "required": True}
                },
                "analytics_endpoint": "/tags/{name}"
            }
        },
        "relations": {
            "description": "Entities are related via shared tags (many-to-many)",
            "mechanism": "Tags table (entity_id → tag + tag_type)",
            "query_endpoint": "/graph"
        },
        "endpoints": {
            "discovery_root": "/index (also: /root, /discover)",
            "data_model": "/schema (also: /openapi.json, /model)",
            "coverage_contract": "/coverage",
            "entity_collections": {
                "/stages": "List all career and education stages",
                "/stages/{id}": "Single stage detail",
                "/oeuvre": "List all work portfolio items",
                "/oeuvre/{id}": "Single oeuvre item detail",
                "/skills": "List all skills with analytics",
                "/skills/{name}": "Entities with specific skill + analytics",
                "/technology": "List all technologies with analytics (also: /technologies)",
                "/technology/{name}": "Entities with specific technology + analytics",
                "/tags": "List all generic tags",
                "/tags/{name}": "Entities with specific tag"
            },
            "metadata": {
                "/greeting": "Identity card (multi-language)",
                "/languages": "Translation coverage",
                "/categories": "Entity flavor + category counts"
            }
        }
    })


@app.get("/coverage", summary="Session coverage report")
@limiter.limit("200/minute")
async def coverage_report(request: Request, conn=Depends(db)):
    """
    Returns machine-readable coverage report for MCP compatibility.
    
    Shows which endpoints have been visited and which are missing,
    with entity-level detail for verifiability.
    
    Response includes:
      - coverage: Overall coverage percentage (0-100)
      - missing: List of unvisited endpoints with reasons
      - total_entities: Total number of entities in database
      - fetched_entities: Number of unique entities accessed
      - session_detail: Extended session information (metadata)
    
    Note: Coverage is based on relevant endpoints that have metrics.
    Not all API endpoints count towards coverage.
    """
    if not session_tracker:
        # If tracking disabled, return base coverage contract
        total = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE visibility='public'"
        ).fetchone()[0]
        
        return ok({
            "coverage": 0,
            "missing": [],
            "total_entities": total,
            "fetched_entities": 0,
            "coverage_is_relevant": False,
            "message": "Session tracking is disabled in config.yaml"
        })
    
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    # Get detailed coverage from session tracker
    coverage_data = session_tracker.get_coverage(client_ip, user_agent)
    
    # Get total entity counts
    total_entities = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE visibility='public'"
    ).fetchone()[0]
    
    # Estimate fetched entities from visited endpoints
    # This is approximate - we track endpoint visits, not individual entity fetches
    visited_endpoints = len([
        item for item in coverage_data.get('breakdown', [])
        if item.get('visited', False)
    ])
    
    # Build MCP-compliant missing list
    missing = []
    if 'missing_endpoints' in coverage_data:
        for item in coverage_data['missing_endpoints']:
            missing.append({
                "endpoint": item['endpoint'],
                "reason": "not visited yet",
                "paginated": item.get('paginated', False)
            })
    
    if 'incomplete_endpoints' in coverage_data:
        for item in coverage_data['incomplete_endpoints']:
            missing.append({
                "endpoint": item['endpoint'],
                "reason": f"partial coverage ({item['coverage_pct']}%) - visit more pages",
                "pages_visited": item['pages_visited'],
                "coverage_pct": item['coverage_pct']
            })
    
    # MCP-compliant response
    response = {
        "coverage": int(coverage_data.get('coverage', {}).get('percentage', 0)),
        "missing": missing,
        "total_entities": total_entities,
        "fetched_entities": visited_endpoints,  # Approximate
        "coverage_is_relevant": True,
        # Extended metadata (not in MCP spec, but useful)
        "session_detail": {
            "visitor_id": coverage_data.get('visitor_id'),
            "session_exists": coverage_data.get('session_exists', False),
            "session": coverage_data.get('session'),
            "breakdown": coverage_data.get('breakdown', [])
        }
    }
    
    return JSONResponse(content=ok(response), media_type="application/json")


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
    direction: str                  = Query("both", pattern="^(in|out|both)$"),
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
@app.get("/technologies", summary="Technologies list (alias for /technology)", include_in_schema=False)
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
    
    Available at: /technology, /technologies (both return the same data)
    
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
