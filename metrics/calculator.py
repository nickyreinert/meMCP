"""
metrics/calculator.py — Tag Metrics Calculation Engine
=======================================================

Purpose:
  Calculate comprehensive metrics for skills, technologies, and tags based
  on entity data. Metrics include proficiency, experience, frequency, diversity,
  growth trends, and composite relevance scores.

Main functions:
  - calculate_all_metrics(): Calculate metrics for all tags
  - calculate_tag_metrics(): Calculate metrics for a specific tag
  - update_metrics_in_db(): Store calculated metrics in database
  - get_tag_metrics(): Retrieve metrics for a tag

Dependencies:
  - db/models.py: Database access and entity queries
  - config.yaml: Metric formula configuration

Metric formulas (all customizable via config.yaml):
  - proficiency: Recency-weighted experience (0-100)
  - experience_years: Total time using skill/tech
  - entity_count: Number of entities with tag
  - frequency: Occurrence rate across all entities
  - last_used: Most recent entity date
  - diversity_score: Variety of contexts (0-1)
  - growth_trend: increasing | stable | decreasing
  - distribution: Breakdown by flavor and category
  - relevance_score: Composite weighted score (0-100)
"""

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml


# --- CONFIGURATION LOADING ---

def load_metrics_config() -> dict:
    """Load metrics configuration from config.yaml."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    return config.get("metrics", {})


CONFIG = load_metrics_config()


# --- DATE UTILITIES ---

def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """
    Parse ISO-8601 date string to datetime object.
    Handles partial dates (YYYY-MM, YYYY) by assuming first of month/year.
    """
    if not date_str:
        return None
    
    try:
        # Full date
        if len(date_str) == 10:  # YYYY-MM-DD
            return datetime.fromisoformat(date_str)
        # Year-month
        elif len(date_str) == 7:  # YYYY-MM
            return datetime.fromisoformat(f"{date_str}-01")
        # Year only
        elif len(date_str) == 4:  # YYYY
            return datetime.fromisoformat(f"{date_str}-01-01")
        else:
            return datetime.fromisoformat(date_str)
    except (ValueError, AttributeError):
        return None


def years_between(start: Optional[datetime], end: Optional[datetime]) -> float:
    """Calculate years between two dates. If end is None, uses current date."""
    if not start:
        return 0.0
    
    # Ensure timezone-naive datetime for comparison
    end_date = end or datetime.now()
    # Remove timezone info if present
    if start.tzinfo:
        start = start.replace(tzinfo=None)
    if end_date.tzinfo:
        end_date = end_date.replace(tzinfo=None)
    
    delta = end_date - start
    return delta.days / 365.25


def years_since(date: Optional[datetime]) -> float:
    """Calculate years since a date. Returns 0 if date is None."""
    if not date:
        return 999.0  # Very old
    # Ensure timezone-naive datetime
    now = datetime.now()
    if date.tzinfo:
        date = date.replace(tzinfo=None)
    if now.tzinfo:
        now = now.replace(tzinfo=None)
    return years_between(date, now)


# --- ENTITY DATA COLLECTION ---

def collect_tag_entities(conn: sqlite3.Connection,
                         tag_name: str,
                         tag_type: str) -> list[dict]:
    """
    Collect all entities that have the specified tag.
    Returns list of entity dicts with relevant fields.
    """
    rows = conn.execute("""
        SELECT DISTINCT e.id, e.flavor, e.category, e.title,
               e.start_date, e.end_date, e.date, e.is_current,
               e.created_at, e.updated_at
        FROM entities e
        JOIN tags t ON t.entity_id = e.id
        WHERE t.tag = ? AND t.tag_type = ? AND e.visibility = 'public'
        ORDER BY e.start_date DESC NULLS LAST, e.date DESC NULLS LAST
    """, (tag_name, tag_type)).fetchall()
    
    return [dict(row) for row in rows]


def get_total_entity_count(conn: sqlite3.Connection) -> int:
    """Get total count of public entities."""
    row = conn.execute("""
        SELECT COUNT(*) as count FROM entities WHERE visibility = 'public'
    """).fetchone()
    return row["count"] if row else 0


# --- METRIC CALCULATIONS ---

def calculate_proficiency(entities: list[dict], config: dict) -> float:
    """
    Calculate proficiency score (0-100) based on recency, duration, and context.
    
    Context-aware: Differentiates between "worked with it" vs "wrote about it"
    by applying context weights based on entity flavor and category.
    
    Formula:
      proficiency = weighted_avg(context_weighted_scores)
      entity_score = context_weight * (recency_score * rw + duration_score * dw)
      recency_score = 100 * exp(-years_since / decay_halflife)
      duration_score = min(100, years_of_use * 15)
    """
    if not entities:
        return 0.0
    
    cfg = config.get("proficiency", {})
    recency_weight = cfg.get("recency_weight", 0.6)
    duration_weight = cfg.get("duration_weight", 0.4)
    decay_halflife = cfg.get("recency_decay_halflife", 3.0)
    min_score = cfg.get("min_score", 5.0)
    default_duration = cfg.get("default_oeuvre_duration_years", 0.5)
    duration_multiplier = cfg.get("duration_score_multiplier", 15.0)
    
    # Get context weights (flavor → category → multiplier)
    context_weights = config.get("context_weights", {})
    
    scores = []
    
    for entity in entities:
        # Determine relevant date (most recent)
        relevant_date = None
        
        # For current positions/stages, use today's date
        if entity.get("is_current"):
            relevant_date = datetime.now()
        elif entity.get("end_date"):
            relevant_date = parse_date(entity["end_date"])
        elif entity.get("start_date"):
            relevant_date = parse_date(entity["start_date"])
        elif entity.get("date"):
            relevant_date = parse_date(entity["date"])
        elif entity.get("published_at"):
            relevant_date = parse_date(entity["published_at"])
        
        # Recency score (exponential decay)
        years_ago = years_since(relevant_date)
        recency_score = 100.0 * math.exp(-years_ago / decay_halflife)
        
        # Duration score
        if entity.get("start_date"):
            start = parse_date(entity["start_date"])
            end = parse_date(entity["end_date"]) if entity.get("end_date") else datetime.now()
            duration_years = years_between(start, end)
        else:
            # For oeuvre items without duration, use configured default
            duration_years = default_duration
        
        duration_score = min(100.0, duration_years * duration_multiplier)
        
        # Weighted combination (before context)
        base_score = (recency_weight * recency_score + duration_weight * duration_score)
        
        # Apply context weight based on flavor and category
        flavor = entity.get("flavor", "oeuvre")
        category = entity.get("category", "other")
        default_weight = context_weights.get("default_weight", 0.5)
        context_weight = context_weights.get(flavor, {}).get(category, default_weight)
        
        # Context-weighted score
        weighted_score = context_weight * base_score
        scores.append(max(min_score, weighted_score))
    
    # Simple average of context-weighted scores
    return sum(scores) / len(scores) if scores else 0.0


def calculate_experience_years(entities: list[dict], config: dict) -> float:
    """
    Calculate total years of experience with this skill/technology.
    Handles overlapping time periods if deduplicate_overlaps is enabled.
    """
    if not entities:
        return 0.0
    
    cfg = config.get("experience_years", {})
    deduplicate = cfg.get("deduplicate_overlaps", True)
    current_bonus = cfg.get("current_bonus_multiplier", 1.2)
    
    time_periods = []
    
    for entity in entities:
        if entity.get("start_date"):
            start = parse_date(entity["start_date"])
            end = parse_date(entity["end_date"]) if entity.get("end_date") else datetime.now()
            
            if start:
                duration = years_between(start, end)
                # Bonus for current/ongoing
                if entity.get("is_current") or not entity.get("end_date"):
                    duration *= current_bonus
                
                time_periods.append({
                    "start": start,
                    "end": end,
                    "duration": duration
                })
        elif entity.get("date"):
            # For oeuvre items, use configured default duration
            cfg = config.get("proficiency", {})
            default_duration = cfg.get("default_oeuvre_duration_years", 0.5)
            date_parsed = parse_date(entity["date"])
            if date_parsed:
                time_periods.append({
                    "start": date_parsed,
                    "end": date_parsed,
                    "duration": default_duration
                })
    
    # Filter out periods with None start/end dates
    time_periods = [p for p in time_periods if p.get("start") and p.get("end")]
    
    if not deduplicate:
        # Simple sum
        return sum(p["duration"] for p in time_periods)
    
    # Merge overlapping periods
    if not time_periods:
        return 0.0
    
    # Sort by start date (safe now since we filtered None values)
    time_periods.sort(key=lambda p: p["start"])
    
    merged = []
    current = time_periods[0]
    
    for period in time_periods[1:]:
        if period["start"] <= current["end"]:
            # Overlapping: extend current period
            current["end"] = max(current["end"], period["end"])
        else:
            # Non-overlapping: save and start new
            merged.append(current)
            current = period
    merged.append(current)
    
    # Calculate total time from merged periods
    total_years = sum(years_between(p["start"], p["end"]) for p in merged)
    return round(total_years, 2)


def calculate_frequency(entity_count: int, total_entities: int, config: dict) -> float:
    """
    Calculate frequency score (0-1) representing how often this tag appears.
    """
    if total_entities == 0:
        return 0.0
    
    return round(entity_count / total_entities, 4)


def calculate_last_used(entities: list[dict]) -> Optional[str]:
    """
    Find the most recent date across all entities.
    Returns ISO-8601 date string or None.
    """
    if not entities:
        return None
    
    dates = []
    for entity in entities:
        # For current positions/stages, use today's date
        if entity.get("is_current"):
            dt = datetime.now()
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            dates.append(dt)
        else:
            # Check all possible date fields
            for field in ["end_date", "start_date", "date", "published_at"]:
                if entity.get(field):
                    dt = parse_date(entity[field])
                    if dt:
                        # Ensure timezone-naive for comparison
                        if dt.tzinfo:
                            dt = dt.replace(tzinfo=None)
                        dates.append(dt)
    
    if not dates:
        return None
    
    most_recent = max(dates)
    return most_recent.date().isoformat()


def calculate_diversity(entities: list[dict], config: dict) -> float:
    """
    Calculate diversity score (0-1) based on variety of contexts.
    Considers both flavor (stages/oeuvre) and category (job/coding/etc).
    """
    if not entities:
        return 0.0
    
    cfg = config.get("diversity", {})
    flavor_weight = cfg.get("flavor_weight", 0.5)
    category_weight = cfg.get("category_weight", 0.5)
    saturation = cfg.get("saturation_threshold", 10)
    
    # Count unique flavors and categories
    flavors = set()
    categories = set()
    
    for entity in entities:
        if entity.get("flavor"):
            flavors.add(entity["flavor"])
        if entity.get("category"):
            categories.add(entity["category"])
    
    # Calculate scores with diminishing returns (log scale)
    flavor_score = min(1.0, math.log(len(flavors) + 1) / math.log(saturation))
    category_score = min(1.0, math.log(len(categories) + 1) / math.log(saturation))
    
    # Weighted combination
    diversity = flavor_weight * flavor_score + category_weight * category_score
    return round(diversity, 4)


def calculate_growth_trend(entities: list[dict], config: dict) -> str:
    """
    Calculate growth trend: increasing | stable | decreasing
    Uses simple linear regression on entity counts over time.
    """
    if not entities:
        return "stable"
    
    cfg = config.get("growth", {})
    min_timespan = cfg.get("min_timespan_years", 1.0)
    min_entities = cfg.get("min_entity_count", 3)
    inc_threshold = cfg.get("increasing_threshold", 0.5)
    dec_threshold = cfg.get("decreasing_threshold", -0.3)
    
    if len(entities) < min_entities:
        return "stable"
    
    # Extract years from entities - filter out None values
    entity_years = []
    for entity in entities:
        date = None
        if entity.get("date"):
            date = parse_date(entity["date"])
        elif entity.get("published_at"):
            date = parse_date(entity["published_at"])
        elif entity.get("start_date"):
            date = parse_date(entity["start_date"])
        elif entity.get("end_date"):
            date = parse_date(entity["end_date"])
        
        if date:
            year_value = date.year + date.month / 12.0
            entity_years.append(year_value)
    
    # Filter out None and check minimum count
    entity_years = [y for y in entity_years if y is not None]
    
    if not entity_years or len(entity_years) < min_entities:
        return "stable"
    
    # Check timespan
    try:
        timespan = max(entity_years) - min(entity_years)
    except (TypeError, ValueError):
        return "stable"
    
    if timespan < min_timespan:
        return "stable"
    
    # Simple linear regression: slope of entities per year
    # Group by year and count
    year_counts = defaultdict(int)
    for year in entity_years:
        year_counts[int(year)] += 1
    
    if len(year_counts) < 2:
        return "stable"
    
    years = sorted(year_counts.keys())
    counts = [year_counts[y] for y in years]
    
    # Calculate slope (entities per year)
    n = len(years)
    mean_x = sum(years) / n
    mean_y = sum(counts) / n
    
    numerator = sum((years[i] - mean_x) * (counts[i] - mean_y) for i in range(n))
    denominator = sum((years[i] - mean_x) ** 2 for i in range(n))
    
    if denominator == 0:
        return "stable"
    
    slope = numerator / denominator
    
    # Classify based on slope
    if slope >= inc_threshold:
        return "increasing"
    elif slope <= dec_threshold:
        return "decreasing"
    else:
        return "stable"


def calculate_distribution(entities: list[dict]) -> str:
    """
    Calculate distribution breakdown by flavor and category.
    Returns JSON string.
    """
    if not entities:
        return json.dumps({})
    
    flavor_counts = defaultdict(int)
    category_counts = defaultdict(int)
    
    for entity in entities:
        if entity.get("flavor"):
            flavor_counts[entity["flavor"]] += 1
        if entity.get("category"):
            category_counts[entity["category"]] += 1
    
    return json.dumps({
        "by_flavor": dict(flavor_counts),
        "by_category": dict(category_counts)
    })


def calculate_relevance(proficiency: float,
                       frequency: float,
                       last_used: Optional[str],
                       diversity: float,
                       experience_years: float,
                       growth_trend: str,
                       is_current: bool,
                       config: dict) -> float:
    """
    Calculate composite relevance score (0-100).
    Weighted combination of all metrics with bonuses/penalties.
    """
    cfg = config.get("relevance", {})
    weights = cfg.get("weights", {})
    current_bonus = cfg.get("current_bonus", 10)
    stale_penalty = cfg.get("stale_penalty", 15)
    stale_threshold = cfg.get("stale_threshold_years", 5)
    recency_decay = cfg.get("recency_decay_halflife", 3.0)
    experience_multiplier = cfg.get("experience_score_multiplier", 10.0)
    growth_scores_cfg = cfg.get("growth_scores", {"increasing": 100.0, "stable": 50.0, "decreasing": 0.0})
    
    # Normalize weights
    w_prof = weights.get("proficiency", 0.30)
    w_freq = weights.get("frequency", 0.20)
    w_rec = weights.get("recency", 0.20)
    w_div = weights.get("diversity", 0.15)
    w_exp = weights.get("experience", 0.10)
    w_growth = weights.get("growth", 0.05)
    
    # Recency score
    years_ago = years_since(parse_date(last_used)) if last_used else 999
    recency_score = 100.0 * math.exp(-years_ago / recency_decay)
    
    # Experience score (capped at 100)
    experience_score = min(100.0, experience_years * experience_multiplier)
    
    # Frequency score (0-100)
    frequency_score = frequency * 100.0
    
    # Diversity score (0-100)
    diversity_score = diversity * 100.0
    
    # Growth score
    growth_score = growth_scores_cfg.get(growth_trend, 50.0)
    
    # Base score (weighted average)
    base_score = (
        w_prof * proficiency +
        w_freq * frequency_score +
        w_rec * recency_score +
        w_div * diversity_score +
        w_exp * experience_score +
        w_growth * growth_score
    )
    
    # Apply bonuses/penalties
    if is_current:
        base_score += current_bonus
    
    if years_ago > stale_threshold:
        base_score -= stale_penalty
    
    # Clamp to 0-100
    return max(0.0, min(100.0, round(base_score, 2)))


# --- TAG METRICS CALCULATION ---

def calculate_tag_metrics(conn: sqlite3.Connection,
                         tag_name: str,
                         tag_type: str,
                         config: Optional[dict] = None) -> dict:
    """
    Calculate all metrics for a specific tag.
    Returns dict with all metric values.
    """
    if config is None:
        config = CONFIG
    
    # Collect entities
    entities = collect_tag_entities(conn, tag_name, tag_type)
    total_entities = get_total_entity_count(conn)
    
    entity_count = len(entities)
    
    # Calculate individual metrics
    proficiency = calculate_proficiency(entities, config)
    experience_years = calculate_experience_years(entities, config)
    frequency = calculate_frequency(entity_count, total_entities, config)
    last_used = calculate_last_used(entities)
    diversity_score = calculate_diversity(entities, config)
    growth_trend = calculate_growth_trend(entities, config)
    distribution = calculate_distribution(entities)
    
    # Check if any entity is current
    is_current = any(e.get("is_current") for e in entities)
    
    # Calculate composite relevance
    relevance_score = calculate_relevance(
        proficiency, frequency, last_used, diversity_score,
        experience_years, growth_trend, is_current, config
    )
    
    return {
        "tag_name": tag_name,
        "tag_type": tag_type,
        "proficiency": round(proficiency, 2),
        "experience_years": experience_years,
        "entity_count": entity_count,
        "frequency": frequency,
        "last_used": last_used,
        "diversity_score": diversity_score,
        "growth_trend": growth_trend,
        "distribution": distribution,
        "relevance_score": relevance_score,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "metrics_version": config.get("version", "1.0")
    }


# --- DATABASE OPERATIONS ---

def update_metrics_in_db(conn: sqlite3.Connection, metrics: dict):
    """
    Store or update metrics in tag_metrics table.
    """
    conn.execute("""
        INSERT OR REPLACE INTO tag_metrics (
            tag_name, tag_type, proficiency, experience_years, entity_count,
            frequency, last_used, diversity_score, growth_trend, distribution,
            relevance_score, calculated_at, metrics_version
        ) VALUES (
            :tag_name, :tag_type, :proficiency, :experience_years, :entity_count,
            :frequency, :last_used, :diversity_score, :growth_trend, :distribution,
            :relevance_score, :calculated_at, :metrics_version
        )
    """, metrics)


def get_tag_metrics(conn: sqlite3.Connection,
                   tag_name: str,
                   tag_type: str) -> Optional[dict]:
    """
    Retrieve stored metrics for a tag.
    Returns None if not found.
    """
    row = conn.execute("""
        SELECT * FROM tag_metrics
        WHERE tag_name = ? AND tag_type = ?
    """, (tag_name, tag_type)).fetchone()
    
    return dict(row) if row else None


def calculate_all_metrics(conn: sqlite3.Connection,
                         tag_type: Optional[str] = None,
                         batch_size: int = 100) -> int:
    """
    Calculate metrics for all tags (or specific tag_type).
    Returns count of tags processed.
    
    Args:
        conn: Database connection
        tag_type: Filter by tag_type (technology|skill|generic), or None for all
        batch_size: Commit every N tags
    
    Returns:
        Number of tags processed
    """
    config = CONFIG
    
    # Get all distinct tags
    sql = """
        SELECT DISTINCT t.tag, t.tag_type
        FROM tags t
        JOIN entities e ON e.id = t.entity_id
        WHERE e.visibility = 'public'
    """
    params = []
    if tag_type:
        sql += " AND t.tag_type = ?"
        params.append(tag_type)
    sql += " ORDER BY t.tag_type, t.tag"
    
    tags = conn.execute(sql, params).fetchall()
    
    count = 0
    for tag_row in tags:
        tag_name = tag_row["tag"]
        tag_type_val = tag_row["tag_type"]
        
        # Calculate metrics
        metrics = calculate_tag_metrics(conn, tag_name, tag_type_val, config)
        
        # Store in database
        update_metrics_in_db(conn, metrics)
        
        count += 1
        
        # Commit in batches
        if count % batch_size == 0:
            conn.commit()
    
    # Final commit
    conn.commit()
    
    return count
