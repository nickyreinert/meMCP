"""
app/routers/mcp.py — MCP Tools Router
======================================

Purpose:
  MCP (Model Context Protocol) endpoints for programmatic database queries.
  Provides read-only tools that enable LLMs to query the database without SQL.

Endpoints:
  GET /mcp/tools        → List available tool definitions with schemas
  POST /mcp/tools/call  → Execute a tool with arguments

Tool execution flow:
  1. Client sends tool name + arguments
  2. Router validates tool exists
  3. execute_tool() maps to database query functions
  4. Results returned as structured JSON

Security:
  - All operations are READ-ONLY
  - Uses dependency injection for database connections
  - Input validation on all arguments

Dependencies:
  - app.mcp_tools: Tool registry and execution logic
  - db.models: Database query functions via dependency injection
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.mcp_tools import get_tool_definitions, execute_tool
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
    conn=Depends(db)
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
