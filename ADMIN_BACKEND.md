# Admin Backend Quick Reference

## Configuration (Environment Variables)

- set those variables in `.env`file

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_USERNAME` | `admin` | Admin login username |
| `ADMIN_PASSWORD` | *(required)* | Admin login password |
| `ADMIN_SECRET_KEY` | *(auto-generated)* | JWT signing key (set for persistence across restarts) |
| `ADMIN_TOKEN_EXPIRE_MINUTES` | `60` | JWT expiration time |
| `ADMIN_CORS_ORIGINS` | `*` | Comma-separated allowed origins |

## Endpoints

### Authentication
| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/admin/login` | POST | No | Authenticate and receive JWT |
| `/admin/` | GET | Yes | Health check / root info |

### Log Management
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/logs` | GET | List available log files |
| `/admin/logs/{filename}` | GET | Download log file content |

### Token Management (wired to `db/profile.db`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/tokens` | GET | List all tokens with usage counts |
| `/admin/tokens` | POST | Create new access token |
| `/admin/tokens/{id}` | DELETE | Soft-revoke a token |
| `/admin/tokens/{id}/budget` | PUT | Update per-token budget overrides |
| `/admin/tokens/{id}/stats` | GET | Usage statistics for a token |

### Database Browser
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/db` | GET | Browse entities (with filters) |
| `/admin/db/stats` | GET | Database summary statistics |
| `/admin/db/tags` | GET | List all tags |
| `/admin/db/metrics` | GET | Tag metrics (proficiency, relevance) |

### Source Management (reads/writes `config.content.yaml`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/sources` | GET | List configured sources |
| `/admin/sources` | POST | Add new oeuvre source |
| `/admin/sources/{id}` | PUT | Update source configuration |
| `/admin/sources/{id}` | DELETE | Remove oeuvre source |

### Scraping & Jobs
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/scrape` | POST | Trigger scraping job (launches `ingest.py`) |
| `/admin/jobs` | GET | List running/completed jobs |
| `/admin/jobs/{id}` | GET | Get job status and output |

### File Upload
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/upload` | POST | Upload PDF files to `data/` |

## Docker Setup

```bash
# Start both main server and admin backend
docker compose up -d

# Admin backend runs on port 8081
curl http://localhost:8081/admin/
```

The admin container shares volumes with `mcp-server`:
- `./db` — SQLite databases
- `./logs` — Log files
- `./data` — Uploaded files and scraped data
- `./config.content.yaml` — Source configuration (read-write)
- `./config.tech.yaml` — Technical configuration (read-only)

## Usage Example

```bash
# 1. Login
TOKEN=$(curl -s -X POST http://localhost:8081/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}' | jq -r '.access_token')

# 2. List tokens
curl -H "Authorization: Bearer $TOKEN" http://localhost:8081/admin/tokens

# 3. Create a new API token
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"owner":"Google-Recruiter","days":30,"tier":"mcp"}' \
  http://localhost:8081/admin/tokens

# 4. Browse database
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8081/admin/db?flavor=oeuvre&limit=10"

# 5. Trigger scraping
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source":"github","disable_llm":true}' \
  http://localhost:8081/admin/scrape
```

## Files

| File | Purpose |
|------|---------|
| `admin/main.py` | App factory, login endpoint, CORS |
| `admin/routers/admin.py` | All admin endpoints with real business logic |
| `admin/dependencies/access_control.py` | JWT auth with env-var config |
| `admin/Dockerfile` | Docker image for admin container |
| `docker-compose.yml` | Both services (mcp-server + admin) |
| `tests/test_admin_backend.py` | Test suite |
