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
  - query_skills: List all skills with metrics
  - search_entities: Full-text search across all entities

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
    query_skills_with_metrics,
    list_entities,
)


# --- TOOL DEFINITIONS ---

TOOL_REGISTRY = [
    {
        "name": "query_stages",
        "description": "Query career stages (education and job history). Returns chronological list of education and work experience. Use category to filter by type (education/job), or search_term to filter by title, description, or technology tags.",
        "inputSchema": {
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
        "inputSchema": {
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
        "inputSchema": {
            "type": "object",
            "properties": {
                "tech_name": {
                    "type": "string",
                    "description": "Name of the technology or skill (e.g., 'Python', 'JavaScript', 'Docker'). Case-insensitive, supports fuzzy matching for common variations (e.g., 'JS' -> 'JavaScript')."
                }
            },
            "required": ["tech_name"]
        }
    },
    {
        "name": "query_skills",
        "description": "Query all skills with proficiency metrics and entity counts. Optionally filter by minimum proficiency threshold. Returns list of skills sorted by relevance score, including proficiency, experience years, and usage statistics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_proficiency": {
                    "type": "number",
                    "description": "Minimum proficiency score (0-100). Only returns skills with proficiency >= this value. Omit to get all skills.",
                    "minimum": 0,
                    "maximum": 100
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Defaults to 50.",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50
                }
            },
            "required": []
        }
    },
    {
        "name": "search_entities",
        "description": "Full-text search across all entities (career stages, portfolio items, technologies). Searches in titles, descriptions, and tags. Returns ranked results with relevance scoring.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string. Searches across entity titles, descriptions, and associated tags. Case-insensitive."
                },
                "flavor": {
                    "type": "string",
                    "enum": ["stages", "oeuvre", "personal", "identity"],
                    "description": "Optional filter by entity flavor: 'stages' for career history, 'oeuvre' for portfolio items. Omit to search all."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Defaults to 20.",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20
                }
            },
            "required": ["query"]
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
    
    # --- QUERY SKILLS ---
    elif tool_name == "query_skills":
        min_proficiency = arguments.get("min_proficiency")
        limit = arguments.get("limit", 50)
        
        # Validate min_proficiency if provided
        if min_proficiency is not None:
            if not isinstance(min_proficiency, (int, float)) or min_proficiency < 0 or min_proficiency > 100:
                raise ValueError("min_proficiency must be a number between 0 and 100")
        
        # Validate limit
        if not isinstance(limit, int) or limit < 1 or limit > 200:
            raise ValueError("limit must be an integer between 1 and 200")
        
        # Query skills with metrics
        all_skills = query_skills_with_metrics(
            conn,
            order_by="relevance_score",
            limit=limit
        )
        
        # Filter by min_proficiency if provided
        if min_proficiency is not None:
            skills = [
                s for s in all_skills 
                if s.get("proficiency") and float(s["proficiency"]) >= min_proficiency
            ]
        else:
            skills = all_skills
        
        return {
            "status": "success",
            "data": {
                "skills": skills,
                "count": len(skills),
                "filters": {
                    "min_proficiency": min_proficiency,
                    "limit": limit
                }
            }
        }
    
    # --- SEARCH ENTITIES ---
    elif tool_name == "search_entities":
        query = arguments.get("query")
        flavor = arguments.get("flavor")
        limit = arguments.get("limit", 20)
        
        # Validate required fields
        if not query:
            raise ValueError("query is required")
        
        # Validate flavor if provided
        if flavor and flavor not in ["stages", "oeuvre", "personal", "identity"]:
            raise ValueError(f"Invalid flavor '{flavor}'. Must be one of: stages, oeuvre, personal, identity.")
        
        # Validate limit
        if not isinstance(limit, int) or limit < 1 or limit > 100:
            raise ValueError("limit must be an integer between 1 and 100")
        
        # Execute search query
        # Use SQL LIKE for basic full-text search
        search_pattern = f"%{query}%"
        sql = """
            SELECT DISTINCT e.* FROM entities e
            LEFT JOIN tags t ON t.entity_id = e.id
            WHERE e.visibility = 'public'
            AND (
                e.title LIKE ? 
                OR e.description LIKE ?
                OR t.tag LIKE ?
            )
        """
        params = [search_pattern, search_pattern, search_pattern]
        
        if flavor:
            sql += " AND e.flavor = ?"
            params.append(flavor)
        
        sql += f" ORDER BY e.updated_at DESC LIMIT {limit}"
        
        rows = conn.execute(sql, params).fetchall()
        
        # Hydrate results using _hydrate helper (import needed)
        from db.models import _hydrate
        results = [_hydrate(conn, dict(r)) for r in rows]
        
        return {
            "status": "success",
            "data": {
                "results": results,
                "count": len(results),
                "query": query,
                "filters": {
                    "flavor": flavor,
                    "limit": limit
                }
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
