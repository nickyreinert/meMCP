---
name: meMCP
description: >
  Connect to and interact with a meMCP server — a personal MCP profile server
  (github.com/nickyreinert/meMCP) that serves professional profile data including career
  stages, projects, articles, skills, and technologies. Use this skill whenever the user
  wants to query, explore, create, or manage data on their meMCP server. Triggers include:
  "talk to my meMCP", "query my profile server", "create an interview", "add a project",
  "search my skills", "what's on my meMCP", "use my MCP server", "meMCP", or any mention
  of interacting with a personal profile/portfolio API server with token auth.
---

# meMCP Skill

Interact with a running meMCP server — a FastAPI-based personal profile MCP server.

## Setup: Required Configuration

Before doing anything, ask the user for these if not already provided in the conversation:

1. **Server URL** — e.g. `http://localhost:8000` or `https://mymcp.example.com`
2. **Token** — for protected endpoints (admin or elevated access token from `config.tech.yaml`)

Store these mentally for the session. Never hardcode them in output.

---

## Access Tiers

meMCP has three access tiers:

| Tier | Token Required | Examples |
|------|----------------|---------|
| Public | No | `/`, `/greeting`, `/entities`, `/search`, `/categories`, `/technology_stack`, `/stages`, `/work`, `/languages`, `/mcp/tools`, `/mcp/resources`, `/prompts` |
| Private | Yes (any valid token) | `/entities` with private data |
| Elevated | Admin token | Creating/updating entities, admin operations |

For read-only queries, try without a token first. If you get a 401/403, prompt for the token.

---

## Key Endpoints Reference

### Health & Discovery
```
GET /health                    → Server health check
GET /                          → API index
GET /mcp/tools                 → List MCP tools
GET /mcp/resources             → List browsable resources
GET /prompts                   → Example prompts for LLM agents
```

### Profile & Identity
```
GET /greeting                  → Identity card (name, bio, contact)
GET /categories                → Entity types with counts
GET /languages                 → Translation coverage
```

### Data Retrieval
```
GET /entities                  → All entities (paginated)
  ?page=1&limit=20
  ?flavor=stages|oeuvre|identity
  ?category=job|education|coding|article|...
  ?source=github|medium|...

GET /stages                    → Career timeline (jobs, education)
GET /work                      → Projects & publications (oeuvre)
GET /technology_stack          → Technologies used
GET /search?q=TERM             → Full-text search across all entities
```

### Metrics
```
GET /metrics/skills            → Skill scores (proficiency, growth, relevance, ...)
GET /metrics/technologies      → Technology scores
GET /metrics/tags              → Tag distribution
```

### Making Requests

Always include the token in the `Authorization` header for protected endpoints:
```
Authorization: Bearer <token>
```

---

## Common Workflows

### 1. Explore the Profile (no token needed)
```
1. GET /health              → confirm server is up
2. GET /greeting            → get name + bio
3. GET /categories          → understand what data exists
4. GET /technology_stack    → see tech skills
```

### 2. Search for Something Specific
```
GET /search?q=<term>
```
Then optionally filter:
```
GET /entities?flavor=oeuvre&category=coding
```

### 3. Job Interview Prep
```
1. GET /prompts             → find the interview prompt
2. GET /stages?category=job → get job history
3. GET /technology_stack    → get tech context
4. Synthesize a mock interview using the data
```
Or use the built-in LLM endpoint if available:
```
POST /llm/interview
Authorization: Bearer <token>
{"topic": "backend engineering", "style": "technical"}
```

### 4. Project Brainstorming
```
GET /work                   → get existing projects
GET /metrics/technologies   → see strongest tech areas
POST /llm/brainstorm        → trigger brainstorming endpoint (if enabled)
```

### 5. Create / Update Entities (requires elevated token)
```
POST /entities
Authorization: Bearer <admin_token>
Content-Type: application/json

{
  "flavor": "oeuvre",
  "category": "coding",
  "title": "My New Project",
  "description": "...",
  "technologies": ["Python", "FastAPI"],
  "skills": ["Backend Development"],
  "tags": ["Open Source"],
  "url": "https://github.com/...",
  "date": "2025-01-01"
}
```

For stages (career entries):
```json
{
  "flavor": "stages",
  "category": "job",
  "title": "Senior Engineer at Acme",
  "description": "...",
  "company": "Acme Corp",
  "start_date": "2022-01-01",
  "end_date": "2024-12-31",
  "technologies": ["Python", "Kubernetes"],
  "skills": ["System Design", "Leadership"]
}
```

---

## How to Make HTTP Calls

Since Claude can't make outbound HTTP calls directly, generate ready-to-run `curl` commands for the user, OR if the user has the server running locally and can execute bash, use the bash tool.

### curl template (read):
```bash
curl -s "http://SERVER_URL/ENDPOINT" \
  -H "Authorization: Bearer TOKEN" | python3 -m json.tool
```

### curl template (create):
```bash
curl -s -X POST "http://SERVER_URL/entities" \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ ... }' | python3 -m json.tool
```

If network is available, use the bash_tool to execute these directly.

---

## Presenting Results

- **Entities list**: Summarize count, then show a table of title / category / date
- **Technology stack**: Show as a ranked list with metrics if available
- **Stages timeline**: Show chronologically with company + role + duration
- **Search results**: Show matched entities with snippet of description
- **Metrics**: Show top 10 by `relevance` score unless user specifies otherwise

---

## Error Handling

| HTTP Code | Meaning | Action |
|-----------|---------|--------|
| 401 | Unauthorized | Ask user for token |
| 403 | Forbidden | Token lacks permission (need admin token) |
| 404 | Not found | Endpoint may not exist in their server version |
| 422 | Validation error | Fix the JSON payload |
| 500 | Server error | Ask user to check server logs |

If server is unreachable: confirm URL is correct and server is running (`uvicorn app.main:app --host 0.0.0.0 --port 8000`).

---

## LLM Interaction Endpoints

meMCP may expose LLM-powered endpoints (depends on server config). Check with:
```
GET /mcp/tools
```

Common tools may include:
- `interview` — Mock job interview using profile data
- `brainstorm` — Project/idea generation based on skills
- `summarize` — Profile narrative generation

These are called via `POST /mcp/call` or dedicated routes — check `/mcp/tools` for exact signatures.

---

## Tips

- Always start with `GET /health` to verify connectivity
- Use `GET /prompts` to discover what the server owner has configured as intended use cases
- Use `GET /mcp/resources` to discover all browsable data endpoints
- Token scope: the `admin_token` in `config.tech.yaml` is the elevated token; other tokens may be more limited
- Pagination: use `?page=N&limit=M` on `/entities` for large datasets
