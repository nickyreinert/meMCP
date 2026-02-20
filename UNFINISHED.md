# UNFINISHED TASKS

## 2026-02-20 15:55

### YAML Sync System - Testing Required

**Status**: Implementation complete, testing pending

**What's Done**:
- ✅ Created `scrapers/yaml_sync.py` module with all sync utilities
- ✅ Updated `medium_raw.py` to use YAML sync for loading/saving
- ✅ Updated `seeder.py` to update YAML after DB insertion and LLM enrichment
- ✅ Updated `ingest.py` to pass yaml_path to enrichment functions
- ✅ Added `yaml_cache_path` property to `BaseScraper`
- ✅ Committed changes to git

**What's Unfinished**:
- ❌ End-to-end testing of YAML sync workflow
- ❌ Verify entity_ids are added to YAML after DB insertion
- ❌ Verify LLM enrichment fields are added to YAML
- ❌ Test manual YAML editing detection (mtime vs last_synced)
- ❌ Update linkedin_pdf scraper to use YAML sync (same pattern as medium_raw)
- ❌ Documentation update in README.md or YAML_WORKFLOW.md

**Current Blockers**:
- `ingest.py --source medium --force` command appears to hang during HTML parsing
- Need to verify Medium.html.yaml gets _metadata header and entity_ids

**Next Steps**:
1. Debug why ingest.py hangs during Medium HTML parsing
2. Run `python ingest.py --source medium --force` with smaller limit
3. Verify YAML file has _metadata header with last_synced timestamp
4. Verify entities in YAML have entity_id field after seeding
5. Test `python ingest.py --llm-only --source medium` to verify LLM enrichment updates YAML
6. Manually edit Medium.html.yaml (change a description), verify mtime > last_synced triggers reload
7. Apply same YAML sync pattern to linkedin_pdf scraper

**Manual Testing Commands**:
```bash
# Test initial scrape and YAML creation (limit to 3 items for quick test)
python ingest.py --source medium --force --limit 1

# Check YAML structure
head -50 data/Medium.html.yaml

# Test LLM enrichment with YAML sync (limit to 2 items)
python ingest.py --llm-only --source medium --batch-size 2

# Manually edit YAML, then run again (should detect manual edit)
# Edit data/Medium.html.yaml (change a description)
# Touch file to update mtime
touch data/Medium.html.yaml
python ingest.py --source medium --limit 1
```

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
