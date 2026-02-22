"""
app/mcp_tools.py — MCP Tools Registry & Executor
=================================================

Purpose:
  Define and execute read-only tools for LLM agents via MCP protocol.
  Provides programmatic database query capabilities without exposing raw SQL.

Main functions:
  - get_tool_definitions(): Returns JSON Schema for all available tools
  - execute_tool(): Maps tool name + args to database queries
  - normalize_search_term(): Fuzzy matching for close variations

Tool catalog:
  - query_stages: Query career history (education, jobs)
  - query_portfolio: Query creative work (repos, articles, books)
  - get_technology_metrics: Get proficiency metrics for a technology

Dependencies:
  - db/models.py: Database query functions
"""

import sqlite3
from typing import Any, Optional
from db.models import (
    query_stages,
    query_oeuvre,
    query_technology_detail,
    get_tag_metrics,
)


# --- TOOL DEFINITIONS ---

TOOL_REGISTRY = [
    {
        "name": "query_stages",
        "description": "Query career stages (education and job history). Returns chronological list of education and work experience. Use category to filter by type (education/job), or search_term to filter by title, description, or technology tags.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["education", "job"],
                    "description": "Filter by stage type: 'education' for academic history, 'job' for work experience. Omit to get both."
                },
                "search_term": {
                    "type": "string",
                    "description": "Search term to filter results by title, description, or associated technology/skill tags. Case-insensitive partial matching."
                }
            },
            "required": []
        }
    },
    {
        "name": "query_portfolio",
        "description": "Query portfolio items (creative work and projects). Returns list of coding projects, articles, books, talks, and other published work. Use flavor to filter by type, or tag to find items with specific technologies/topics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "flavor": {
                    "type": "string",
                    "enum": ["coding", "blog_post", "article", "book", "website"],
                    "description": "Filter by work type: 'coding' for GitHub projects, 'blog_post' for blog entries, 'article' for published articles, 'book' for books, 'website' for websites. Omit to get all."
                },
                "tag": {
                    "type": "string",
                    "description": "Filter by technology/skill tag (e.g., 'Python', 'JavaScript', 'AI'). Case-insensitive, supports partial matching."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_technology_metrics",
        "description": "Get detailed proficiency metrics for a specific technology or skill. Returns experience years, proficiency score (0-100), usage frequency, recency, diversity score, and growth trend. Useful for understanding depth of expertise.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tech_name": {
                    "type": "string",
                    "description": "Name of the technology or skill (e.g., 'Python', 'JavaScript', 'Docker'). Case-insensitive, supports fuzzy matching for common variations (e.g., 'JS' -> 'JavaScript')."
                }
            },
            "required": ["tech_name"]
        }
    }
]


# --- FUZZY MATCHING ---

TECH_ALIASES = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "py": "Python",
    "python": "Python",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "react": "React",
    "reactjs": "React",
    "vue": "Vue.js",
    "vuejs": "Vue.js",
    "node": "Node.js",
    "nodejs": "Node.js",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "docker": "Docker",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
}


def normalize_search_term(term: str, conn: sqlite3.Connection) -> str:
    """
    Normalize search term to match existing tags in database.
    First checks aliases, then tries case-insensitive LIKE query.
    
    Args:
        term: User-provided search term
        conn: Database connection
    
    Returns:
        Normalized term or original if no match found
    """
    # Try alias lookup first
    normalized = TECH_ALIASES.get(term.lower())
    if normalized:
        return normalized
    
    # Try case-insensitive exact match in tags table
    row = conn.execute("""
        SELECT tag FROM tags
        WHERE LOWER(tag) = LOWER(?)
        GROUP BY tag
        ORDER BY COUNT(*) DESC
        LIMIT 1
    """, (term,)).fetchone()
    
    if row:
        return row["tag"]
    
    # Try partial match (LIKE %term%)
    row = conn.execute("""
        SELECT tag FROM tags
        WHERE LOWER(tag) LIKE LOWER(?)
        GROUP BY tag
        ORDER BY COUNT(*) DESC
        LIMIT 1
    """, (f"%{term}%",)).fetchone()
    
    if row:
        return row["tag"]
    
    # No match found, return original
    return term


# --- TOOL EXECUTION ---

def execute_tool(conn: sqlite3.Connection, tool_name: str, arguments: dict) -> dict:
    """
    Execute a tool by name with provided arguments.
    
    Args:
        conn: Database connection
        tool_name: Name of tool to execute
        arguments: Tool-specific arguments
    
    Returns:
        Result dictionary with data and metadata
    
    Raises:
        ValueError: If tool_name is unknown or arguments are invalid
    """
    
    # --- QUERY STAGES ---
    if tool_name == "query_stages":
        category: Optional[str] = arguments.get("category")
        search_term: Optional[str] = arguments.get("search_term")
        
        # Validate category if provided
        if category and category not in ["education", "job"]:
            raise ValueError(f"Invalid category '{category}'. Must be 'education' or 'job'.")
        
        # Execute base query
        if search_term:
            # Normalize technology/skill search term
            normalized_term = normalize_search_term(search_term, conn)
            # Try as technology first
            results = query_stages(conn, category=category or None, technology=normalized_term)
            # If no results, try as skill
            if not results:
                results = query_stages(conn, category=category or None, skill=normalized_term)
            # If still no results, try as generic tag
            if not results:
                results = query_stages(conn, category=category or None, tag=search_term)
        else:
            results = query_stages(conn, category=category or None)
        
        return {
            "status": "success",
            "data": {
                "stages": results,
                "count": len(results),
                "filters": {
                    "category": category,
                    "search_term": search_term
                }
            }
        }
    
    # --- QUERY PORTFOLIO ---
    elif tool_name == "query_portfolio":
        flavor: Optional[str] = arguments.get("flavor")
        tag: Optional[str] = arguments.get("tag")
        
        # Validate flavor if provided
        valid_flavors = ["coding", "blog_post", "article", "book", "website"]
        if flavor and flavor not in valid_flavors:
            raise ValueError(f"Invalid flavor '{flavor}'. Must be one of: {', '.join(valid_flavors)}.")
        
        # Execute base query
        if tag:
            # Normalize tag
            normalized_tag = normalize_search_term(tag, conn)
            # Try as technology first
            results = query_oeuvre(conn, category=flavor or None, technology=normalized_tag)
            # If no results, try as skill
            if not results:
                results = query_oeuvre(conn, category=flavor or None, skill=normalized_tag)
            # If still no results, try as generic tag
            if not results:
                results = query_oeuvre(conn, category=flavor or None, tag=tag)
        else:
            results = query_oeuvre(conn, category=flavor or None)
        
        return {
            "status": "success",
            "data": {
                "portfolio": results,
                "count": len(results),
                "filters": {
                    "flavor": flavor,
                    "tag": tag
                }
            }
        }
    
    # --- GET TECHNOLOGY METRICS ---
    elif tool_name == "get_technology_metrics":
        tech_name = arguments.get("tech_name")
        
        if not tech_name:
            raise ValueError("tech_name is required")
        
        # Normalize technology name
        normalized_name = normalize_search_term(tech_name, conn)
        
        # Get technology detail (includes all entities using it)
        tech_detail = query_technology_detail(conn, normalized_name)
        
        # Get metrics if available
        metrics = get_tag_metrics(conn, normalized_name, "technology")
        
        return {
            "status": "success",
            "data": {
                "technology": normalized_name,
                "original_query": tech_name if tech_name != normalized_name else None,
                "metrics": metrics,
                "entity_count": tech_detail.get("entity_count", 0),
                "by_flavor": tech_detail.get("by_flavor", {})
            }
        }
    
    # --- UNKNOWN TOOL ---
    else:
        raise ValueError(f"Unknown tool: {tool_name}")


def get_tool_definitions() -> list[dict]:
    """
    Return the list of available tool definitions.
    Each tool includes name, description, and JSON Schema for arguments.
    
    Returns:
        List of tool definition dictionaries
    """
    return TOOL_REGISTRY
