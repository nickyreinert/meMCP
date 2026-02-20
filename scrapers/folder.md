# Medium Raw HTML Connector

## Overview

Added `medium_raw` connector to parse raw HTML dumps of Medium profile pages, bypassing Cloudflare protection and accessing complete article history.

**Philosophy**: Keep it lean. Prefer "raw" manual approaches (save HTML locally) over heavy dependencies. Designed to run efficiently in the background.

## Implementation

### Files Created
- `scrapers/medium_raw.py` - New scraper implementation

### Files Modified  
- `scrapers/base.py` - Registered `medium_raw` in ScraperFactory, removed Playwright
- `scrapers/sitemap.py` - Removed Playwright dependency
- `scrapers/manual.py` - Removed Playwright dependency
- `config.yaml` - Updated medium source to use new connector
- `requirements.txt` - Removed Playwright
- `README.md` - Documented available connectors, removed Playwright install

### Files Removed
- `scrapers/playwright_fetcher.py` - Deleted (heavy dependency, not essential)

## Scrapers Overview

### GitHub (`github_api`)
- Uses GitHub REST API v3
- **Full pagination support**: Fetches ALL repos (100 per page, follows Link headers)
- Features:
  - Optional README content fetching
  - Fork filtering (configurable)
  - Language detection
  - Stars and forks metadata
- Fast, no browser automation needed
- Respects rate limits via headers

### RSS (`rss`)
- Standard RSS/Atom feed parsing
- Works for most blogs
- Limited to recent posts (~10-30)

### Medium Raw (`medium_raw`)
- Parses saved HTML copy of profile page
- Extracts all articles (102+ vs 10 from RSS)
- Manual approach: Save page → Parse locally
- No browser automation needed

### Sitemap (`sitemap`)
- Parses sitemap.xml files
- Two modes:
  - **Multi-entity mode** (`single-entity: false`): Each page becomes separate entity
  - **Single-entity mode** (`single-entity: true`): Whole site as one entity (uses frontpage)
- Fetches pages with basic HTTP
- Configurable CSS selectors for content extraction
- Works for static sites and blogs
- **Cache file support** (`cache-file`):
  - If cache exists: loads from cache, skips fetching
  - If LLM fields empty + LLM enabled: flags for reprocessing
  - If cache missing: fetches and saves to cache
  - Allows manual editing without losing changes

### Manual (`manual`)
- Custom HTML scraping with CSS selectors
- Basic HTTP requests only
- For sites with simple HTML structure

### LinkedIn PDF (`linkedin_pdf`)
- Parses LinkedIn profile PDF export with LLM
- Smart YAML caching with sync detection
- Two modes:
  - **First run**: Parses PDF → creates `.yaml` cache
  - **Subsequent runs**: Loads from YAML cache (unless PDF modified or --force)
- Re-parse triggers:
  - PDF modification date newer than YAML last_synced
  - `--force` flag provided
- LLM enrichment detection:
  - Checks each entity for `llm_enriched`, `tags`, `skills`, `technologies`
  - If missing → flags for re-enrichment
  - If present → uses cached data
- Manual editing: Edit YAML to refine content (preserved across runs)
- Requires: `pypdf` for PDF text extraction, LLM for structured data extraction

**Note**: All scrapers use standard HTTP requests. If a site requires JavaScript rendering, users should save the fully-rendered HTML manually (like Medium approach) rather than adding browser automation dependencies.

## Features

- Parses complete DOM structure of saved Medium profile page
- Extracts article metadata: title, URL, publication date
- Returns **102 articles** vs **10 from RSS feed**
- Supports deduplication via `should_fetch()` caching
- Proper date parsing for various date formats

## Usage

### 1. Save Medium Profile HTML
1. Visit your Medium profile page (e.g., https://medium.com/@username)
2. Save complete page as HTML to `data/Medium.html`

### 2. Configure
```yaml
oeuvre_sources:
  medium:
    enabled: true
    connector: medium_raw
    url: file://data/Medium.html
    sub_type_override: article
    limit: 0  # 0 = all articles
```

### 3. Ingest
```bash
python ingest.py --source medium --disable-llm
```

## Technical Details

### HTML Structure Parsing
- Finds article links: `<a href="/@username/article-slug">`
- Extracts titles from `<h2>` tags within parent `<td>`
- Parses dates from `<p>Published <span>Date</span></p>` pattern
- Handles date format: `%b %d, %Y` (e.g., "Jan 31, 2024")

### Error Handling
- Validates file existence
- Graceful fallback for missing titles (uses URL slug)
- Logs unparseable dates as debug messages
- Skips duplicates via URL matching

## Results

✓ Extracts 102 Medium articles (vs 10 from RSS)
✓ Includes publication dates for all articles
✓ Bypasses Medium's Cloudflare protection
✓ Works offline with saved HTML
✓ **Zero heavy dependencies** (no Playwright/Selenium)

## Comparison: Dependencies Removed

| Approach | Before | After |
|----------|--------|-------|
| Medium scraping | Playwright (200MB+ binaries) | BeautifulSoup (HTML parsing) |
| Installation | `pip install && playwright install` | `pip install` |
| Complexity | Browser automation, async code | Simple HTTP + parsing |
| Background runs | Resource-intensive | Lightweight |

**Trade-off**: Manual HTML saving vs automatic scraping. Chosen manual for simplicity and reliability.
