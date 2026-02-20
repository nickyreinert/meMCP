# meMCP

A FastAPI-based Model Context Protocol (MCP) server that aggregates and serves your professional profile, projects, publications, and career data.

## Features

- Multi-language support (EN, DE)
- Aggregates data from GitHub, Medium, RSS feeds, Sitemaps, LinkedIn, by plain HTML scraping or manual YAML files
- LLM-powered content extraction and enrichment (e.g., summarization, tag extraction, skill/technology classification)
- Entity graph with relationships
- RESTful API with advanced search and filtering

## Data Model

The MCP stores information as **entities**. There are **three** main types (`flavor` of entities):

- `personal` - Static information about you (name, bio, greetings, contact details, etc.)
- `stages` - Career stages (education, jobs, projects, etc.) with timeframes and descriptions
- `oeuvre` - Your work (code repos, articles, books, talks, etc.) with metadata and links

Entities carry additional meta data, like:
- `source` - refers to the configuration slug
- `source_url` - refers to where the script fetched the data from
- `title` - if the particular source provides a title
- `description` - an LLM enriched description of the entity, based on the original source content (e.g., repo description, article summary, etc.)
- `start_date` and `end_date` - for stages, if available (e.g., job duration, project timeline, etc.)
- `date` - for oeuvre, if available (e.g., publication date, repo creation date, etc.)
- `created_at` and `updated_at` - timestamps for when the entity was created and last updated in the database
- `llm_enriched` - boolean flag indicating whether the entity has been enriched with LLM-generated content (e.g., description)
- `llm_model` - the name of the LLM model used for enrichment, if applicable (e.g., "mistral-small:24b-instruct-2501-q4_K_M")

Each entity of `stages` can be classified into three `categories`:
- `education`
- `job`
- `other` (specified in the `description` field)

Entities of `oeuvre` can be classified into:
- `coding`
- `blog_post`
- `article`
- `book`
- `website`
- and more...

Besides that `stages` and `oeuvre` entities can be further classified to identify **technologies**, **skills** and **general tags**. 

`technologies` describe **what you worked with** in a specific way:
- Programming languages (Python, JavaScript, etc.)
- Frameworks (React, Django, etc.)
- Tools (Docker, Git, etc.)

`skills` described **what you did** in a more general way, like
- Data Analysis
- Project Management
- Public Speaking
- System Operations

And finally `tags` tries to capute general attributes that are not covered by the other two, like:
- Maze Runner
- Open Source

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Edit `config.yaml` to set:
- Your static identity information
- LLM backend (Remote AI provider or locally, like **Ollama**)
- Data sources (GitHub, Medium, blogs, etc.)

### 3. Ingest Data

```bash
# Runs a full ingestion with all provided sources and utilizes LLM for content extraction
python ingest.py

# Force refresh (ignore cache)
python ingest.py --force

# Fast mode: fetch without LLM enrichment (skips PDF sources, uses .yaml cache if available)
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

## Configuration

### Scraper Modules

#### LinkedIn

LinkedIn does not allow to parse your profile. You need to export your profile page as PDF. The scraper usess LLM to extract structured data from the PDF. 
It creates an YAML file in `data/linkedin_profile.pdf.yaml` that can be used for manual editing. If this file exists, the scraper will not run PDF extraction again.

```yaml
stages:
  enabled: true                      # Set false to skip stages processing
  source_type: linkedin_pdf          # Only linkedin_pdf supported (auto-caching to YAML)
  source_path: linkedin_profile.pdf  # LinkedIn export PDF file
```

#### Medium.com

Medium does not allow scraping your complete story list. To consider all your Medium articles, scroll to the end of your list of stories (https://medium.com/me/stories?tab=posts-published) and then open the DOM inspector. Copy the top node (<html>) and past it in a file (e.g., `data/Medium.html`). Then use the `medium_raw` connector to extract all articles from this file.

The scraper will:
1. Extract article URLs, titles, and dates from the saved HTML
2. Fetch each article's full content using a **headless browser** (bypasses Cloudflare protection)
3. Create a YAML cache at `data/Medium.html.yaml` for manual editing

**Prerequisites**: Install Playwright for headless browser support:
```bash
pip install playwright
playwright install chromium
```

The YAML cache will be used for future runs to avoid re-parsing the HTML file and re-fetching articles.

```yaml
  medium_raw: # can be named anything, but the `connector` must be `medium_raw`
    connector: medium_raw
    enabled: true
    url: file://data/Medium.html
    sub_type_override: article
    limit: 0  # 0 = all, you can optionally use the `rss` connector, but it will only consider the ~10 most recent articles from the RSS feed.

```yaml
  medium_rss: # Optional: for incremental updates only
    connector: rss
    enabled: false
    url: https://medium.com/feed/@nickyreinert
    sub_type_override: article
    limit: 0  # 0 = all available (RSS typically has ~10 most recent)
    cache_ttl_hours: 168  # 7 days
    llm-processing: true
```
    limit: 0  # 0 = all available (RSS typically has ~10 most recent)
    cache_ttl_hours: 168  # 7 days
    llm-processing: true
```

**Best practice**: Use `medium_raw` once to get your complete article history (metadata only), then switch to `rss` for ongoing updates with content.

### GitHub

Github offers an API to parse your repositories. The parser will extract metadata, like use languages, description and README content, which can be enriched with LLM to create a more detailed description of the project and extract `technologies`, `skills` and `tags`.

```yaml
  github: # can be named anything, but the `connector` must be `github_api`
    connector: github_api
    enabled: true
    url: https://api.github.com/users/nickyreinert/repos
    sub_type_override: coding   # Override default sub_type (coding/blog_post/article/book/website/podcast/video)
    limit: 0                    # 0 = all, otherwise integer limit on number of repos to fetch
    fetch_readmes: true         # true = richer descriptions, slower
    llm-processing: true        
```

### Generic RSS/Atom Feeds

You can also add any RSS/Atom feed as source. The parser will extract metadata and content, which can be enriched with LLM to create a more detailed description of the article and extract `technologies`, `skills` and `tags`.

```yaml
  my_blog: # can be named anything
    connector: rss
    enabled: true
    url: https://myblog.com/feed.xml
    sub_type_override: blog_post
    limit: 0  # 0 = all available
    cache_ttl_hours: 168  # 7 days
    llm-processing: true
```

### Website and Sitemap Scraper

You can add any website with a sitemap.xml as a source. A sitemap usually indicates multiple pages. By default each page is considered a single *entity*, which means it will be fetched, summarized and classified. If you want the entire site to be considered as a whole, you can set `single-entity: true`. 

**Cache File Workflow:**
- If `cache-file` is specified and exists: loads from cache, skips fetching
- If cache exists but LLM fields (tags, skills, technologies) are empty AND `llm-processing: true`: reprocesses with LLM
- If cache missing: fetches pages and saves to cache file
- Cache file allows manual editing of extracted data without losing changes on subsequent runs

**Mode 1: Multi-entity** (each page = separate entity)
```yaml
  my_blog:
    connector: sitemap
    enabled: true
    url: https://myblog.com/sitemap.xml
    sub_type_override: blog_post
    limit: 0                    # 0 = all, otherwise integer limit
    cache_ttl_hours: 168        # 7 days
    llm-processing: true
    single-entity: false        # Each page becomes a separate entity (default)
    cache-file: file://data/myblog_sitemap.yaml  # Optional: cache for manual editing
    
    connector-setup:
      post-title-selector: h1
      post-content-selector: article
      post-published-date-selector: 'time[datetime]'
      post-description-selector: 'meta[name="description"]'
```

**Mode 2: Single-entity** (whole site = one entity)
```yaml
  my_project_website:
    connector: sitemap
    enabled: true
    url: https://myproject.com/sitemap.xml
    sub_type_override: website
    llm-processing: true
    single-entity: true         # Treat entire site as one entity
    cache-file: file://data/myproject_sitemap.yaml  # Optional: cache for manual editing
    
    connector-setup:
      post-title-selector: h1
      post-content-selector: main
      post-description-selector: 'meta[name="description"]'
``` 
```

### Static Manual Data

You can provide a YAML file with manually curated entities. This is useful for static information that doesn't change often, or for data that cannot be easily scraped. The file should contain a list of entities with the same structure as the database entries. The data can be undertand as `stages` or `oeuvre` flavor. This connector checks the file date and compares it to the ingestion date to decide whether to reprocess the file with LLM or not, based on the `cache_ttl_hours` setting.

```yaml
  manual: # can be named anything
    connector: manual
    enabled: true
    url: file://data/manual_data.yaml
    llm-processing: true
    cache_ttl_hours: 168  # 7 days (if the file is updated within this timeframe, it will be reprocessed with LLM)
```

The required structure of the `manual_data.yaml` file is as follows:

```yaml
entities:
  job1:
    flavor: stages  
    category: job
    title: "Software Engineer at XYZ"
    description: "Worked on developing web applications using Python and React."
    company: "XYZ"
    start_date: "2020-01-01"
    end_date: "2022-12-31"
    skills: ["Python", "React", "Web Development"]
    technologies: ["Django", "Node.js", "AWS"]
    tags: ["Full-stack", "Remote"]
    
  project1:
    flavor: oeuvre
    category: coding
    title: "Personal Portfolio Website"
    description: "A personal website to showcase my projects and skills, built with Next.js and hosted on Vercel."
    url: "https://myportfolio.com"
    date: "2021-06-15"
    skills: ["Web Development", "UI/UX Design"]
    technologies: ["Next.js", "Vercel"]
    tags: ["Portfolio", "Open Source"]
    
```

## LLM Backend Setup

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
| `sitemap` | Scrape URLs from sitemap.xml (multi-entity or single-entity mode) | `url: https://example.com/sitemap.xml`<br/>`single-entity: false` |
| `manual` | Manual JSON data entry | `url: file://path/to/data.json` |

**Note on Medium:** The `medium_raw` connector extracts **all articles** from a saved HTML copy of your Medium profile page, while `rss` only gets the ~10 most recent posts from the RSS feed.

## Data Storage

- `db/profile.db` - SQLite database with all entities
- `.cache/` - Cached API responses and scraped content
- `data/linkedin_profile.pdf.yaml` - Auto-generated YAML cache for manual LinkedIn profile editing
- `data/<source>_export.yaml` - Exported entities by source for manual editing

## License

Personal project - configure and use as needed.
