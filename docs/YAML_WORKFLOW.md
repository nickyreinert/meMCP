# YAML Manual Editing Workflow

## Concept

Consolidate data storage using **YAML files as manually editable caches** with database synchronization.

### Architecture

```
Manual Export (PDF/HTML) 
    ↓
DB + YAML export (with entity_id)
    ↓
Manual editing (YAML files)
    ↓
Selective update (--yaml-update)
    ↓
DB update with cascade (tags, skills, relations)
```

## Files

### Core Implementation
- `scrapers/yaml_exporter.py` — Export DB → YAML with entity_id
- `scrapers/yaml_connector.py` — Parse YAML → entity dicts
- `scrapers/seeder.py` — Added `update_entity()` + `update_from_yaml()` methods

### Data Files
- `data/linkedin_export.yaml` — LinkedIn/career data cache
- `data/medium_export.yaml` — Medium articles cache (template created)
- `data/<source>_export.yaml` — Generic pattern for any source

### Configuration
- `config.yaml` — Added `auto_export_yaml: true` option
- `ingest.py` — Added CLI args: `--yaml-update`, `--file`, `--id`, `--export-yaml`

## Workflow

### 1. Initial Scrape (PDF/HTML → DB + YAML)

```bash
# Scrape content, write to DB, export to YAML files
python ingest.py --export-yaml
```

**What happens:**
- Scrapes configured sources (LinkedIn PDF, Medium HTML, RSS, etc.)
- Writes entities to DB
- Exports to `data/<source>_export.yaml` with `entity_id` added to each item
- Creates manually editable cache files

### 2. Manual Editing

Edit YAML files:
```yaml
experience:
  - entity_id: "abc-123-def-456"     # DO NOT modify (DB primary key)
    role: "Senior Data Analyst"      # Edit freely
    company: "VML"
    start_date: "2024-06"
    end_date: null
    description: "Updated description..."   # Edit description
    tags: [Python, DataEngineering, GenAI]  # Add/remove tags
    skills: [DataAnalytics, LLM]            # Add/remove skills
```

**Editable fields:**
- `title`, `description`
- `tags`, `skills` (capability tags)
- `start_date`, `end_date`
- Extension fields (`location`, `remote`, etc.)

**DO NOT modify:**
- `entity_id` (it's the DB primary key)

### 3. Update DB from YAML

#### Update all entities in file:
```bash
python ingest.py --yaml-update --file data/linkedin_export.yaml
```

#### Update single entity by ID:
```bash
python ingest.py --yaml-update --file data/linkedin_export.yaml --id abc-123-def-456
```

#### Update with LLM enrichment:
```bash
# Skip LLM enrichment (faster)
python ingest.py --yaml-update --file data/linkedin_export.yaml --disable-llm

# With LLM enrichment (default)
python ingest.py --yaml-update --file data/linkedin_export.yaml
```

**What happens (CASCADE updates):**
1. Updates entity row (title, description, dates)
2. Updates `ext_*` table (extensions like location, remote)
3. **Deletes old tags → inserts new tags** (with tag_type classification)
4. **Deletes old relations → rebuilds relations** (company, institution, technologies)
5. Updates nested projects (for professional entities)

### 4. Export After Changes

Re-export current DB state:
```bash
python ingest.py --export-yaml
```

## Tag Classification (Cascade Logic)

When updating entities, tags are automatically classified:

### Technology Tags
```python
TECHNOLOGIES = {
    "Python": "language",
    "React": "framework",
    "AWS": "cloud",
    "PostgreSQL": "database",
    ...
}
```
→ Stored with `tag_type='technology'`
→ Creates `used_technology` relations to technology entities

### Capability Tags
```python
CAPABILITIES = {
    "DataAnalytics",
    "MachineLearning",
    "ProjectManagement",
    ...
}
```
→ Stored with `tag_type='capability'`
→ No relations created (broad skills, not specific tools)

### Generic Tags
Everything else → Stored with `tag_type='generic'`

## Cascade Update Details

When `update_entity()` runs, it:

1. **Entity Table Update**
   - title, description
   - start_date, end_date, is_current
   - url, sub_type
   - updated_at timestamp

2. **Extension Table Update** (`ext_professional`, `ext_education`, etc.)
   - role, employment_type, location
   - company_id (FK resolved from company name)
   - institution_id (FK resolved from institution name)

3. **Tags Table** (`tags`)
   - **DELETE** all existing tags for entity
   - **INSERT** new tags with proper classification:
     - `(entity_id, 'Python', 'technology')`
     - `(entity_id, 'DataAnalytics', 'capability')`
     - `(entity_id, 'Berlin', 'generic')`

4. **Relations Table** (`relations`)
   - **DELETE** all outgoing relations from entity
   - **REBUILD** relations:
     - `entity → company` (worked_at)
     - `entity → institution` (studied_at)
     - `entity → technology` (used_technology) for each tech tag
     - `project → professional` (part_of) for nested projects

## Platform-Specific Examples

### LinkedIn Export
```yaml
# data/linkedin_export.yaml
experience:
  - entity_id: "uuid-1"
    role: "Senior Data Analyst"
    company: "VML"
    start_date: "2024-06"
    end_date: null
    tags: [Python, SQL, GenAI]
    skills: [DataAnalytics, ProjectManagement]
    projects:
      - entity_id: "uuid-2"
        title: "GenAI Document Automation"
        description: "..."
        tags: [CopilotStudio, PowerAutomate]

education:
  - entity_id: "uuid-3"
    institution: "University of Example"
    degree: "MSc Data Science"
    ...
```

### Medium Export
```yaml
# data/medium_export.yaml
articles:
  - entity_id: "uuid-10"
    title: "Building a Text Extraction Pipeline"
    description: "..."
    url: "https://medium.com/@user/article"
    platform: "medium"
    published_at: "2024-01-15"
    tags: [Python, NLP, AI]
```

### GitHub Export
```yaml
# data/github_export.yaml
projects:
  - entity_id: "uuid-20"
    title: "awesome-repo"
    description: "..."
    url: "https://github.com/user/awesome-repo"
    repo_url: "https://github.com/user/awesome-repo"
    stars: 42
    language: "Python"
    tags: [Flask, Docker]
```

## Benefits

### Single Source of Truth
- One YAML file per platform
- DB is authoritative, YAML is editable cache
- No duplicate templates (removed redundant stages_template.json usage)

### Manual Adjustment Control
- Edit descriptions, tags, dates directly
- No need to re-scrape to fix typos
- Version control friendly (diff-able YAML)

### Platform Agnostic
- Same pattern for LinkedIn, Medium, GitHub, any source
- Generic `yaml_connector.py` works for all platforms
- Easy to add new sources

### Cascade Safety
- Automatic tag classification
- Relations rebuilt on update
- Extensions updated atomically
- No orphaned data

## Configuration

### Enable Auto-Export
```yaml
# config.yaml
auto_export_yaml: true  # Export after each ingestion
```

### Stages Source Configuration
```yaml
stages:
  source_type: linkedin_export  # Use YAML file
  source_path: data/linkedin_export.yaml
  export_template: false  # No need for JSON template anymore
```

### Oeuvre Sources
```yaml
oeuvre_sources:
  medium:
    enabled: true
    connector: medium_raw
    url: file://data/Medium.html
    # After scraping, exports to data/medium_export.yaml
  
  github:
    enabled: true
    connector: github_api
    url: https://api.github.com/users/username/repos
    # After scraping, exports to data/github_export.yaml
```

## CLI Reference

### Normal Ingestion
```bash
# Scrape all sources
python ingest.py

# Scrape with YAML export
python ingest.py --export-yaml

# Scrape specific source only
python ingest.py --source medium --export-yaml
```

### YAML Update Mode
```bash
# Update all entities from file
python ingest.py --yaml-update --file data/linkedin_export.yaml

# Update single entity
python ingest.py --yaml-update --file data/linkedin_export.yaml --id abc-123

# Update without LLM enrichment (faster)
python ingest.py --yaml-update --file data/medium_export.yaml --disable-llm
```

### LLM Enrichment
```bash
# Enrich all unenriched entities
python ingest.py --llm-only

# Enrich single entity
python ingest.py --llm-only --item abc-123

# Enrich specific source
python ingest.py --llm-only --source medium --batch-size 20
```

## Migration Path

### Old Workflow (Before)
1. LinkedIn PDF → stages_template.json (manual template)
2. LinkedIn YAML → separate file
3. Manual JSON → another separate file
4. Medium HTML → direct scrape, no cache

### New Workflow (After)
1. LinkedIn PDF → DB + `data/linkedin_export.yaml` (with IDs)
2. Medium HTML → DB + `data/medium_export.yaml` (with IDs)
3. Edit YAML → `--yaml-update` → DB cascade update
4. Single pattern for all platforms

### Deprecation
- `stages_template.json` → Now optional (export_template: false recommended)
- Direct PDF/HTML scraping → Still works, but exports to YAML for editability
- Multiple LinkedIn files → Consolidated to `linkedin_export.yaml`

## Testing

### Test Export
```bash
python ingest.py --source medium --export-yaml
# Check: data/medium_export.yaml created with entity_id fields
```

### Test Update
```bash
# 1. Edit data/medium_export.yaml (change a title or description)
# 2. Update DB
python ingest.py --yaml-update --file data/medium_export.yaml
# 3. Verify changes in DB or API endpoint
```

### Test Cascade
```bash
# 1. Edit YAML: add new tag to entity
# 2. Update entity
python ingest.py --yaml-update --file data/linkedin_export.yaml --id <entity_id>
# 3. Check DB: tags table has new tag with proper tag_type
# 4. Check DB: relations table has new used_technology relation if tech tag
```

## Troubleshooting

### Issue: entity_id not found
```
ERROR: Entity abc-123 not found in DB
```
**Solution:** Don't modify entity_id in YAML. If you need to create a new entity, remove entity_id field or run normal ingestion instead.

### Issue: Company/Institution not resolved
```
WARNING: Company 'Unknown Corp' not found in index
```
**Solution:** Ensure company/institution entities exist in DB first. Run full ingestion or create them manually.

### Issue: Tags not classified correctly
**Check:** Verify tag names match exact case in `TECHNOLOGIES` or `CAPABILITIES` dicts in `seeder.py`.

### Issue: Relations not created
**Debug:** Check logs for FK resolution warnings. Relations require valid company_id/institution_id/tech_id.

## Next Steps

1. Run initial ingestion with export:
   ```bash
   python ingest.py --export-yaml
   ```

2. Review generated YAML files in `data/` folder

3. Make manual edits as needed

4. Update DB:
   ```bash
   python ingest.py --yaml-update --file data/linkedin_export.yaml
   ```

5. Verify changes via API or database query

6. Commit YAML files to version control for tracking changes

## Implementation Summary

- ✓ YAML exporter (DB → YAML with entity_id)
- ✓ YAML connector (YAML → entity dicts)
- ✓ Cascade update logic (tags, skills, relations)
- ✓ CLI arguments (--yaml-update, --file, --id)
- ✓ Medium export template
- ✓ Config documentation
- ✓ Auto-export option
- ✓ Generic pattern for all platforms
