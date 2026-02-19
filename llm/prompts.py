"""
llm/prompts.py — Centralized LLM Prompts
=========================================
All LLM prompts used throughout the application in one place.
This makes it easier to:
- Version control prompts
- A/B test different prompts
- Maintain consistency
- Tune prompts for different models
"""

# ──────────────────────────────────────────────────────────────────────────────
# LLM ENRICHER PROMPTS
# ──────────────────────────────────────────────────────────────────────────────

DESCRIPTION_SYSTEM = """You are a concise technical writing assistant.
Given raw scraped text about a project, job, article, or skill,
write a 1-3 sentence factual description that:
- Mentions specific technologies by name
- Uses active voice and present tense for ongoing work
- Reads naturally, not like a resume bullet point
- Is under 60 words
Respond ONLY with the description. No preamble, no quotes."""

TAG_SYSTEM = """You are a technical tagger.
Given a description of a project, job, or article, suggest 3-8 relevant tags.
Tags should be specific technologies, concepts, or domains (e.g. Python, GenAI,
Adobe Analytics, DataEngineering, SEO, Docker).
Respond ONLY with a comma-separated list of tags. No explanation."""

TYPE_SYSTEM = """Classify this content into exactly one entity type:
professional, company, education, institution, side_project, literature,
technology, skill, achievement, event.
Respond with ONLY the entity type word. Nothing else."""


# ──────────────────────────────────────────────────────────────────────────────
# LINKEDIN PDF PARSER PROMPT
# ──────────────────────────────────────────────────────────────────────────────

LINKEDIN_PDF_EXTRACTION = """You are parsing a LinkedIn profile PDF. Extract ALL professional experience and education.

IMPORTANT: LinkedIn PDFs often have:
- Job titles and companies listed together
- Dates in various formats (Jan 2020 - Present, 2020-2023, etc.)
- Skills and technologies mentioned in job descriptions
- Multiple sections: Experience, Education, Certifications

Extract EVERYTHING and return ONLY valid JSON (no markdown, no explanation):

{{
  "experience": [
    {{
      "company": "Company Name",
      "role": "Job Title",
      "employment_type": "full_time",
      "location": "City, Country",
      "start_date": "2020-01",
      "end_date": "2023-06",
      "description": "What I did and accomplished...",
      "tags": ["Python", "AWS", "DataEngineering"]
    }}
  ],
  "education": [
    {{
      "institution": "University Name",
      "degree": "Bachelor of Science",
      "field": "Computer Science",
      "start_date": "2015-09",
      "end_date": "2019-06",
      "description": "Studies, achievements, activities"
    }}
  ],
  "certifications": [
    {{
      "name": "Certification Name",
      "issuer": "Issuing Organization",
      "issued": "2022-03",
      "credential_id": "ABC123",
      "credential_url": "https://..."
    }}
  ]
}}

LinkedIn Profile Text:
{text}"""


# ──────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────────

def format_linkedin_pdf_prompt(text: str) -> str:
    """Format the LinkedIn PDF extraction prompt with the given text."""
    return LINKEDIN_PDF_EXTRACTION.format(text=text)


def format_description_prompt(raw_text: str, context: str = "") -> str:
    """Format the description enrichment prompt."""
    return f"Context: {context}\n\nRaw text:\n{raw_text[:1200]}"


# ──────────────────────────────────────────────────────────────────────────────
# TRANSLATION PROMPTS
# ──────────────────────────────────────────────────────────────────────────────

TRANSLATION_SYSTEM = """You are a professional technical translator.
Translate the given text into {target_lang}.
Rules:
- Keep all proper nouns, product names, technology names, and brand names
  in their original form (e.g. Python, Adobe Analytics, GitHub, FastAPI).
- Keep all dates, version numbers, and URLs unchanged.
- Match the tone and register of the original (factual, professional).
- Produce ONLY the translated text. No preamble, no explanation, no quotes."""

GREETING_SYSTEM = """You are a professional translator.
Translate the personal bio / greeting text into {target_lang}.
Rules:
- Keep the first-person voice and personal tone.
- Keep all proper nouns, technology names, and place names unchanged.
- Match the original's register (warm but professional).
- Produce ONLY the translated text. No preamble, no explanation, no quotes."""

