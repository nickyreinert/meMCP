# UNFINISHED TASKS

## 2026-02-21 19:15 - Enhanced Session Tracking with Pagination & SQLite

**Status**: Complete

**What's Done**:
- ✅ Moved relevant endpoints to config.yaml (configurable, weighted)
- ✅ Created SQLite schema for persistent sessions (sessions, session_coverage, request_log)
- ✅ Implemented pagination-aware coverage tracking
  - Paginated endpoints: 50% for 1 page, 75% for 2 pages, 100% for 3+ pages
  - Non-paginated endpoints: 100% on first visit
  - Weighted coverage calculation
- ✅ Refactored SessionTracker to use SQLite backend
- ✅ Added GET /coverage endpoint with detailed report
  - Shows missing endpoints
  - Shows incomplete endpoints (paginated with partial coverage)
  - Full breakdown with pages visited per endpoint
- ✅ Enhanced middleware to track pagination (offset, page, skip params)
- ✅ Updated response headers with earned/total weight

**Architecture Changes**:
- Relevant endpoints now configured in config.yaml with weight and pagination flags
- SQLite persistence: db/sessions.db (3 tables)
- Pagination tracking via query params (offset, limit, page, skip)
- Coverage calculated as weighted percentage
- GET /coverage provides actionable feedback to LLM agents

**Testing**:
- ✅ Pagination tracking: 3 pages = 100%, 1 page = 50%
- ✅ Coverage endpoint shows missing + incomplete
- ✅ SQLite persistence verified (1 session, 5 coverage entries, 12 logs)
- ✅ Response headers with weighted metrics
- ✅ File logging with anonymized IP

**Files Modified**:
- `config.yaml` (added session.relevant_endpoints with weights)
- `app/session_tracker.py` (complete rewrite for SQLite + pagination)
- `app/main.py` (updated middleware, added /coverage endpoint)

**Config Example**:
```yaml
session:
  relevant_endpoints:
    /greeting: {weight: 1.0, paginated: false}
    /stages: {weight: 0.5, paginated: true}
    /skills: {weight: 0.5, paginated: true}
```

## 2026-02-21 18:50 - Session Tracking & Coverage Monitoring

**Status**: Complete

**What's Done**:
- ✅ Added session configuration to config.yaml (timeout_hours, log_file, track_coverage)
- ✅ Created app/session_tracker.py module for session management
- ✅ Implemented IP anonymization (last octet removed)
- ✅ Created unique visitor ID via hash(anonymized_ip + user_agent)
- ✅ Added middleware for automatic request tracking
- ✅ Coverage tracking for metric-relevant endpoints (10 total)
- ✅ Session reset mechanism when hitting root endpoint (/)
- ✅ Response headers with coverage metadata (X-Coverage-*, X-Session-*)
- ✅ File logging to logs/api_access.log
- ✅ Increased rate limits (30→120, 60→200, 20→60 per minute)
- ✅ Tested: session tracking, coverage calculation, reset mechanism

**Architecture**:
- Relevant endpoints (with metrics): /greeting, /stages, /stages/{id}, /oeuvre, /oeuvre/{id}, /skills, /skills/{name}, /technology, /technology/{name}, /tags/{tag_name}
- Coverage returned as percentage + X out of N endpoints visited
- Sessions expire after 5 hours (configurable)
- Middleware injects headers into every response
- Root endpoint (/) resets session and includes reset explanation

**Files Created**:
- `app/session_tracker.py` (new)
- `logs/api_access.log` (auto-created)

**Files Modified**:
- `config.yaml` (added session block)
- `app/main.py` (import tracker, initialize, add middleware, update root endpoint, increase rate limits)

**Testing**:
- ✅ Coverage increases correctly (10% → 20% → 30% → 40% → 50%)
- ✅ Session reset works (coverage returns to 0%)
- ✅ Headers present on all responses
- ✅ Detail endpoints (/oeuvre/{id}) normalize correctly
- ✅ Logging captures all requests with anonymized IP

## 2026-02-20 23:15 - Identity Data Refactoring

**Status**: Complete

**What's Done**:
- ✅ Created identity scraper (scrapers/identity.py) to load from identity.yaml
- ✅ Added raw_data TEXT column to entities table for JSON storage
- ✅ Updated db/models.py to handle raw_data serialization/deserialization
- ✅ Created migration script (scripts/migrate_add_raw_data.py)
- ✅ Removed personal flavor entity creation from seeder
- ✅ Updated /greeting endpoint to use identity flavor with categories
- ✅ Integrated identity scraper into ingest.py
- ✅ Cleaned up 17 duplicate personal flavor entries
- ✅ Tested multi-language support (en/de) with identity data

**Architecture Changes**:
- Identity data now stored as 3 separate entities with flavor="identity"
  - basic: name, tagline, description, location
  - links: github, medium, blog, linkedin, etc.
  - contact: reason, preferred, email, phone, telegram, other
- Each entity stores multi-lang data as JSON in raw_data field
- Frontend /greeting endpoint extracts requested language from raw_data
- No more duplicate personal flavor entities

**Files Modified**:
- `scrapers/identity.py` (new)
- `scrapers/base.py` (added identity connector)
- `db/models.py` (added raw_data column, updated upsert_entity and _hydrate)
- `scrapers/seeder.py` (removed _seed_owner, added raw_data passthrough)
- `app/main.py` (updated /greeting endpoint for identity flavor)
- `ingest.py` (added identity source processing)
- `scripts/migrate_add_raw_data.py` (new migration script)

**Next Steps**:
- ✅ Run: `python ingest.py --source identity --disable-llm` to populate identity entities
- ✅ Database migration executed successfully
- ✅ Multi-language support verified (en/de)

## 2026-02-20 - Sitemap Scraper YAML Sync Update

**Status**: Implementation complete, testing pending

**What's Done**:
- ✅ Updated `scrapers/sitemap.py` to use yaml_sync module
- ✅ Replaced manual YAML loading with `load_yaml_with_metadata()`
- ✅ Replaced manual YAML saving with `save_yaml_atomic()`
- ✅ Added `yaml_cache_path` property for ingest.py integration
- ✅ Improved LLM enrichment status tracking
- ✅ entity_id tracking for DB sync
- ✅ Updated README.md with comprehensive connector documentation
- ✅ Updated Available Connectors table with linkedin_pdf and cache-file info

**What's Unfinished**:
- ❌ End-to-end testing of sitemap scraper with cache-file workflow
- ❌ Verify LLM enrichment detection and re-enrichment for cached entities

**Files Modified**:
- `scrapers/sitemap.py` (updated to use yaml_sync module)
- `README.md` (updated connector table, LinkedIn section, Data Storage section)

**Architecture Notes**:
- Sitemap scraper now follows same YAML sync pattern as medium_raw and linkedin_pdf
- Cache file structure:
  ```yaml
  _metadata:
    last_synced: "2026-02-20T16:30:00Z"
    source: "my_blog"
  entities:
    - title: "Article Title"
      url: "https://..."
      entity_id: "abc123..."  # Added after DB insertion
      description: "..."
      technologies: [...]     # Added after LLM enrichment
      skills: [...]           # Added after LLM enrichment
      tags: [...]             # Added after LLM enrichment
      llm_enriched: true
      llm_model: "mistral-small:24b"
  ```

---

## 2026-02-20 - LinkedIn PDF Scraper YAML Sync Implementation

**Status**: Implementation complete, testing pending

**What's Done**:
- ✅ Created `scrapers/linkedin_pdf_scraper.py` with smart caching (following medium_raw pattern)
- ✅ Registered linkedin_pdf connector in ScraperFactory
- ✅ Updated config.yaml to use connector architecture for stages
- ✅ Updated ingest.py to treat stages as regular connector source
- ✅ Added LLM enricher support to BaseScraper and ScraperFactory
- ✅ Implemented PDF mtime vs YAML last_synced detection
- ✅ Implemented --force flag support for re-parsing
- ✅ LLM enrichment detection per entity (checks llm_enriched status)
- ✅ YAML cache with entity_id, technologies, skills, tags preservation

**What's Unfinished**:
- ❌ End-to-end testing of linkedin_pdf scraper workflow
- ❌ Verify PDF re-parsing when modification detected
- ❌ Verify LLM enrichment detection and re-enrichment

**Files Modified**:
- `scrapers/linkedin_pdf_scraper.py` (new file, 393 lines)
- `scrapers/base.py` (added llm parameter, registered linkedin_pdf)
- `config.yaml` (updated stages to use connector architecture)
- `ingest.py` (removed special stages handling, unified workflow)
- `scrapers/folder.md` (documented linkedin_pdf connector)

**Architecture Notes**:
- Stages now uses same connector pattern as oeuvre sources
- PDF parsing only happens when:
  - No YAML cache exists
  - PDF modified after last_synced
  - --force flag provided
- LLM enrichment status tracked per entity in YAML
- Manual YAML edits preserved across runs

**Next Steps**:
1. Test: `python ingest.py --source stages`
2. Test: Modify PDF, verify re-parsing
3. Test: Edit YAML manually, verify preservation
4. Test: LLM enrichment detection and update

---

## 2026-02-20 15:55

### YAML Sync System - Testing Required

**Status**: Implementation complete, testing complete

**What's Done**:
- ✅ Created `scrapers/yaml_sync.py` module with all sync utilities
- ✅ Updated `medium_raw.py` to use YAML sync for loading/saving
- ✅ Updated `seeder.py` to update YAML after DB insertion and LLM enrichment
- ✅ Updated `ingest.py` to pass yaml_path to enrichment functions
- ✅ Added `yaml_cache_path` property to `BaseScraper`
- ✅ Committed changes to git
- ✅ Verify entity_ids are added to YAML after DB insertion
- ✅ Verify LLM enrichment fields are added to YAML
- ✅ End-to-end testing of YAML sync workflow
- ✅ Test manual YAML editing detection (mtime vs last_synced)
- ✅ Update linkedin_pdf scraper to use YAML sync (same pattern as medium_raw)


**Files Modified**:
- `scrapers/yaml_sync.py` (new file, 350 lines)
- `scrapers/medium_raw.py` (updated imports, docstrings, load/save methods)
- `scrapers/seeder.py` (added yaml_path params, YAML update calls)
- `scrapers/base.py` (added yaml_cache_path property)
- `ingest.py` (updated to pass yaml_path for enrichment)

**Architecture Notes**:
- YAML file structure:
  ```yaml
  _metadata:
    last_synced: "2026-02-20T15:30:45Z"
    source: "medium"
  articles:
    - title: "Article Title"
      url: "https://..."
      entity_id: "abc123..."  # Added after DB insertion
      description: "..."
      technologies: [...]     # Added after LLM enrichment
      skills: [...]           # Added after LLM enrichment
      tags: [...]             # Added after LLM enrichment
      llm_enriched: true      # Added after LLM enrichment
      llm_model: "..."        # Added after LLM enrichment
  ```

- Sync detection logic:
  - Compare file mtime vs _metadata.last_synced
  - If mtime > last_synced: user edited manually, reload from YAML
  - If mtime <= last_synced: no changes, safe to use cache or update
  
- Atomic writes pattern:
  - Write to temp file (`.tmp` suffix)
  - Rename temp file to target (atomic operation)
  - Prevents corruption if process crashes mid-write
