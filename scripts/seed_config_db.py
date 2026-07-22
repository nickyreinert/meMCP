"""
scripts/seed_config_db.py — Default Config Seeder
==================================================

Seeds the DB with default prompts, metrics config, and chat config.
Sources, identity, and i18n are managed exclusively via the admin UI.

Usage:
    python scripts/seed_config_db.py [--force]

    --force: Overwrite existing DB values with defaults.
             Without this flag, existing DB rows are never overwritten.

Also callable programmatically:
    from scripts.seed_config_db import seed_defaults
    result = seed_defaults(db_path)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.models import get_db, init_db
from db.config_store import (
    set_config, get_config,
    upsert_prompt, get_prompt,
)

logger = logging.getLogger(__name__)


# ── Default prompts ──────────────────────────────────────────────────────────

_DEFAULT_PROMPTS = {
    "description_system": {
        "category": "enrichment",
        "name": "Description Generator",
        "content": (
            "You are a concise technical writing assistant.\n"
            "Given raw scraped text about a project, job, article, or skill,\n"
            "write a 1-3 sentence factual description that:\n"
            "- Mentions specific technologies by name\n"
            "- Uses active voice and present tense for ongoing work\n"
            "- Reads naturally, not like a resume bullet point\n"
            "- Is under 60 words\n"
            "Respond ONLY with the description. No preamble, no quotes."
        ),
    },
    "tag_system": {
        "category": "enrichment",
        "name": "Tag Suggester",
        "content": (
            "You are a technical tagger.\n"
            "Given a description of a project, job, or article, suggest 3-8 relevant tags.\n"
            "Tags should be specific technologies, concepts, or domains (e.g. Python, GenAI,\n"
            "Adobe Analytics, DataEngineering, SEO, Docker).\n"
            "Respond ONLY with a comma-separated list of tags. No explanation."
        ),
    },
    "type_system": {
        "category": "enrichment",
        "name": "Type Classifier",
        "content": (
            "Classify this content into exactly one entity type:\n"
            "professional, company, education, institution, side_project, literature,\n"
            "technology, skill, achievement, event.\n"
            "Respond with ONLY the entity type word. Nothing else."
        ),
    },
    "linkedin_pdf_extraction": {
        "category": "extraction",
        "name": "LinkedIn PDF Parser",
        "content": (
            "You are parsing a LinkedIn profile PDF. Extract ALL professional experience and education.\n\n"
            "IMPORTANT: LinkedIn PDFs often have:\n"
            "- Job titles and companies listed together\n"
            "- Dates in various formats (Jan 2020 - Present, 2020-2023, etc.)\n"
            "- Skills and technologies mentioned in job descriptions\n"
            "- Multiple sections: Experience, Education, Certifications\n\n"
            "Extract EVERYTHING and return ONLY valid JSON (no markdown, no explanation):\n\n"
            "{{\n"
            '  "experience": [\n'
            "    {{\n"
            '      "company": "Company Name",\n'
            '      "role": "Job Title",\n'
            '      "employment_type": "full_time",\n'
            '      "location": "City, Country",\n'
            '      "start_date": "2020-01",\n'
            '      "end_date": "2023-06",\n'
            '      "description": "What I did and accomplished...",\n'
            '      "tags": ["Python", "AWS", "DataEngineering"]\n'
            "    }}\n"
            "  ],\n"
            '  "education": [\n'
            "    {{\n"
            '      "institution": "University Name",\n'
            '      "degree": "Bachelor of Science",\n'
            '      "field": "Computer Science",\n'
            '      "start_date": "2015-09",\n'
            '      "end_date": "2019-06",\n'
            '      "description": "Studies, achievements, activities"\n'
            "    }}\n"
            "  ],\n"
            '  "certifications": [\n'
            "    {{\n"
            '      "name": "Certification Name",\n'
            '      "issuer": "Issuing Organization",\n'
            '      "issued": "2022-03",\n'
            '      "credential_id": "ABC123",\n'
            '      "credential_url": "https://..."\n'
            "    }}\n"
            "  ]\n"
            "}}\n\n"
            "LinkedIn Profile Text:\n"
            "{text}"
        ),
    },
    "translation_system": {
        "category": "translation",
        "name": "Entity Translator",
        "content": (
            "You are a professional technical translator.\n"
            "Translate the given text into {target_lang}.\n"
            "Rules:\n"
            "- Keep all proper nouns, product names, technology names, and brand names\n"
            "  in their original form (e.g. Python, Adobe Analytics, GitHub, FastAPI).\n"
            "- Keep all dates, version numbers, and URLs unchanged.\n"
            "- Match the tone and register of the original (factual, professional).\n"
            "- Produce ONLY the translated text. No preamble, no explanation, no quotes."
        ),
    },
    "greeting_system": {
        "category": "translation",
        "name": "Greeting Translator",
        "content": (
            "You are a professional translator.\n"
            "Translate the personal bio / greeting text into {target_lang}.\n"
            "Rules:\n"
            "- Keep the first-person voice and personal tone.\n"
            "- Keep all proper nouns, technology names, and place names unchanged.\n"
            "- Match the original's register (warm but professional).\n"
            "- Produce ONLY the translated text. No preamble, no explanation, no quotes."
        ),
    },
    "chat_system": {
        "category": "chat",
        "name": "Chat Proxy System Prompt",
        "content": (
            "You are a conversational assistant representing {persona_name}"
            "{tagline_suffix}.\n"
            "You speak on their behalf to people who have been granted access to their profile.\n"
            "Answer questions about their background, experience, projects, and skills.\n"
            "Always use the available tools to fetch factual data — never make things up.\n"
            "Tone: {tone}\n"
            "Format replies naturally for chat. Keep responses concise (2-4 sentences) "
            "unless more detail is requested.\n"
            "If a tool returns an access_denied error, explain that the user would need "
            "a higher-tier token for that feature."
        ),
    },
}


# ── Default metrics config (seeded on first boot) ───────────────────────────

_DEFAULT_METRICS = {
    "enabled": True,
    "version": "1.2",
    "context_weights": {
        "default_weight": 0.5,
        "stages": {"job": 1.0, "education": 0.85, "achievement": 0.7, "other": 0.8},
        "oeuvre": {
            "coding": 1.0, "talk": 0.6, "article": 0.3, "blog_post": 0.3,
            "book": 0.7, "podcast": 0.5, "video": 0.5, "website": 0.6, "other": 0.5,
        },
    },
    "proficiency": {
        "recency_weight": 0.6, "duration_weight": 0.4,
        "recency_decay_halflife": 3.0, "min_score": 5.0,
        "default_oeuvre_duration_years": 0.5, "duration_score_multiplier": 15.0,
    },
    "experience_years": {"deduplicate_overlaps": True, "current_bonus_multiplier": 1.2},
    "frequency": {"min_threshold": 3},
    "diversity": {"flavor_weight": 0.5, "category_weight": 0.5, "saturation_threshold": 10},
    "growth": {
        "min_timespan_years": 1.0, "min_entity_count": 3,
        "increasing_threshold": 0.5, "decreasing_threshold": -0.3,
    },
    "relevance": {
        "weights": {
            "proficiency": 0.30, "frequency": 0.20, "recency": 0.20,
            "diversity": 0.15, "experience": 0.10, "growth": 0.05,
        },
        "current_bonus": 10, "stale_penalty": 15, "stale_threshold_years": 5,
        "recency_decay_halflife": 3.0, "experience_score_multiplier": 10.0,
        "growth_scores": {"increasing": 100.0, "stable": 50.0, "decreasing": 0.0},
    },
}

# ── Default chat config ─────────────────────────────────────────────────────

_DEFAULT_CHAT = {
    "host": "groq",
    "model": "llama-3.3-70b-versatile",
    "ollama_url": "http://localhost:11434",
    "db_path": "data/proxy.db",
    "rate_limit_per_minute": 20,
    "max_history": 10,
    "max_input_chars": 2000,
    "max_output_chars": 3000,
    "persona": {
        "name": "the profile owner",
        "tagline": "",
        "tone": "Be warm, concise, and professional.",
    },
    "starters": [
        "What's your tech stack?",
        "Are you open to new opportunities?",
        "What was your last role?",
        "Tell me about a recent project",
    ],
}

# ── Default MCP prompt templates (user-facing, for LLM agents) ───────────────

_DEFAULT_MCP_PROMPTS = {
    "build-resume": {
        "name": "Build Resume",
        "description": "Generate a tailored resume from career data",
        "use_case": "Generate a chronological resume highlighting top skills and experience",
        "prompt_template": (
            "Act as an expert technical recruiter. Fetch my stages and skills from the meMCP server. "
            "Build a chronological resume highlighting my top 3 technologies. Format as Markdown."
        ),
    },
    "build-cover-letter": {
        "name": "Build Cover Letter",
        "description": "Write a personalized cover letter for a job application",
        "use_case": "Write a cover letter matching personal experience to specific job requirements",
        "prompt_template": (
            "Fetch my identity and recent stages. Ask me for a target job description, then draft "
            "a cover letter matching my specific experience_years and skills to the job requirements."
        ),
    },
    "visualize-knowledge": {
        "name": "Visualize Knowledge",
        "description": "Create visual representation of skills and technologies",
        "use_case": "Draw graphs or charts of skills and proficiency levels",
        "prompt_template": (
            "Fetch my technologies and their relevance scores. Generate a Mermaid.js radar chart "
            "visualizing my proficiency across different tech stacks."
        ),
    },
    "analyze-career-growth": {
        "name": "Analyze Career Growth",
        "description": "Analyze career progression and skill development over time",
        "use_case": "Identify career patterns and growth trajectories",
        "prompt_template": (
            "Fetch all stages from the meMCP server ordered by date. Analyze the progression of "
            "roles, responsibilities, and technologies used. Identify key transitions and growth "
            "patterns. Provide insights on career trajectory and skill evolution."
        ),
    },
    "project-portfolio-summary": {
        "name": "Project Portfolio Summary",
        "description": "Summarize all projects and notable work",
        "use_case": "Create an executive summary of side projects and published work",
        "prompt_template": (
            "Fetch all oeuvre entries from the meMCP server. Categorize by type (coding, blog_post, "
            "article, book, website). Summarize the breadth of work, highlighting most impactful "
            "or recent projects. Format as a portfolio overview with key metrics."
        ),
    },
    "skill-gap-analysis": {
        "name": "Skill Gap Analysis",
        "description": "Compare current skills against target role requirements",
        "use_case": "Identify missing skills for career development",
        "prompt_template": (
            "Fetch my current skills and technologies from meMCP. Ask the user for a target job "
            "description or role requirements. Perform a gap analysis identifying: 1) matching skills, "
            "2) transferable skills, 3) missing skills to develop. Provide learning recommendations."
        ),
    },
    "interview-prep": {
        "name": "Interview Preparation",
        "description": "Generate potential interview questions based on experience",
        "use_case": "Technical Interviewer",
        "prompt_template": (
            "Act as a hiring manager for a Senior Developer role. Analyze my technologies and growth metrics. "
            "Generate 5 behavioral questions based on my actual career stages and 3 technical questions "
            "based on my most used skills."
        ),
    },
    "career-coach": {
        "name": "Career Coach",
        "description": "Provide personalized career advice based on current trajectory",
        "use_case": "Career coaching and mentorship",
        "prompt_template": (
            "Compare my current technology_stack and proficiency metrics against the requirements "
            "for a [USER_INPUT: Role]. Identify which skills are missing and suggest which of my existing oeuvre "
            "projects I should expand to bridge the gap."
        ),
    },
    "blog-ideator": {
        "name": "Blog Post Ideator",
        "description": "Generate blog post ideas based on expertise and trends",
        "use_case": "Content ideation for personal branding",
        "prompt_template": (
            "Look at my oeuvre (articles and coding). Find a high-performing tag or technology with high relevance "
            "but few recent entries. Propose 3 unique blog post titles that would showcase my diversity metric."
        ),
    },
    "oss-contribution": {
        "name": "Open Source Contribution Advisor",
        "description": "Suggest open source projects to contribute to based on skills",
        "use_case": "Finding relevant open source projects for contribution",
        "prompt_template": (
            "Analyze my coding entities in oeuvre. Based on the technologies I use most frequently (e.g., Python, FastAPI), "
            "suggest what kind of Open Source projects I am best qualified to contribute to today."
        ),
    },
    "narrative-bio": {
        "name": "Narrative Bio",
        "description": "Write a compelling narrative biography",
        "use_case": "Personal branding and storytelling",
        "prompt_template": (
            "Review my stages from the earliest start_date to now. Instead of a dry list, write a 3-paragraph professional "
            "'Origin Story' that highlights the transition between my education and my most significant job roles."
        ),
    },
}


# ── Default tech config (infrastructure — previously in config.tech.yaml) ────

_DEFAULT_SERVER = {
    "host": "0.0.0.0",
    "port": 8000,
    "base_url": "http://localhost:8000",
}

_DEFAULT_SECURITY = {
    "cors_origins": ["*"],
    "trusted_proxies": ["127.0.0.1", "::1"],
}

_DEFAULT_SESSION = {
    "enabled": True,
    "timeout_hours": 5,
    "log_file": "logs/api_access.log",
    "track_coverage": True,
    "db_path": "db/sessions.db",
    "relevant_endpoints": {
        "/greeting": {"paginated": False},
        "/stages": {"paginated": True},
        "/stages/{id}": {"paginated": False},
        "/oeuvre": {"paginated": True},
        "/oeuvre/{id}": {"paginated": False},
        "/skills": {"paginated": True},
        "/skills/{name}": {"paginated": False},
        "/technology": {"paginated": True},
        "/technology/{name}": {"paginated": False},
        "/tags/{tag_name}": {"paginated": False},
    },
}

_DEFAULT_LLM = {
    "backend": "ollama",
    "model": "mistral-small:24b-instruct-2501-q4_K_M",
    "groq_api_key": "",
    "ollama_url": "http://localhost:11434",
    "shrink_text": False,
    "shrink_skip_chars": 3,
}

_DEFAULT_CACHE = {
    "cache_dir": ".cache",
    "cache_ttl_hours": 6,
    "watch_interval_minutes": 120,
}

_DEFAULT_ENDPOINTS = {
    "mcp_required": [
        "/greeting",
        "/categories",
        "/entities",
        "/entities/{entity_id}",
        "/entities/{entity_id}/related",
        "/category/{entity_flavor}",
        "/tags",
        "/tags/{tag_name}",
        "/search",
        "/graph",
        "/skills",
        "/skills/{skill_name}",
        "/technology",
        "/technology/{tech_name}",
        "/technologies",
        "/stages",
        "/stages/{entity_id}",
        "/oeuvre",
        "/oeuvre/{entity_id}",
        "/prompts/{prompt_id}",
        "/mcp/tools/call",
        "/mcp/resources/read",
        "/admin/rebuild",
        "/admin/translate",
    ],
}


# ── Seeding logic ────────────────────────────────────────────────────────────

def seed_defaults(db_path: Path, force: bool = False) -> dict:
    """
    Seed DB with default prompts and initial config values.

    Args:
        db_path: Path to the SQLite database.
        force: If True, overwrite existing DB values.

    Returns:
        {"config_rows": N, "prompt_rows": N, "skipped": N}
    """
    conn = get_db(db_path)
    stats = {"config_rows": 0, "prompt_rows": 0, "skipped": 0}

    # ── Seed default prompts ──
    _seed_prompts(conn, force, stats)

    # ── Seed default metrics config (if DB has none) ──
    _seed_section(conn, "metrics", _DEFAULT_METRICS, force, stats)

    # ── Seed default chat config (if DB has none) ──
    _seed_section(conn, "chat", _DEFAULT_CHAT, force, stats)

    # ── Seed default MCP prompt templates ──
    _seed_mcp_prompts(conn, force, stats)

    # ── Seed tech config (infrastructure) ──
    _seed_section(conn, "server", _DEFAULT_SERVER, force, stats)
    _seed_section(conn, "security", _DEFAULT_SECURITY, force, stats)
    _seed_section(conn, "session", _DEFAULT_SESSION, force, stats)
    _seed_section(conn, "llm", _DEFAULT_LLM, force, stats)
    _seed_section(conn, "cache", _DEFAULT_CACHE, force, stats)
    _seed_section(conn, "endpoints", _DEFAULT_ENDPOINTS, force, stats)

    conn.commit()
    conn.close()
    return stats


# Backward-compat alias
seed_from_yaml = seed_defaults


def _set_if_new(conn, namespace, key, value, force, stats, updated_by="seed"):
    """Set config only if not already present (unless force=True)."""
    if not force and get_config(conn, namespace, key) is not None:
        stats["skipped"] += 1
        return
    set_config(conn, namespace, key, value, updated_by=updated_by)
    stats["config_rows"] += 1


def _seed_section(conn, namespace, defaults, force, stats):
    """Seed a config namespace from a defaults dict."""
    for key, value in defaults.items():
        _set_if_new(conn, namespace, key, value, force, stats)


def _seed_prompts(conn, force, stats):
    for prompt_id, info in _DEFAULT_PROMPTS.items():
        if not force and get_prompt(conn, prompt_id) is not None:
            stats["skipped"] += 1
            continue
        upsert_prompt(
            conn, prompt_id, info["category"], info["name"],
            info["content"], updated_by="seed",
        )
        stats["prompt_rows"] += 1


def _seed_mcp_prompts(conn, force, stats):
    """Seed MCP prompt templates into config table (namespace='mcp_prompts')."""
    for prompt_id, info in _DEFAULT_MCP_PROMPTS.items():
        _set_if_new(conn, "mcp_prompts", prompt_id, info, force, stats)


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    force = "--force" in sys.argv

    db_path_str = os.environ.get("MEMCP_DB_PATH", "db/profile.db")
    db_path = Path(db_path_str)
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    init_db(db_path)
    result = seed_defaults(db_path, force=force)
    logger.info("Seeding complete: %s", result)
