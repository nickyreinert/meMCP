"""
db/models.py — Simplified Entity Data Model
============================================

Design principles:
  1. Three entity flavors: personal, stages, oeuvre
  2. No separate entity types - all data in one table
  3. Categories for classification within flavors
  4. Technologies, skills, and tags as classifications (not entities)
  5. SQLite backing store - portable, zero infra

Entity flavors:
  personal  → Static information (name, bio, greetings, contact details)
  stages    → Career stages (education, jobs) with timeframes
  oeuvre    → Your work (repos, articles, books, talks)

Entity categories (flavor-specific):
  stages:
    - education
    - job

  oeuvre:
    - coding
    - blog_post
    - article
    - book
    - website

Tag types (for technologies, skills, general tags):
  technology  → Python, JavaScript, React, Docker, Git, etc.
  skill       → Data Analysis, Project Management, Public Speaking, etc.
  generic     → AI, Web Development, Open Source, etc.
"""

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent / "profile.db"


# --- SCHEMA DDL ---

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Core entity table ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,       -- uuid4
    flavor          TEXT NOT NULL,          -- personal | stages | oeuvre | identity
    category        TEXT,                   -- For stages: education|job; For oeuvre: coding|blog_post|article|book|website; For identity: basic|links|contact
    title           TEXT NOT NULL,
    description     TEXT,                   -- LLM-enriched or manual
    url             TEXT,
    canonical_url   TEXT,                   -- Canonical URL for cross-posted content (from <link rel="canonical">)
    source          TEXT,                   -- github|medium|blog|linkedin|manual|identity (config slug)
    source_url      TEXT,                   -- raw URL this was scraped from
    start_date      TEXT,                   -- ISO-8601 date or partial (2020-01) - for stages
    end_date        TEXT,                   -- null = ongoing - for stages
    date            TEXT,                   -- Publication/creation date - for oeuvre
    is_current      INTEGER DEFAULT 0,      -- 1 if ongoing (for stages)
    language        TEXT DEFAULT 'en',      -- content language
    visibility      TEXT DEFAULT 'public',  -- public | private
    raw_data        TEXT,                   -- JSON data storage for identity and other structured data
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    llm_enriched    INTEGER DEFAULT 0,      -- 1 if LLM-enriched
    llm_enriched_at TEXT,                   -- ISO-8601 timestamp
    llm_model       TEXT                    -- e.g. "mistral-small:24b-instruct-2501-q4_K_M"
);

CREATE INDEX IF NOT EXISTS idx_entities_flavor ON entities(flavor);
CREATE INDEX IF NOT EXISTS idx_entities_category ON entities(category);
CREATE INDEX IF NOT EXISTS idx_entities_source ON entities(source);
CREATE INDEX IF NOT EXISTS idx_entities_dates ON entities(start_date, end_date);

-- Natural key indexes for duplicate prevention
CREATE INDEX IF NOT EXISTS idx_entities_source_url ON entities(source, url) WHERE url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical_url) WHERE canonical_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_entities_source_title ON entities(source, flavor, title);
CREATE INDEX IF NOT EXISTS idx_entities_identity ON entities(flavor, category) WHERE flavor='identity';

-- ── Tags (many-to-many) ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tags (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    tag_type  TEXT NOT NULL DEFAULT 'generic',  -- technology | skill | generic
    UNIQUE(entity_id, tag, tag_type)
);
CREATE INDEX IF NOT EXISTS idx_tags_type ON tags(tag_type);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_tags_entity ON tags(entity_id);

-- ── Translations (i18n overlay — only title + description) ────────────────────
CREATE TABLE IF NOT EXISTS entity_translations (
    entity_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    lang          TEXT NOT NULL,           -- ISO 639-1: en | de | fr
    title         TEXT,                    -- translated title (null = use base)
    description   TEXT,                    -- translated description
    translated_at TEXT NOT NULL,
    model         TEXT,                    -- e.g. groq/llama3-8b-8192
    PRIMARY KEY (entity_id, lang)
);
CREATE INDEX IF NOT EXISTS idx_trans_entity ON entity_translations(entity_id);
CREATE INDEX IF NOT EXISTS idx_trans_lang   ON entity_translations(lang);

-- ── Greeting translations (static identity bio text) ─────────────────────
CREATE TABLE IF NOT EXISTS greeting_translations (
    lang          TEXT PRIMARY KEY,
    tagline       TEXT,
    short         TEXT,
    greeting      TEXT,
    translated_at TEXT NOT NULL,
    model         TEXT
);

-- ── Tag metrics (calculated statistics for skills, technologies, tags) ───────
CREATE TABLE IF NOT EXISTS tag_metrics (
    tag_name          TEXT NOT NULL,
    tag_type          TEXT NOT NULL,           -- technology | skill | generic
    proficiency       REAL,                    -- 0-100: recency-weighted experience score
    experience_years  REAL,                    -- total years of experience
    entity_count      INTEGER DEFAULT 0,       -- number of entities with this tag
    frequency         REAL,                    -- occurrence rate (0-1)
    last_used         TEXT,                    -- ISO-8601 date of most recent entity
    diversity_score   REAL,                    -- variety of contexts (0-1)
    growth_trend      TEXT,                    -- increasing | stable | decreasing
    distribution      TEXT,                    -- JSON: breakdown by flavor/category
    relevance_score   REAL,                    -- composite score (0-100)
    calculated_at     TEXT NOT NULL,           -- ISO-8601 timestamp
    metrics_version   TEXT DEFAULT '1.0',      -- formula version for tracking changes
    PRIMARY KEY (tag_name, tag_type)
);
CREATE INDEX IF NOT EXISTS idx_metrics_type ON tag_metrics(tag_type);
CREATE INDEX IF NOT EXISTS idx_metrics_relevance ON tag_metrics(relevance_score DESC);

-- ── Cache table (for scraped sources) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS scrape_cache (
    url         TEXT PRIMARY KEY,
    content     TEXT,
    scraped_at  TEXT NOT NULL,
    etag        TEXT,
    status_code INTEGER
);

-- ── Access tokens ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tokens (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    token_value             TEXT NOT NULL UNIQUE,
    owner_name              TEXT NOT NULL,
    expires_at              TEXT NOT NULL,       -- ISO-8601 datetime (UTC)
    is_active               INTEGER DEFAULT 1,   -- 1=active, 0=revoked
    created_at              TEXT NOT NULL,
    -- Tier and per-token intelligence budget overrides (NULL = use global defaults)
    tier                    TEXT DEFAULT 'private',  -- private | elevated
    max_tokens_per_session  INTEGER,             -- override: max LLM output tokens per session
    max_calls_per_day       INTEGER,             -- override: max intelligence calls per day
    max_input_chars         INTEGER,             -- override: max input chars before truncation
    max_output_chars        INTEGER              -- override: max output chars after LLM response
);
CREATE INDEX IF NOT EXISTS idx_tokens_value ON tokens(token_value);
-- NOTE: idx_tokens_tier is created in _migrate_columns (column may not exist yet on older DBs)

-- ── Usage logs (all-stage request tracking) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS usage_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id        INTEGER NOT NULL REFERENCES tokens(id),
    endpoint_called TEXT NOT NULL,
    timestamp       TEXT NOT NULL,  -- ISO-8601 datetime (UTC)
    input_args      TEXT,           -- JSON-encoded query params / body args
    tier            TEXT,           -- private | elevated (caller's tier at time of call)
    api_provider    TEXT,           -- groq | perplexity (NULL for standard calls)
    input_length    INTEGER,        -- character count of the raw input sent
    input_text      TEXT,           -- actual input text (truncated to max_input_chars for storage)
    tokens_used     INTEGER         -- LLM output tokens consumed (from API response)
);
CREATE INDEX IF NOT EXISTS idx_usage_token        ON usage_logs(token_id);
CREATE INDEX IF NOT EXISTS idx_usage_timestamp    ON usage_logs(timestamp);
-- Indexes on new columns are created in _migrate_columns (safe for existing DBs)
"""


# --- DB CONNECTION ---

def get_db(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(path: Path = DB_PATH):
    """Initialize database schema and run any pending column migrations."""
    conn = get_db(path)
    conn.executescript(SCHEMA)
    _migrate_columns(conn)
    conn.commit()
    conn.close()


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """
    Safely add new columns to existing tables without destroying data.
    Each ALTER TABLE is wrapped in a try/except so it is a no-op when the
    column already exists (SQLite raises OperationalError in that case).
    """
    migrations = [
        # tokens — tier + per-token budget overrides
        "ALTER TABLE tokens ADD COLUMN tier TEXT DEFAULT 'private'",
        "ALTER TABLE tokens ADD COLUMN max_tokens_per_session INTEGER",
        "ALTER TABLE tokens ADD COLUMN max_calls_per_day INTEGER",
        "ALTER TABLE tokens ADD COLUMN max_input_chars INTEGER",
        "ALTER TABLE tokens ADD COLUMN max_output_chars INTEGER",
        # usage_logs — intelligence hub extension columns
        "ALTER TABLE usage_logs ADD COLUMN tier TEXT",
        "ALTER TABLE usage_logs ADD COLUMN api_provider TEXT",
        "ALTER TABLE usage_logs ADD COLUMN input_length INTEGER",
        "ALTER TABLE usage_logs ADD COLUMN input_text TEXT",
        "ALTER TABLE usage_logs ADD COLUMN tokens_used INTEGER",
        # Indexes on new columns — must run AFTER columns exist
        "CREATE INDEX IF NOT EXISTS idx_tokens_tier        ON tokens(tier)",
        "CREATE INDEX IF NOT EXISTS idx_usage_provider     ON usage_logs(api_provider)",
        "CREATE INDEX IF NOT EXISTS idx_usage_date_token   ON usage_logs(token_id, timestamp)",
    ]
    for stmt in migrations:
        try:
            conn.execute(stmt)
        except Exception:
            pass  # Column/index already exists — skip silently


# --- ENTITY CRUD ---

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def upsert_entity(conn: sqlite3.Connection, data: dict) -> str:
    """
    Insert or update an entity. Returns the entity id.
    `data` must have: flavor, title. Everything else is optional.
    
    Natural Key Strategy (prevents duplicates):
      - If entity has URL: lookup by (source, url)
      - If canonical_url set: check if any entity has this as url or canonical_url
      - If no URL: lookup by (source, flavor, title)
      - If identity: lookup by (flavor, category)
      - If id provided: use that id directly
    
    Required fields:
      - flavor: personal|stages|oeuvre|identity
      - title: entity title
    
    Optional fields:
      - category: For stages (education|job), for oeuvre (coding|blog_post|article|book|website), for identity (basic|links|contact)
      - description: LLM-enriched or manual
      - url: entity URL
      - canonical_url: canonical URL for cross-posted content
      - source: config slug (github|medium|linkedin|manual|identity)
      - source_url: raw scrape URL
      - start_date, end_date: for stages
      - date: for oeuvre (publication/creation date)
      - raw_data: dict of structured data (stored as JSON string)
      - tags: list of generic tags
      - technologies: list of technology tags
      - skills: list of skill tags
      - llm_enriched, llm_enriched_at, llm_model: LLM tracking
    """
    # Natural key lookup to find existing entity (prevents duplicates)
    existing = None
    
    if data.get("id"):
        # If ID provided explicitly, use it
        eid = data["id"]
        existing = conn.execute("SELECT id, created_at FROM entities WHERE id=?", (eid,)).fetchone()
    else:
        # Lookup by natural key
        source = data.get("source")
        url = data.get("url")
        canonical_url = data.get("canonical_url")
        flavor = data["flavor"]
        title = data["title"]
        category = data.get("category")
        
        # 1. Check canonical URL matching (cross-posted content)
        if canonical_url:
            # Check if canonical matches any existing url or canonical_url
            existing = conn.execute(
                """SELECT id, created_at FROM entities 
                   WHERE (url=? OR canonical_url=?) AND url IS NOT NULL
                   LIMIT 1""",
                (canonical_url, canonical_url)
            ).fetchone()
        
        # 2. Check if this URL is someone else's canonical
        if not existing and url:
            existing = conn.execute(
                "SELECT id, created_at FROM entities WHERE canonical_url=? LIMIT 1",
                (url,)
            ).fetchone()
        
        # 3. Check by (source, url)
        if not existing and url and source:
            existing = conn.execute(
                "SELECT id, created_at FROM entities WHERE source=? AND url=? AND url IS NOT NULL",
                (source, url)
            ).fetchone()
        
        # 4. Identity entities: (flavor, category) is unique
        if not existing and flavor == "identity" and category:
            existing = conn.execute(
                "SELECT id, created_at FROM entities WHERE flavor=? AND category=?",
                (flavor, category)
            ).fetchone()
        
        # 5. Fallback: (source, flavor, title)
        if not existing and source and title:
            existing = conn.execute(
                "SELECT id, created_at FROM entities WHERE source=? AND flavor=? AND title=?",
                (source, flavor, title)
            ).fetchone()
        
        # Use existing ID or generate new one
        eid = existing["id"] if existing else new_id()
    
    ts = now_iso()

    # Serialize raw_data if it's a dict
    raw_data_value = data.get("raw_data")
    if raw_data_value and isinstance(raw_data_value, dict):
        raw_data_value = json.dumps(raw_data_value)
    
    base = {
        "id":              eid,
        "flavor":          data["flavor"],
        "category":        data.get("category"),
        "title":           data["title"],
        "description":     data.get("description"),
        "url":             data.get("url"),
        "canonical_url":   data.get("canonical_url"),
        "source":          data.get("source"),
        "source_url":      data.get("source_url"),
        "start_date":      data.get("start_date"),
        "end_date":        data.get("end_date"),
        "date":            data.get("date"),
        "is_current":      1 if data.get("is_current") else 0,
        "language":        data.get("language", "en"),
        "visibility":      data.get("visibility", "public"),
        "raw_data":        raw_data_value,
        "created_at":      existing["created_at"] if existing else ts,
        "updated_at":      ts,
        "llm_enriched":    1 if data.get("llm_enriched") else 0,
        "llm_enriched_at": data.get("llm_enriched_at"),
        "llm_model":       data.get("llm_model"),
    }

    if existing:
        conn.execute("""
            UPDATE entities SET flavor=:flavor, category=:category, title=:title,
            description=:description, url=:url, canonical_url=:canonical_url, source=:source,
            source_url=:source_url, start_date=:start_date, end_date=:end_date,
            date=:date, is_current=:is_current, language=:language,
            visibility=:visibility, raw_data=:raw_data, updated_at=:updated_at,
            llm_enriched=:llm_enriched, llm_enriched_at=:llm_enriched_at,
            llm_model=:llm_model WHERE id=:id
        """, base)
    else:
        conn.execute("""
            INSERT INTO entities (id,flavor,category,title,description,url,canonical_url,source,
            source_url,start_date,end_date,date,is_current,language,visibility,
            raw_data,created_at,updated_at,llm_enriched,llm_enriched_at,llm_model)
            VALUES (:id,:flavor,:category,:title,:description,:url,:canonical_url,:source,
            :source_url,:start_date,:end_date,:date,:is_current,:language,:visibility,
            :raw_data,:created_at,:updated_at,:llm_enriched,:llm_enriched_at,:llm_model)
        """, base)

    # Tags: replace all
    conn.execute("DELETE FROM tags WHERE entity_id=?", (eid,))
    
    # Generic tags
    for tag in data.get("tags", []):
        if tag and tag.strip():
            conn.execute(
                "INSERT OR IGNORE INTO tags(entity_id,tag,tag_type) VALUES(?,?,'generic')",
                (eid, tag.strip())
            )
    
    # Technology tags
    for tag in data.get("technologies", []):
        if tag and tag.strip():
            conn.execute(
                "INSERT OR IGNORE INTO tags(entity_id,tag,tag_type) VALUES(?,?,'technology')",
                (eid, tag.strip())
            )
    
    # Skill tags
    for tag in data.get("skills", []):
        if tag and tag.strip():
            conn.execute(
                "INSERT OR IGNORE INTO tags(entity_id,tag,tag_type) VALUES(?,?,'skill')",
                (eid, tag.strip())
            )

    return eid


# --- QUERY HELPERS ---

def get_entity(conn: sqlite3.Connection, eid: str) -> Optional[dict]:
    """Get single entity by ID with tags."""
    row = conn.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
    if not row:
        return None
    return _hydrate(conn, dict(row))


def list_entities(conn: sqlite3.Connection,
                  flavor: str = None,
                  category: str = None,
                  source: str = None,
                  search: str = None,
                  tags: list[str] = None,
                  limit: int = 50,
                  offset: int = 0) -> list[dict]:
    """List entities with filters."""
    where = ["e.visibility='public'"]
    params: list[Any] = []

    if flavor:
        where.append("e.flavor=?")
        params.append(flavor)

    if category:
        where.append("e.category=?")
        params.append(category)

    if source:
        where.append("e.source=?")
        params.append(source)

    tag_join = ""
    if search:
        # Search in title, description, AND tags
        tag_join = " LEFT JOIN tags ts ON ts.entity_id=e.id"
        where.append("(e.title LIKE ? OR e.description LIKE ? OR ts.tag LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    if tags:
        for i, tag in enumerate(tags):
            alias = f"t{i}"
            tag_join += f" JOIN tags {alias} ON {alias}.entity_id=e.id AND {alias}.tag LIKE ?"
            params.append(f"%{tag}%")

    sql = f"""
        SELECT DISTINCT e.* FROM entities e
        {tag_join}
        WHERE {' AND '.join(where)}
        ORDER BY e.start_date DESC NULLS LAST, e.date DESC NULLS LAST, e.updated_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [_hydrate(conn, dict(r)) for r in rows]


def _hydrate(conn: sqlite3.Connection, row: dict) -> dict:
    """Attach tags to entity row and parse raw_data JSON."""
    eid = row.get("id")
    if not eid:
        return row

    # Get tags by type
    tags_raw = conn.execute(
        "SELECT tag, tag_type FROM tags WHERE entity_id=? ORDER BY tag_type, tag", (eid,)
    ).fetchall()

    row["tags"] = [t["tag"] for t in tags_raw if t["tag_type"] == "generic"]
    row["technologies"] = [t["tag"] for t in tags_raw if t["tag_type"] == "technology"]
    row["skills"] = [t["tag"] for t in tags_raw if t["tag_type"] == "skill"]
    
    # Parse raw_data from JSON string to dict
    if row.get("raw_data"):
        try:
            row["raw_data"] = json.loads(row["raw_data"])
        except (json.JSONDecodeError, TypeError):
            row["raw_data"] = {}

    return row


# --- TRANSLATION CRUD ---

SUPPORTED_LANGS = {"en", "de"}
DEFAULT_LANG = "en"


def upsert_translation(conn: sqlite3.Connection, entity_id: str, lang: str,
                       title: str = None, description: str = None,
                       model: str = None):
    """Store or overwrite a translation for one entity."""
    conn.execute("""
        INSERT INTO entity_translations
            (entity_id, lang, title, description, translated_at, model)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_id, lang) DO UPDATE SET
            title         = excluded.title,
            description   = excluded.description,
            translated_at = excluded.translated_at,
            model         = excluded.model
    """, (entity_id, lang, title, description, now_iso(), model))


def get_translation(conn: sqlite3.Connection,
                    entity_id: str, lang: str) -> Optional[dict]:
    """Get translation for entity+lang."""
    row = conn.execute("""
        SELECT * FROM entity_translations WHERE entity_id=? AND lang=?
    """, (entity_id, lang)).fetchone()
    return dict(row) if row else None


def needs_translation(conn: sqlite3.Connection,
                      entity_id: str, lang: str) -> bool:
    """Return True if no translation exists yet for this entity+lang."""
    row = conn.execute("""
        SELECT 1 FROM entity_translations WHERE entity_id=? AND lang=?
    """, (entity_id, lang)).fetchone()
    return row is None


def apply_translation(entity: dict, translation: Optional[dict]) -> dict:
    """
    Overlay translation on top of an entity dict (in-place + return).
    Only title and description are replaced — everything else stays as-is.
    """
    if not translation:
        return entity
    if translation.get("title"):
        entity["title_orig"] = entity.get("title")
        entity["title"] = translation["title"]
    if translation.get("description"):
        entity["description_orig"] = entity.get("description")
        entity["description"] = translation["description"]
    entity["_lang"] = translation.get("lang")
    return entity


def upsert_greeting_translation(conn: sqlite3.Connection, lang: str,
                                 tagline: str, short: str, greeting: str,
                                 model: str = None):
    """Store greeting translation."""
    conn.execute("""
        INSERT INTO greeting_translations
            (lang, tagline, short, greeting, translated_at, model)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(lang) DO UPDATE SET
            tagline       = excluded.tagline,
            short         = excluded.short,
            greeting      = excluded.greeting,
            translated_at = excluded.translated_at,
            model         = excluded.model
    """, (lang, tagline, short, greeting, now_iso(), model))


def get_greeting_translation(conn: sqlite3.Connection,
                              lang: str) -> Optional[dict]:
    """Get greeting translation for lang."""
    row = conn.execute("""
        SELECT * FROM greeting_translations WHERE lang=?
    """, (lang,)).fetchone()
    return dict(row) if row else None


def list_entities_needing_translation(conn: sqlite3.Connection,
                                      lang: str,
                                      limit: int = 200) -> list[dict]:
    """Return entities that have no translation for the given lang yet."""
    rows = conn.execute("""
        SELECT e.* FROM entities e
        WHERE e.visibility = 'public'
          AND e.flavor != 'personal'
          AND NOT EXISTS (
              SELECT 1 FROM entity_translations t
              WHERE t.entity_id = e.id AND t.lang = ?
          )
        ORDER BY e.updated_at DESC
        LIMIT ?
    """, (lang, limit)).fetchall()
    return [dict(r) for r in rows]


# --- DOMAIN QUERIES ---

def query_stages(conn: sqlite3.Connection,
                 category: Optional[str] = None,
                 tag: Optional[str] = None,
                 skill: Optional[str] = None,
                 technology: Optional[str] = None) -> list[dict]:
    """
    Return all stages (education, jobs) ordered chronologically.
    category filter: education|job
    tag/skill/technology: filter by generic tag, skill, or technology
    """
    sql = "SELECT e.* FROM entities e"
    params = []
    
    # Add tag join if any tag filter is present
    if tag or skill or technology:
        sql += " JOIN tags t ON t.entity_id = e.id"
    
    sql += " WHERE e.flavor = 'stages' AND e.visibility = 'public'"
    
    if category:
        sql += " AND e.category = ?"
        params.append(category)
    
    if tag:
        sql += " AND t.tag = ? AND t.tag_type = 'generic'"
        params.append(tag)
    elif skill:
        sql += " AND t.tag = ? AND t.tag_type = 'skill'"
        params.append(skill)
    elif technology:
        sql += " AND t.tag = ? AND t.tag_type = 'technology'"
        params.append(technology)
    
    sql += " ORDER BY e.start_date DESC NULLS LAST, e.end_date DESC NULLS LAST"

    rows = conn.execute(sql, params).fetchall()
    return [_hydrate(conn, dict(r)) for r in rows]


def query_oeuvre(conn: sqlite3.Connection,
                 category: Optional[str] = None,
                 tag: Optional[str] = None,
                 skill: Optional[str] = None,
                 technology: Optional[str] = None) -> list[dict]:
    """
    Return all oeuvre (work items).
    category filter: coding|blog_post|article|book|website
    tag/skill/technology: filter by generic tag, skill, or technology
    """
    sql = "SELECT e.* FROM entities e"
    params = []
    
    # Add tag join if any tag filter is present
    if tag or skill or technology:
        sql += " JOIN tags t ON t.entity_id = e.id"
    
    sql += " WHERE e.flavor = 'oeuvre' AND e.visibility = 'public'"
    
    if category:
        sql += " AND e.category = ?"
        params.append(category)
    
    if tag:
        sql += " AND t.tag = ? AND t.tag_type = 'generic'"
        params.append(tag)
    elif skill:
        sql += " AND t.tag = ? AND t.tag_type = 'skill'"
        params.append(skill)
    elif technology:
        sql += " AND t.tag = ? AND t.tag_type = 'technology'"
        params.append(technology)
    
    sql += " ORDER BY e.date DESC NULLS LAST, e.updated_at DESC"

    rows = conn.execute(sql, params).fetchall()
    return [_hydrate(conn, dict(r)) for r in rows]


def query_technologies(conn: sqlite3.Connection, category: str = None) -> list[dict]:
    """
    Return all distinct technology tags with entity counts.
    Optionally filter by entity category.
    """
    sql = """
        SELECT t.tag AS name,
               COUNT(DISTINCT t.entity_id) AS entity_count
        FROM tags t
        JOIN entities e ON e.id = t.entity_id
        WHERE t.tag_type = 'technology' AND e.visibility = 'public'
    """
    params = []
    if category:
        sql += " AND e.category = ?"
        params.append(category)
    sql += """
        GROUP BY t.tag
        ORDER BY entity_count DESC, t.tag
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_technology_detail(conn: sqlite3.Connection, name: str) -> dict:
    """All entities that use a specific technology."""
    rows = conn.execute("""
        SELECT DISTINCT e.* FROM entities e
        JOIN tags t ON t.entity_id = e.id
        WHERE t.tag_type = 'technology' AND t.tag = ? AND e.visibility = 'public'
        ORDER BY e.start_date DESC NULLS LAST, e.date DESC NULLS LAST, e.updated_at DESC
    """, (name,)).fetchall()
    entities = [_hydrate(conn, dict(r)) for r in rows]
    
    # Check if technology itself is an entity
    tech_entity_row = conn.execute("""
        SELECT e.* FROM entities e
        WHERE LOWER(e.title) = LOWER(?) AND e.visibility = 'public'
        LIMIT 1
    """, (name,)).fetchone()
    tech_entity = _hydrate(conn, dict(tech_entity_row)) if tech_entity_row else None
    
    grouped: dict[str, list] = {}
    for e in entities:
        grouped.setdefault(e["flavor"], []).append(e)
    
    return {
        "technology": name,
        "tech_entity": tech_entity,
        "entity_count": len(entities),
        "entities": entities,
        "by_flavor": grouped
    }


def query_skills(conn: sqlite3.Connection) -> list[dict]:
    """
    Return all distinct skill tags with entity counts.
    """
    rows = conn.execute("""
        SELECT t.tag AS name,
               COUNT(DISTINCT t.entity_id) AS entity_count
        FROM tags t
        JOIN entities e ON e.id = t.entity_id
        WHERE t.tag_type = 'skill' AND e.visibility = 'public'
        GROUP BY t.tag
        ORDER BY entity_count DESC, t.tag
    """).fetchall()
    return [dict(r) for r in rows]


def query_skill_detail(conn: sqlite3.Connection, skill: str) -> dict:
    """Return all entities that carry a given skill tag."""
    rows = conn.execute("""
        SELECT DISTINCT e.* FROM entities e
        JOIN tags t ON t.entity_id = e.id
        WHERE t.tag_type = 'skill' AND t.tag = ? AND e.visibility = 'public'
        ORDER BY e.start_date DESC NULLS LAST, e.date DESC NULLS LAST, e.updated_at DESC
    """, (skill,)).fetchall()
    entities = [_hydrate(conn, dict(r)) for r in rows]
    
    grouped: dict[str, list] = {}
    for e in entities:
        grouped.setdefault(e["flavor"], []).append(e)
    
    return {
        "skill": skill,
        "entity_count": len(entities),
        "entities": entities,
        "by_flavor": grouped
    }


def query_tag_detail(conn: sqlite3.Connection, tag_name: str) -> dict:
    """Return all entities that carry a given generic tag."""
    rows = conn.execute("""
        SELECT DISTINCT e.* FROM entities e
        JOIN tags t ON t.entity_id = e.id
        WHERE t.tag_type = 'generic' AND t.tag = ? AND e.visibility = 'public'
        ORDER BY e.start_date DESC NULLS LAST, e.date DESC NULLS LAST, e.updated_at DESC
    """, (tag_name,)).fetchall()
    entities = [_hydrate(conn, dict(r)) for r in rows]
    
    grouped: dict[str, list] = {}
    for e in entities:
        grouped.setdefault(e["flavor"], []).append(e)
    
    return {
        "tag": tag_name,
        "entity_count": len(entities),
        "entities": entities,
        "by_flavor": grouped
    }


def list_all_tags(conn: sqlite3.Connection,
                  tag_type: str = None) -> list[dict]:
    """Return all tags with usage counts, optionally filtered by type."""
    sql = """
        SELECT tag, tag_type, COUNT(*) as count FROM tags
    """
    params = []
    if tag_type:
        sql += " WHERE tag_type=?"
        params.append(tag_type)
    sql += " GROUP BY tag, tag_type ORDER BY count DESC"
    
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_graph(conn: sqlite3.Connection) -> dict:
    """
    Return graph visualization data: all entities and tags as nodes,
    and entity-tag relationships as links.
    """
    # Get all entities as nodes
    entity_rows = conn.execute("""
        SELECT id, title, flavor, category FROM entities
        WHERE visibility = 'public'
    """).fetchall()
    
    entity_nodes = [
        {
            "id": row["id"],
            "label": row["title"],
            "type": "entity",
            "flavor": row["flavor"],
            "category": row["category"]
        }
        for row in entity_rows
    ]
    
    # Get all unique tags as nodes
    tag_rows = conn.execute("""
        SELECT DISTINCT tag, tag_type FROM tags t
        JOIN entities e ON e.id = t.entity_id
        WHERE e.visibility = 'public'
    """).fetchall()
    
    tag_nodes = [
        {
            "id": f"tag:{row['tag']}",
            "label": row["tag"],
            "type": "tag",
            "tag_type": row["tag_type"]
        }
        for row in tag_rows
    ]
    
    # Get all entity-tag relationships as links
    link_rows = conn.execute("""
        SELECT t.entity_id, t.tag FROM tags t
        JOIN entities e ON e.id = t.entity_id
        WHERE e.visibility = 'public'
    """).fetchall()
    
    links = [
        {
            "source": row["entity_id"],
            "target": f"tag:{row['tag']}",
            "type": "tagged"
        }
        for row in link_rows
    ]
    
    return {
        "nodes": entity_nodes + tag_nodes,
        "links": links
    }


# --- TAG METRICS QUERIES ---

def get_tag_metrics(conn: sqlite3.Connection,
                   tag_name: str,
                   tag_type: str) -> Optional[dict]:
    """
    Retrieve calculated metrics for a specific tag.
    Returns None if metrics not found.
    """
    row = conn.execute("""
        SELECT * FROM tag_metrics
        WHERE tag_name = ? AND tag_type = ?
    """, (tag_name, tag_type)).fetchone()
    
    if not row:
        return None
    
    metrics = dict(row)
    
    # Parse JSON distribution field
    if metrics.get("distribution"):
        try:
            import json
            metrics["distribution"] = json.loads(metrics["distribution"])
        except (ValueError, TypeError):
            metrics["distribution"] = {}
    
    return metrics


def list_tag_metrics(conn: sqlite3.Connection,
                    tag_type: Optional[str] = None,
                    order_by: str = "relevance_score",
                    limit: int = 100) -> list[dict]:
    """
    List tag metrics with optional filtering and ordering.
    
    Args:
        conn: Database connection
        tag_type: Filter by tag_type (technology|skill|generic), or None for all
        order_by: Sort field (relevance_score|proficiency|entity_count|last_used)
        limit: Maximum results to return
    
    Returns:
        List of tag metrics dictionaries
    """
    valid_order_fields = {
        "relevance_score", "proficiency", "entity_count",
        "frequency", "last_used", "experience_years", "diversity_score"
    }
    
    if order_by not in valid_order_fields:
        order_by = "relevance_score"
    
    sql = "SELECT * FROM tag_metrics"
    params = []
    
    if tag_type:
        sql += " WHERE tag_type = ?"
        params.append(tag_type)
    
    sql += f" ORDER BY {order_by} DESC"
    
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    
    rows = conn.execute(sql, params).fetchall()
    
    metrics_list = []
    for row in rows:
        metrics = dict(row)
        
        # Parse JSON distribution field
        if metrics.get("distribution"):
            try:
                import json
                metrics["distribution"] = json.loads(metrics["distribution"])
            except (ValueError, TypeError):
                metrics["distribution"] = {}
        
        metrics_list.append(metrics)
    
    return metrics_list


def query_skills_with_metrics(conn: sqlite3.Connection,
                              order_by: str = "relevance_score",
                              limit: int = 100) -> list[dict]:
    """
    Query all skills with their metrics.
    Combines tag counts with calculated metrics.
    """
    sql = """
        SELECT 
            t.tag AS name,
            COUNT(DISTINCT t.entity_id) AS entity_count,
            m.proficiency,
            m.experience_years,
            m.frequency,
            m.last_used,
            m.diversity_score,
            m.growth_trend,
            m.distribution,
            m.relevance_score
        FROM tags t
        JOIN entities e ON e.id = t.entity_id
        LEFT JOIN tag_metrics m ON m.tag_name = t.tag AND m.tag_type = t.tag_type
        WHERE t.tag_type = 'skill' AND e.visibility = 'public'
        GROUP BY t.tag
    """
    
    valid_order_fields = {
        "relevance_score", "proficiency", "entity_count",
        "frequency", "last_used", "experience_years"
    }
    
    if order_by in valid_order_fields:
        sql += f" ORDER BY m.{order_by} DESC NULLS LAST, entity_count DESC"
    else:
        sql += " ORDER BY m.relevance_score DESC NULLS LAST, entity_count DESC"
    
    if limit:
        sql += f" LIMIT {limit}"
    
    rows = conn.execute(sql).fetchall()
    
    results = []
    for row in rows:
        result = dict(row)
        
        # Parse JSON distribution field
        if result.get("distribution"):
            try:
                import json
                result["distribution"] = json.loads(result["distribution"])
            except (ValueError, TypeError):
                result["distribution"] = {}
        
        results.append(result)
    
    return results


def query_technologies_with_metrics(conn: sqlite3.Connection,
                                   category: Optional[str] = None,
                                   order_by: str = "relevance_score",
                                   limit: int = 100) -> list[dict]:
    """
    Query all technologies with their metrics.
    Combines tag counts with calculated metrics.
    """
    sql = """
        SELECT 
            t.tag AS name,
            COUNT(DISTINCT t.entity_id) AS entity_count,
            m.proficiency,
            m.experience_years,
            m.frequency,
            m.last_used,
            m.diversity_score,
            m.growth_trend,
            m.distribution,
            m.relevance_score
        FROM tags t
        JOIN entities e ON e.id = t.entity_id
        LEFT JOIN tag_metrics m ON m.tag_name = t.tag AND m.tag_type = t.tag_type
        WHERE t.tag_type = 'technology' AND e.visibility = 'public'
    """
    params = []
    
    if category:
        sql += " AND e.category = ?"
        params.append(category)
    
    sql += " GROUP BY t.tag"
    
    valid_order_fields = {
        "relevance_score", "proficiency", "entity_count",
        "frequency", "last_used", "experience_years"
    }
    
    if order_by in valid_order_fields:
        sql += f" ORDER BY m.{order_by} DESC NULLS LAST, entity_count DESC"
    else:
        sql += " ORDER BY m.relevance_score DESC NULLS LAST, entity_count DESC"
    
    if limit:
        sql += f" LIMIT {limit}"
    
    rows = conn.execute(sql, params).fetchall()
    
    results = []
    for row in rows:
        result = dict(row)
        
        # Parse JSON distribution field
        if result.get("distribution"):
            try:
                import json
                result["distribution"] = json.loads(result["distribution"])
            except (ValueError, TypeError):
                result["distribution"] = {}
        
        results.append(result)
    
    return results

