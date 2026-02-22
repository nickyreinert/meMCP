"""
app/routers/mcp.py — MCP Tools & Resources Router
==================================================

Purpose:
  MCP (Model Context Protocol) endpoints for programmatic database queries and resource access.
  Provides read-only tools and resources that enable LLMs to query the database without SQL.

Endpoints:
  GET /mcp/tools            → List available tool definitions with schemas
  POST /mcp/tools/call      → Execute a tool with arguments
  GET /mcp/resources        → List available MCP resources (REST endpoint mappings)
  GET /mcp/resources/read   → Read a specific resource by URI

Tool execution flow:
  1. Client sends tool name + arguments
  2. Router validates tool exists
  3. execute_tool() maps to database query functions
  4. Results returned as structured JSON

Resource resolution flow:
  1. LLM requests a resource URI (e.g., me://profile/greeting)
  2. Server maps URI to internal REST endpoint
  3. Executes endpoint logic and returns data in MCP content envelope

Security:
  - All operations are READ-ONLY
  - Uses dependency injection for database connections
  - Input validation on all arguments

Dependencies:
  - app.mcp_tools: Tool registry and execution logic
  - db.models: Database query functions via dependency injection
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Query, Header
from slowapi import Limiter
from slowapi.util import get_remote_address
import json

from app.mcp_tools import get_tool_definitions, execute_tool
from app.dependencies.access_control import (
    require_private_access, TokenInfo, log_usage,
)
from db.models import get_db, DB_PATH


# --- ROUTER SETUP ---

router = APIRouter(prefix="/mcp", tags=["MCP Tools"])
limiter = Limiter(key_func=get_remote_address)


# --- HELPER FUNCTIONS ---

def ok(data: dict, meta: Optional[dict] = None) -> dict:
    """Standard success response format."""
    resp = {"status": "success", "data": data}
    if meta:
        resp["meta"] = meta
    return resp


def db():
    """Database connection dependency."""
    conn = get_db(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def get_resource_definitions() -> list:
    """
    Define all available MCP resources mapping to existing REST endpoints.
    
    Resources represent stable, browsable data sources that LLMs can discover
    and read programmatically through the MCP Resource protocol.
    
    Returns:
        List of resource definitions with URI, name, description, and internal route.
    """
    return [
        {
            "uri": "me://profile/greeting",
            "name": "Identity Card",
            "description": "Basic profile info: name, tagline, bio, location, contact, links",
            "mimeType": "application/json",
            "route": "/greeting"
        },
        {
            "uri": "me://profile/stages",
            "name": "Career Timeline",
            "description": "Complete career and education history with dates, companies, roles, skills, technologies",
            "mimeType": "application/json",
            "route": "/stages"
        },
        {
            "uri": "me://profile/technology_stack",
            "name": "Technology Stack",
            "description": "All technologies used with proficiency metrics, experience years, and project counts",
            "mimeType": "application/json",
            "route": "/technology"
        },
        {
            "uri": "me://profile/skills",
            "name": "Skills & Expertise",
            "description": "Professional skills with proficiency, experience, and entity associations",
            "mimeType": "application/json",
            "route": "/skills"
        },
        {
            "uri": "me://profile/oeuvre",
            "name": "Portfolio & Publications",
            "description": "Side projects, articles, publications, and creative work",
            "mimeType": "application/json",
            "route": "/oeuvre"
        },
        {
            "uri": "me://profile/categories",
            "name": "Entity Categories",
            "description": "Overview of all entity types and their counts",
            "mimeType": "application/json",
            "route": "/categories"
        },
        {
            "uri": "me://profile/tags",
            "name": "Tag Cloud",
            "description": "All tags used across entities with usage counts",
            "mimeType": "application/json",
            "route": "/tags"
        },
        {
            "uri": "me://profile/languages",
            "name": "Language Support",
            "description": "Supported languages and translation coverage statistics",
            "mimeType": "application/json",
            "route": "/languages"
        },
    ]


# --- ENDPOINTS ---

@router.get("/tools", summary="List available MCP tools")
@limiter.limit("120/minute")
async def list_mcp_tools(request: Request):
    """
    Returns all available MCP tools for programmatic database queries.
    
    Tools enable LLMs to query the database without raw SQL access.
    Each tool includes name, description, and JSON Schema for arguments.
    
    Available tools:
      - query_stages: Query career history (education, jobs)
      - query_portfolio: Query portfolio items (repos, articles, books)
      - get_technology_metrics: Get proficiency metrics for a technology
      - query_skills: List all skills with proficiency metrics
      - search_entities: Full-text search across all entities
    
    Execute tools via POST /mcp/tools/call with tool name and arguments.
    
    Response format:
    {
        "status": "success",
        "data": {
            "tools": [
                {
                    "name": "tool_name",
                    "description": "Tool description",
                    "inputSchema": { ... JSON Schema ... }
                }
            ],
            "count": 5,
            "execution_endpoint": "/mcp/tools/call"
        }
    }
    """
    tools = get_tool_definitions()
    return ok({
        "tools": tools,
        "count": len(tools),
        "execution_endpoint": "/mcp/tools/call"
    })


@router.post("/tools/call", summary="Execute an MCP tool")
@limiter.limit("60/minute")
async def call_mcp_tool(
    request: Request,
    tool_request: dict,
    conn=Depends(db),
    token_info: TokenInfo = Depends(require_private_access),
):
    """
    Execute a specific MCP tool with provided arguments.
    
    Request body:
    {
        "tool": "tool_name",
        "arguments": {
            "arg1": "value1",
            "arg2": "value2"
        }
    }
    
    Returns:
    {
        "status": "success",
        "data": { ... tool-specific result ... }
    }
    
    All tools are READ-ONLY. No database modifications are possible.
    
    Examples:
      - query_stages:
        {"tool": "query_stages", "arguments": {"category": "job"}}
      
      - query_portfolio:
        {"tool": "query_portfolio", "arguments": {"flavor": "coding", "tag": "Python"}}
      
      - get_technology_metrics:
        {"tool": "get_technology_metrics", "arguments": {"tech_name": "JavaScript"}}
      
      - query_skills:
        {"tool": "query_skills", "arguments": {"min_proficiency": 50, "limit": 10}}
      
      - search_entities:
        {"tool": "search_entities", "arguments": {"query": "analytics", "limit": 10}}
    
    Error responses:
      - 400: Invalid arguments or missing required fields
      - 404: Unknown tool name
      - 500: Tool execution failure
    """
    # Validate request structure
    if "tool" not in tool_request:
        raise HTTPException(400, "Missing 'tool' field in request body")

    tool_name = tool_request["tool"]
    arguments = tool_request.get("arguments", {})

    # Log full body args now that they're parsed (supplements the endpoint-level log)
    log_usage(conn, token_info.id, request.url.path, tool_request)
    
    # Validate tool exists
    tool_names = [t["name"] for t in get_tool_definitions()]
    if tool_name not in tool_names:
        raise HTTPException(
            404, 
            f"Unknown tool '{tool_name}'. Available tools: {', '.join(tool_names)}"
        )
    
    # Execute tool
    try:
        result = execute_tool(conn, tool_name, arguments)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Tool execution failed: {str(e)}")


# --- MCP RESOURCE ENDPOINTS ---

@router.get("/resources", summary="List available MCP resources")
@limiter.limit("120/minute")
async def list_mcp_resources(request: Request):
    """
    Returns all available MCP resources for LLM browsing.
    
    Resources are stable, read-only data sources that map to existing REST endpoints.
    Each resource has a unique URI that can be resolved via GET /mcp/resources/read.
    
    Resources expose high-level profile data:
      - me://profile/greeting: Identity and contact information
      - me://profile/stages: Career timeline (jobs + education)
      - me://profile/technology_stack: Technologies with proficiency metrics
      - me://profile/skills: Skills and expertise areas
      - me://profile/oeuvre: Portfolio, articles, publications
      - me://profile/categories: Entity type overview
      - me://profile/tags: Tag cloud with counts
      - me://profile/languages: Translation support info
    
    To read a resource, use:
      GET /mcp/resources/read?uri=me://profile/greeting
    
    Response format:
    {
        "status": "success",
        "data": {
            "resources": [
                {
                    "uri": "me://profile/greeting",
                    "name": "Identity Card",
                    "description": "Basic profile info...",
                    "mimeType": "application/json"
                }
            ],
            "count": 8
        }
    }
    """
    resources = get_resource_definitions()
    
    # Remove internal route field from public response
    public_resources = [
        {
            "uri": r["uri"],
            "name": r["name"],
            "description": r["description"],
            "mimeType": r["mimeType"]
        }
        for r in resources
    ]
    
    return ok({
        "resources": public_resources,
        "count": len(public_resources)
    })


@router.get("/resources/read", summary="Read a specific MCP resource")
@limiter.limit("200/minute")
async def read_mcp_resource(
    request: Request,
    uri: str = Query(..., description="Resource URI (e.g., me://profile/greeting)"),
    lang: Optional[str] = Query(None, description="Language preference (en|de)"),
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
    conn=Depends(db),
    token_info: TokenInfo = Depends(require_private_access),
):
    """
    Resolve and read a specific MCP resource by URI.
    
    This endpoint bridges MCP Resource URIs to internal REST endpoints.
    It executes the corresponding route handler and wraps the result
    in the MCP content envelope format.
    
    Query Parameters:
      - uri: Resource URI (e.g., me://profile/greeting)
      - lang: Language preference (optional, defaults based on Accept-Language header)
    
    Response format (MCP content envelope):
    {
        "status": "success",
        "data": {
            "contents": [
                {
                    "uri": "me://profile/greeting",
                    "mimeType": "application/json",
                    "text": "{...JSON string of the resource data...}"
                }
            ]
        }
    }
    
    Supported URIs:
      - me://profile/greeting
      - me://profile/stages
      - me://profile/technology_stack
      - me://profile/skills
      - me://profile/oeuvre
      - me://profile/categories
      - me://profile/tags
      - me://profile/languages
    
    Error responses:
      - 404: Unknown resource URI
      - 500: Resource resolution failure
    """
    # Find matching resource definition
    resources = get_resource_definitions()
    resource = next((r for r in resources if r["uri"] == uri), None)
    
    if not resource:
        available_uris = [r["uri"] for r in resources]
        raise HTTPException(
            404,
            f"Unknown resource URI '{uri}'. Available: {', '.join(available_uris)}"
        )
    
    # Import main app to access route handlers and query functions
    try:
        from db.models import (
            get_greeting_translation, query_stages, query_oeuvre,
            query_skills_with_metrics, query_technologies_with_metrics,
            list_all_tags, SUPPORTED_LANGS, DEFAULT_LANG
        )
        from app.main import resolve_lang, _localise_many, ok as app_ok
        
        # Resolve language preference
        resolved_lang = resolve_lang(lang, accept_language)
        
        # Execute query based on URI
        if uri == "me://profile/greeting":
            # Fetch identity entities
            from db.models import list_entities
            identity_rows = list_entities(conn, flavor="identity", limit=10)
            if not identity_rows:
                raise HTTPException(404, "Identity entities not found")
            
            # Organize by category
            identity_data = {}
            for row in identity_rows:
                category = row.get("category")
                if category:
                    identity_data[category] = row
            
            # Get basic info
            basic_entity = identity_data.get("basic", {})
            basic_raw = basic_entity.get("raw_data", {})
            basic_lang = basic_raw.get(resolved_lang, basic_raw.get(DEFAULT_LANG, {}))
            
            # Get links info
            links_entity = identity_data.get("links", {})
            links_raw = links_entity.get("raw_data", {})
            links_lang = links_raw.get(resolved_lang, links_raw.get(DEFAULT_LANG, {}))
            
            # Get contact info
            contact_entity = identity_data.get("contact", {})
            contact_raw = contact_entity.get("raw_data", {})
            contact_lang = contact_raw.get(resolved_lang, contact_raw.get(DEFAULT_LANG, {}))
            
            result_data = {
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
            }
        
        elif uri == "me://profile/stages":
            stages = query_stages(conn, category=None, tag=None, skill=None, technology=None)
            stages = _localise_many(conn, stages, resolved_lang)
            result_data = {"stages": stages, "count": len(stages)}
        
        elif uri == "me://profile/technology_stack":
            technologies = query_technologies_with_metrics(conn)
            result_data = {"technologies": technologies, "count": len(technologies)}
        
        elif uri == "me://profile/skills":
            skills = query_skills_with_metrics(conn)
            result_data = {"skills": skills, "count": len(skills)}
        
        elif uri == "me://profile/oeuvre":
            oeuvre = query_oeuvre(conn, category=None, tag=None, skill=None, technology=None)
            oeuvre = _localise_many(conn, oeuvre, resolved_lang)
            result_data = {"oeuvre": oeuvre, "count": len(oeuvre)}
        
        elif uri == "me://profile/categories":
            rows = conn.execute("""
                SELECT flavor, category, COUNT(*) as count FROM entities
                WHERE visibility='public'
                GROUP BY flavor, category ORDER BY flavor, count DESC
            """).fetchall()
            flavors_data = {"flavors": {}, "total": 0}
            for row in rows:
                flavor = row["flavor"]
                category = row["category"] or "uncategorized"
                count = row["count"]
                if flavor not in flavors_data["flavors"]:
                    flavors_data["flavors"][flavor] = {"total": 0, "categories": {}}
                flavors_data["flavors"][flavor]["categories"][category] = count
                flavors_data["flavors"][flavor]["total"] += count
                flavors_data["total"] += count
            result_data = flavors_data
        
        elif uri == "me://profile/tags":
            tags = list_all_tags(conn)
            result_data = {"tags": tags, "count": len(tags)}
        
        elif uri == "me://profile/languages":
            # Get translation coverage (matching languages endpoint logic)
            # Note: Original query uses 'type' but entities table has 'flavor' column
            total = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE visibility='public'"
            ).fetchone()[0]
            
            coverage = []
            for lang_code in sorted(SUPPORTED_LANGS):
                n_translated = conn.execute(
                    "SELECT COUNT(DISTINCT entity_id) FROM entity_translations WHERE lang=?",
                    (lang_code,),
                ).fetchone()[0]
                greeting_done = bool(
                    conn.execute(
                        "SELECT 1 FROM greeting_translations WHERE lang=?", (lang_code,)
                    ).fetchone()
                )
                coverage.append({
                    "lang": lang_code,
                    "is_default": lang_code == DEFAULT_LANG,
                    "entities_total": total,
                    "entities_translated": n_translated,
                    "coverage_pct": round(n_translated / total * 100, 1) if total else 0,
                    "greeting_translated": greeting_done,
                })
            
            result_data = {"languages": coverage}
        
        else:
            raise HTTPException(404, f"Unknown resource URI '{uri}'")
        
        # Wrap result in MCP content envelope
        data_json = json.dumps(result_data, ensure_ascii=False, indent=2)
        
        return ok(
            {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": resource["mimeType"],
                        "text": data_json,
                    }
                ]
            },
            meta={"access_stage": token_info.stage},
        )
        
    except ImportError as e:
        raise HTTPException(500, f"Failed to import dependencies: {str(e)}")
    except Exception as e:
        raise HTTPException(500, f"Resource resolution failed: {str(e)}")
