# UNFINISHED TASKS

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
