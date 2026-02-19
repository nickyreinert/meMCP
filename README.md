# Personal MCP Server

A FastAPI-based Model Context Protocol (MCP) server that aggregates and serves your professional profile, projects, publications, and career data.

## Features

- Multi-language support (EN, DE)
- Aggregates data from GitHub, Medium, RSS feeds, and LinkedIn
- LLM-powered content enrichment
- Entity graph with relationships
- RESTful API with advanced search and filtering

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Edit `config.yaml` to set:
- Your identity information
- Admin token (for secure endpoints)
- LLM backend (Groq or Ollama)
- Data sources (GitHub, Medium, blogs, etc.)

### 3. Ingest Data

```bash
# Full ingestion (LinkedIn PDF + all sources with LLM enrichment)
python ingest.py

# Force refresh (ignore cache)
python ingest.py --force

# Fast mode: fetch without LLM enrichment (skips PDF sources, use linkedin_export.yaml instead)
python ingest.py --disable-llm

# LLM-only mode: enrich existing entities (run after --disable-llm)
python ingest.py --llm-only

# Process specific source only
python ingest.py --source github
python ingest.py --source medium

# Dry run (fetch but don't save to DB)
python ingest.py --dry-run
```

### 4. Run Server

```bash
# Start the FastAPI server with uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Or with auto-reload for development
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Server will be available at: **http://localhost:8000**

## Important Commands

### Data Management

```bash
# Full data ingestion (LinkedIn + all sources)
python ingest.py
```

### LLM Backend Setup

**Option 1: Groq (Recommended - Fast & Free Tier)**

```bash
pip install groq
export GROQ_API_KEY=gsk_...
# Update config.yaml: llm.backend = groq
```

**Option 2: Ollama (Local & Private)**

```bash
brew install ollama
ollama serve
ollama pull mistral-small:24b-instruct-2501-q4_K_M
# Update config.yaml: llm.backend = ollama
```

### Development

```bash
# Run with auto-reload
uvicorn app.main:app --reload

# Check health
curl http://localhost:8000/health

# View API documentation
open http://localhost:8000/docs
```

## API Endpoints

- `GET /` - API index
- `GET /greeting` - Identity card
- `GET /entities` - All entities (paginated)
- `GET /categories` - Entity types + counts
- `GET /technology_stack` - Technologies used
- `GET /stages` - Career timeline
- `GET /work` - Projects & publications
- `GET /search?q=...` - Full-text search
- `GET /languages` - Translation coverage
- `POST /admin/rebuild` - Rebuild data (auth required)
- `POST /admin/translate` - Run translations (auth required)

Full API docs: http://localhost:8000/docs

## Configuration

Key settings in `config.yaml`:

```yaml
server:
  host: 0.0.0.0
  port: 8000
  admin_token: "change-me-please"

llm:
  backend: ollama  # or groq
  model: mistral-small:24b-instruct-2501-q4_K_M

oeuvre_sources:
  github:
    enabled: true
    connector: github_api
    url: https://api.github.com/users/YOUR_USERNAME/repos

  medium:
    enabled: true
    connector: medium_raw  # or rss
    url: file://data/Medium.html  # for medium_raw, or https://medium.com/feed/@username for rss
```

## Available Connectors

| Connector | Purpose | Config Example |
|-----------|---------|----------------|
| `github_api` | GitHub repos via API | `url: https://api.github.com/users/USERNAME/repos` |
| `rss` | RSS/Atom feeds | `url: https://example.com/feed.xml` |
| `medium_raw` | Raw HTML dump of Medium profile (bypasses Cloudflare) | `url: file://data/Medium.html` |
| `sitemap` | Scrape URLs from sitemap.xml | `url: https://example.com/sitemap.xml` |
| `manual` | Manual JSON data entry | `url: file://path/to/data.json` |

**Note on Medium:** The `medium_raw` connector extracts **all articles** from a saved HTML copy of your Medium profile page, while `rss` only gets the ~10 most recent posts from the RSS feed.

## Data Storage

- `db/profile.db` - SQLite database with all entities
- `.cache/` - Cached API responses and scraped content
- `stages_template.json` - Generated template for manual stage editing

## License

Personal project - configure and use as needed.
