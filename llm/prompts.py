"""
llm/prompts.py — Centralized LLM Prompts (DB-backed)
=====================================================
Prompts are stored in the ``llm_prompts`` DB table and editable via the admin UI.
Hardcoded ``_DEFAULTS`` serve as fallback when the DB has no data yet (first boot).

Public API:
  get_prompt(prompt_id, conn=None) → str   # returns prompt content
  format_linkedin_pdf_prompt(text)         # convenience wrapper
  format_description_prompt(raw_text, ctx) # convenience wrapper
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# HARDCODED DEFAULTS (fallback when DB is empty or unavailable)
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULTS: dict[str, str] = {
    "description_system": (
        "You are a concise technical writing assistant.\n"
        "Given raw scraped text about a project, job, article, or skill,\n"
        "write a 1-3 sentence factual description that:\n"
        "- Mentions specific technologies by name\n"
        "- Uses active voice and present tense for ongoing work\n"
        "- Reads naturally, not like a resume bullet point\n"
        "- Is under 60 words\n"
        "Respond ONLY with the description. No preamble, no quotes."
    ),
    "tag_system": (
        "You are a technical tagger.\n"
        "Given a description of a project, job, or article, suggest 3-8 relevant tags.\n"
        "Tags should be specific technologies, concepts, or domains (e.g. Python, GenAI,\n"
        "Adobe Analytics, DataEngineering, SEO, Docker).\n"
        "Respond ONLY with a comma-separated list of tags. No explanation."
    ),
    "type_system": (
        "Classify this content into exactly one entity type:\n"
        "professional, company, education, institution, side_project, literature,\n"
        "technology, skill, achievement, event.\n"
        "Respond with ONLY the entity type word. Nothing else."
    ),
    "linkedin_pdf_extraction": (
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
    "translation_system": (
        "You are a professional technical translator.\n"
        "Translate the given text into {target_lang}.\n"
        "Rules:\n"
        "- Keep all proper nouns, product names, technology names, and brand names\n"
        "  in their original form (e.g. Python, Adobe Analytics, GitHub, FastAPI).\n"
        "- Keep all dates, version numbers, and URLs unchanged.\n"
        "- Match the tone and register of the original (factual, professional).\n"
        "- Produce ONLY the translated text. No preamble, no explanation, no quotes."
    ),
    "greeting_system": (
        "You are a professional translator.\n"
        "Translate the personal bio / greeting text into {target_lang}.\n"
        "Rules:\n"
        "- Keep the first-person voice and personal tone.\n"
        "- Keep all proper nouns, technology names, and place names unchanged.\n"
        "- Match the original's register (warm but professional).\n"
        "- Produce ONLY the translated text. No preamble, no explanation, no quotes."
    ),
    "chat_system": (
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
}


# ──────────────────────────────────────────────────────────────────────────────
# DB-BACKED PROMPT RETRIEVAL
# ──────────────────────────────────────────────────────────────────────────────

def get_prompt(prompt_id: str, conn: Optional[sqlite3.Connection] = None) -> str:
    """
    Return the prompt text for *prompt_id*.

    Resolution order:
      1. DB ``llm_prompts`` table (if *conn* provided and row exists)
      2. Hardcoded ``_DEFAULTS``

    Raises KeyError if prompt_id is unknown everywhere.
    """
    if conn is not None:
        try:
            from db.config_store import get_prompt as _db_get
            row = _db_get(conn, prompt_id)
            if row and row.get("content") and row.get("is_active", 1):
                return row["content"]
        except Exception as exc:
            logger.debug("DB prompt lookup failed for %s: %s", prompt_id, exc)

    if prompt_id in _DEFAULTS:
        return _DEFAULTS[prompt_id]

    raise KeyError(f"Unknown prompt_id: {prompt_id!r}")


# ──────────────────────────────────────────────────────────────────────────────
# CONVENIENCE CONSTANTS (backward-compat for direct importers)
# ──────────────────────────────────────────────────────────────────────────────

DESCRIPTION_SYSTEM  = _DEFAULTS["description_system"]
TAG_SYSTEM          = _DEFAULTS["tag_system"]
TYPE_SYSTEM         = _DEFAULTS["type_system"]
LINKEDIN_PDF_EXTRACTION = _DEFAULTS["linkedin_pdf_extraction"]
TRANSLATION_SYSTEM  = _DEFAULTS["translation_system"]
GREETING_SYSTEM     = _DEFAULTS["greeting_system"]


# ──────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────────

def format_linkedin_pdf_prompt(text: str, conn: Optional[sqlite3.Connection] = None) -> str:
    """Format the LinkedIn PDF extraction prompt with the given text."""
    prompt = get_prompt("linkedin_pdf_extraction", conn)
    return prompt.format(text=text)


def format_description_prompt(raw_text: str, context: str = "") -> str:
    """Format the description enrichment prompt."""
    return f"Context: {context}\n\nRaw text:\n{raw_text[:1200]}"

