"""
app/services/intelligence.py — Intelligence Hub
================================================

Central service layer for all AI-powered features available exclusively to
Elevated-tier tokens.  Two external providers are used:

  - Groq  (llama-3.3-70b-versatile) — fast reasoning, logic, RAG synthesis.
  - Perplexity (sonar-reasoning)     — live web search with citations.

Token Budget
------------
Every Elevated token is subject to two independent limits (configurable in
config.yaml, overridable per-token via manage_tokens.py):

  max_tokens_per_session  – total LLM *output* tokens across this HTTP session.
  max_calls_per_day       – total intelligence API calls logged in usage_logs today.

When a limit is reached the hub raises HTTP 429 (Too Many Requests).

Truncation
----------
  max_input_chars  – incoming text is truncated to this length before being
                     sent to the LLM (saves cost / context window).
  max_output_chars – LLM response is truncated to this length before being
                     returned to the caller (saves bandwidth).

Both values are read from config.yaml / per-token overrides.

Usage Logging
-------------
Every intelligence call appends a row to usage_logs containing:
  token_id, endpoint_called, timestamp, input_args (JSON),
  tier, api_provider, input_length, input_text (raw user input),
  tokens_used (LLM output tokens from the API response).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from fastapi import HTTPException

from app.dependencies.access_control import TokenInfo, log_usage

logger = logging.getLogger(__name__)


# ── Config loader ────────────────────────────────────────────────────────────

def _load_intelligence_config() -> dict:
    """Load the [intelligence] section from config.yaml."""
    try:
        import yaml  # PyYAML
        config_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        with open(config_path) as fh:
            cfg = yaml.safe_load(fh)
        return cfg.get("intelligence", {})
    except Exception as exc:
        logger.warning("Could not load intelligence config: %s", exc)
        return {}


# ── Defaults (mirrors config.yaml defaults) ───────────────────────────────────

_DEFAULTS = {
    "max_tokens_per_session": 4000,
    "max_calls_per_day":      20,
    "max_input_chars":        2000,
    "max_output_chars":       3000,
}


def _resolve(token_info: TokenInfo, key: str) -> int:
    """
    Return the effective limit for *key*.
    Priority: per-token override > config.yaml > hard-coded default.
    """
    per_token = getattr(token_info, key, None)
    if per_token is not None:
        return per_token
    cfg_val = _load_intelligence_config().get(key)
    if cfg_val is not None:
        return int(cfg_val)
    return _DEFAULTS[key]


# ── Text helpers ──────────────────────────────────────────────────────────────

def _truncate(text: str, max_chars: int, label: str = "text") -> str:
    """Truncate *text* to *max_chars* with a visible marker."""
    if len(text) <= max_chars:
        return text
    cutoff = max(0, max_chars - 3)
    truncated = text[:cutoff] + "…"
    logger.debug("Truncated %s from %d to %d chars", label, len(text), len(truncated))
    return truncated


# ── Budget checker ────────────────────────────────────────────────────────────

def _check_daily_budget(conn: sqlite3.Connection, token_info: TokenInfo) -> int:
    """
    Count intelligence calls made today for this token.
    Raises HTTP 429 if the daily cap is exceeded.
    Returns the current call count (before the upcoming call is logged).
    """
    max_calls = _resolve(token_info, "max_calls_per_day")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        """
        SELECT COUNT(*) FROM usage_logs
        WHERE token_id = ?
          AND api_provider IS NOT NULL
          AND timestamp >= ?
        """,
        (token_info.id, today),
    ).fetchone()
    count = row[0] if row else 0
    if count >= max_calls:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "daily_budget_exceeded",
                "calls_today": count,
                "max_calls_per_day": max_calls,
                "message": (
                    f"Daily intelligence call limit reached ({count}/{max_calls}). "
                    "Resets at midnight UTC. Contact the token owner to increase limits."
                ),
            },
        )
    return count


def _check_session_token_budget(
    conn: sqlite3.Connection,
    token_info: TokenInfo,
    tokens_about_to_use: int,
) -> int:
    """
    Sum LLM output tokens consumed today for this token and check against
    the session budget (we treat 'session' as the rolling current day to keep
    it stateless — no server-side session object needed).
    Raises HTTP 429 if the budget is exceeded.
    Returns the current consumed token count.
    """
    max_tok = _resolve(token_info, "max_tokens_per_session")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        """
        SELECT COALESCE(SUM(tokens_used), 0) FROM usage_logs
        WHERE token_id = ?
          AND api_provider IS NOT NULL
          AND timestamp >= ?
        """,
        (token_info.id, today),
    ).fetchone()
    used = row[0] if row else 0
    if used + tokens_about_to_use > max_tok:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "token_budget_exceeded",
                "tokens_used_today": used,
                "max_tokens_per_session": max_tok,
                "message": (
                    f"Token budget exceeded ({used}/{max_tok} output tokens used today). "
                    "Resets at midnight UTC. Contact the token owner to increase limits."
                ),
            },
        )
    return used


# ── Intelligence usage logger ─────────────────────────────────────────────────

def log_intelligence_call(
    conn: sqlite3.Connection,
    token_info: TokenInfo,
    endpoint: str,
    api_provider: str,
    raw_input: str,
    input_args: Optional[dict],
    tokens_used: int,
) -> None:
    """
    Persist a detailed intelligence call record to usage_logs.

    Stored fields (visible in manage_tokens.py stats):
      - token_id          token that made the call
      - endpoint_called   e.g. /mcp/intelligence/simulate_interview
      - timestamp         UTC ISO-8601
      - input_args        JSON dict of structured arguments (tool name, role, etc.)
      - tier              elevated
      - api_provider      groq | perplexity
      - input_length      character count of raw_input as received
      - input_text        raw_input (already truncated to max_input_chars)
      - tokens_used       LLM output tokens from API response
    """
    conn.execute(
        """
        INSERT INTO usage_logs
            (token_id, endpoint_called, timestamp, input_args,
             tier, api_provider, input_length, input_text, tokens_used)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            token_info.id,
            endpoint,
            datetime.now(timezone.utc).isoformat(),
            json.dumps(input_args, ensure_ascii=False) if input_args else None,
            token_info.stage,
            api_provider,
            len(raw_input),
            raw_input,
            tokens_used,
        ),
    )
    conn.commit()


# ── Groq provider ─────────────────────────────────────────────────────────────

class _GroqProvider:
    """
    Thin wrapper around the Groq chat-completions API.
    Uses the `groq` SDK if installed; falls back to raw HTTP via requests.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
    ) -> tuple[str, int]:
        """
        Send a chat-completion request to Groq.

        Returns (answer_text, output_tokens_used).
        Raises HTTPException 502 on provider errors.
        """
        if not self.api_key:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "groq_not_configured",
                    "message": (
                        "Groq API key is not configured. "
                        "Set intelligence.groq_model and llm.groq_api_key in config.yaml."
                    ),
                },
            )
        try:
            import groq as groq_sdk  # type: ignore
            client = groq_sdk.Groq(api_key=self.api_key)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.7,
            )
            text = resp.choices[0].message.content or ""
            tokens = resp.usage.completion_tokens if resp.usage else 0
            return text, tokens
        except ImportError:
            # groq SDK not available — fall back to raw HTTP
            return self._complete_http(system_prompt, user_prompt, max_tokens)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Groq API error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={"error": "groq_api_error", "message": str(exc)},
            )

    def _complete_http(
        self, system_prompt: str, user_prompt: str, max_tokens: int
    ) -> tuple[str, int]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type":  "application/json",
                },
                json=payload,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
            tokens = data.get("usage", {}).get("completion_tokens", 0)
            return text, tokens
        except requests.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "groq_api_error", "message": str(exc)},
            )


# ── Perplexity provider ───────────────────────────────────────────────────────

class _PerplexityProvider:
    """
    Wrapper for the Perplexity AI chat-completions endpoint (OpenAI-compatible).
    Returns the answer text plus a list of citation URLs.
    """

    _API_URL = "https://api.perplexity.ai/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def search(
        self,
        system_prompt: str,
        query: str,
        max_tokens: int = 1000,
    ) -> tuple[str, list[str], int]:
        """
        Send a search query to Perplexity.

        Returns (answer_text, citations_list, output_tokens_used).
        Raises HTTPException 502 on provider errors.
        """
        if not self.api_key:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "perplexity_not_configured",
                    "message": (
                        "Perplexity API key is not configured. "
                        "Set intelligence.perplexity_api_key in config.yaml."
                    ),
                },
            )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": query},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "return_citations": True,
        }
        try:
            r = requests.post(
                self._API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type":  "application/json",
                },
                json=payload,
                timeout=45,
            )
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
            citations: list[str] = data.get("citations", [])
            tokens = data.get("usage", {}).get("completion_tokens", 0)
            return text, citations, tokens
        except requests.HTTPError as exc:
            logger.error("Perplexity API error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={"error": "perplexity_api_error", "message": str(exc)},
            )
        except Exception as exc:
            logger.error("Perplexity request failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={"error": "perplexity_request_failed", "message": str(exc)},
            )


# ── Intelligence Hub ──────────────────────────────────────────────────────────

class IntelligenceHub:
    """
    Central facade for all AI-powered intelligence tools.

    Each public method:
      1. Checks daily call budget → HTTP 429 if exceeded.
      2. Truncates incoming text to max_input_chars.
      3. Queries the database for relevant profile context.
      4. Calls the appropriate LLM provider.
      5. Checks session token budget → HTTP 429 if exceeded.
      6. Truncates the LLM response to max_output_chars.
      7. Logs the full call to usage_logs.
      8. Returns a structured result dict.
    """

    def __init__(self) -> None:
        cfg = _load_intelligence_config()
        llm_cfg: dict = {}
        try:
            import yaml
            root_cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            with open(root_cfg_path) as fh:
                llm_cfg = yaml.safe_load(fh).get("llm", {})
        except Exception:
            pass

        self._groq = _GroqProvider(
            api_key=cfg.get("groq_api_key") or llm_cfg.get("groq_api_key", ""),
            model=cfg.get("groq_model", "llama-3.3-70b-versatile"),
        )
        self._perplexity = _PerplexityProvider(
            api_key=cfg.get("perplexity_api_key", ""),
            model=cfg.get("perplexity_model", "sonar-reasoning"),
        )

    # ── simulate_interview ────────────────────────────────────────────────────

    def simulate_interview(
        self,
        conn: sqlite3.Connection,
        token_info: TokenInfo,
        job_description: str,
        endpoint: str = "/mcp/intelligence/simulate_interview",
    ) -> dict:
        """
        Generate 3 targeted interview questions using actual project experience.

        DB queries: oeuvre (portfolio) + skills tags for relevant context.
        Provider: Groq (llama-3.3-70b-versatile).
        """
        _check_daily_budget(conn, token_info)
        max_in = _resolve(token_info, "max_input_chars")
        max_out = _resolve(token_info, "max_output_chars")

        raw_input = job_description
        job_desc_trunc = _truncate(job_description, max_in, "job_description")

        # Pull relevant portfolio context from the DB
        oeuvre_rows = conn.execute(
            """
            SELECT e.title, e.description, GROUP_CONCAT(t.tag, ', ') AS tags
            FROM entities e
            LEFT JOIN tags t ON t.entity_id = e.id
            WHERE e.flavor = 'oeuvre' AND e.visibility = 'public'
            GROUP BY e.id
            ORDER BY e.date DESC, e.start_date DESC
            LIMIT 10
            """,
        ).fetchall()

        skills_rows = conn.execute(
            """
            SELECT tag, proficiency, experience_years
            FROM tag_metrics
            WHERE tag_type = 'skill'
            ORDER BY relevance_score DESC
            LIMIT 10
            """,
        ).fetchall()

        oeuvre_ctx = "\n".join(
            f"- {r['title']}: {(r['description'] or '')[:200]}  [tags: {r['tags'] or 'n/a'}]"
            for r in oeuvre_rows
        )
        skills_ctx = "\n".join(
            f"- {r['tag']} (proficiency: {r['proficiency']:.0f}/100, "
            f"{r['experience_years']:.1f} yrs)"
            for r in skills_rows
        )

        system_prompt = textwrap.dedent("""
            You are an expert technical interviewer preparing challenging but fair
            questions for a candidate.  Your questions must be grounded in the
            candidate's *actual* project experience and skills listed below.
            Output exactly 3 interview questions as a numbered list.
            Each question should probe a different dimension:
            (1) technical depth, (2) problem-solving approach, (3) cross-team impact.
        """).strip()

        user_prompt = textwrap.dedent(f"""
            JOB DESCRIPTION:
            {job_desc_trunc}

            CANDIDATE PORTFOLIO (recent projects):
            {oeuvre_ctx or 'No portfolio data available.'}

            CANDIDATE SKILLS:
            {skills_ctx or 'No skills data available.'}

            Generate 3 targeted interview questions based on the above.
        """).strip()

        _check_session_token_budget(conn, token_info, 300)  # reserve estimate
        answer, tokens_used = self._groq.complete(system_prompt, user_prompt, max_tokens=600)
        answer_trunc = _truncate(answer, max_out, "simulate_interview response")

        log_intelligence_call(
            conn, token_info, endpoint, "groq", raw_input,
            {"tool": "simulate_interview", "job_description_length": len(raw_input)},
            tokens_used,
        )

        return {
            "tool": "simulate_interview",
            "provider": "groq",
            "model": self._groq.model,
            "questions": answer_trunc,
            "tokens_used": tokens_used,
            "context_used": {
                "portfolio_items": len(oeuvre_rows),
                "skills": len(skills_rows),
            },
        }

    # ── market_relevance_check ────────────────────────────────────────────────

    def market_relevance_check(
        self,
        conn: sqlite3.Connection,
        token_info: TokenInfo,
        role: str,
        endpoint: str = "/mcp/intelligence/market_relevance_check",
    ) -> dict:
        """
        Benchmark the technology stack against 2026 industry trends.

        DB queries: tag_metrics (technology type) for the stack snapshot.
        Provider: Perplexity (sonar-reasoning) for live web data.
        """
        _check_daily_budget(conn, token_info)
        max_in = _resolve(token_info, "max_input_chars")
        max_out = _resolve(token_info, "max_output_chars")

        raw_input = role
        role_trunc = _truncate(role, max_in, "role")

        tech_rows = conn.execute(
            """
            SELECT tag_name, proficiency, experience_years, growth_trend,
                   last_used, relevance_score
            FROM tag_metrics
            WHERE tag_type = 'technology'
            ORDER BY relevance_score DESC
            LIMIT 20
            """,
        ).fetchall()

        tech_ctx = "\n".join(
            f"- {r['tag_name']}: proficiency {r['proficiency']:.0f}/100, "
            f"{r['experience_years']:.1f} yrs exp, trend={r['growth_trend']}, "
            f"last_used={r['last_used'] or 'unknown'}"
            for r in tech_rows
        )

        system_prompt = textwrap.dedent("""
            You are a senior technology analyst benchmarking a developer's skill set
            against current (2026) industry demands.  Use live web data to assess
            demand, salary impact, and future outlook for each technology.
            Respond with:
              1. A "Future-Proof Score" from 0-100 for the overall stack.
              2. Top 3 strengths (in-demand technologies).
              3. Top 3 gaps or declining technologies to reconsider.
              4. 2-3 recommended technologies to add.
            Always cite your sources.
        """).strip()

        query = textwrap.dedent(f"""
            Role being evaluated: {role_trunc}

            Current technology stack:
            {tech_ctx or 'No technology data available.'}

            Benchmark this stack against 2026 industry trends for the given role.
            Provide a Future-Proof Score with reasoning and citations.
        """).strip()

        _check_session_token_budget(conn, token_info, 500)
        answer, citations, tokens_used = self._perplexity.search(
            system_prompt, query, max_tokens=800
        )
        answer_trunc = _truncate(answer, max_out, "market_relevance_check response")

        log_intelligence_call(
            conn, token_info, endpoint, "perplexity", raw_input,
            {"tool": "market_relevance_check", "role": role},
            tokens_used,
        )

        return {
            "tool": "market_relevance_check",
            "provider": "perplexity",
            "model": self._perplexity.model,
            "role": role,
            "analysis": answer_trunc,
            "citations": citations,
            "tokens_used": tokens_used,
            "technologies_evaluated": len(tech_rows),
        }

    # ── ask_profile_agent ─────────────────────────────────────────────────────

    def ask_profile_agent(
        self,
        conn: sqlite3.Connection,
        token_info: TokenInfo,
        question: str,
        endpoint: str = "/mcp/intelligence/ask_profile_agent",
    ) -> dict:
        """
        General-purpose RAG: search entities, synthesise a professional answer.

        DB queries: entities full-text search for relevant context.
        Provider: Groq (llama-3.3-70b-versatile).
        """
        _check_daily_budget(conn, token_info)
        max_in = _resolve(token_info, "max_input_chars")
        max_out = _resolve(token_info, "max_output_chars")

        raw_input = question
        question_trunc = _truncate(question, max_in, "question")

        # Keyword-based entity retrieval (simple but effective for SQLite)
        keywords = [kw.strip() for kw in question_trunc.split() if len(kw.strip()) > 3][:10]
        like_clauses = " OR ".join(
            f"(e.title LIKE ? OR e.description LIKE ?)" for _ in keywords
        )
        params: list = []
        for kw in keywords:
            params += [f"%{kw}%", f"%{kw}%"]

        if like_clauses:
            entity_rows = conn.execute(
                f"""
                SELECT e.title, e.flavor, e.category, e.description,
                       e.start_date, e.end_date, e.date,
                       GROUP_CONCAT(t.tag, ', ') AS tags
                FROM entities e
                LEFT JOIN tags t ON t.entity_id = e.id
                WHERE e.visibility = 'public'
                  AND ({like_clauses})
                GROUP BY e.id
                LIMIT 8
                """,
                params,
            ).fetchall()
        else:
            entity_rows = []

        # If nothing matched fall back to identity entities
        if not entity_rows:
            entity_rows = conn.execute(
                """
                SELECT e.title, e.flavor, e.category, e.description,
                       e.start_date, e.end_date, e.date,
                       GROUP_CONCAT(t.tag, ', ') AS tags
                FROM entities e
                LEFT JOIN tags t ON t.entity_id = e.id
                WHERE e.visibility = 'public' AND e.flavor = 'identity'
                GROUP BY e.id
                LIMIT 5
                """,
            ).fetchall()

        context_parts: list[str] = []
        for r in entity_rows:
            date_str = r["date"] or r["start_date"] or ""
            end_str  = f" → {r['end_date']}" if r["end_date"] else ""
            desc     = (r["description"] or "")[:300]
            context_parts.append(
                f"[{r['flavor']}/{r['category']}] {r['title']} {date_str}{end_str}\n"
                f"  {desc}\n  Tags: {r['tags'] or 'n/a'}"
            )

        context = "\n\n".join(context_parts) or "No relevant entities found."

        system_prompt = textwrap.dedent("""
            You are a professional profile assistant.  Answer questions about the
            person's background, experience, and skills using only the context
            provided.  Be concise, objective, and professional.
            If the context does not contain enough information, say so clearly.
        """).strip()

        user_prompt = textwrap.dedent(f"""
            QUESTION: {question_trunc}

            RELEVANT PROFILE DATA:
            {context}

            Answer the question based on the data above.
        """).strip()

        _check_session_token_budget(conn, token_info, 300)
        answer, tokens_used = self._groq.complete(system_prompt, user_prompt, max_tokens=600)
        answer_trunc = _truncate(answer, max_out, "ask_profile_agent response")

        log_intelligence_call(
            conn, token_info, endpoint, "groq", raw_input,
            {"tool": "ask_profile_agent", "question_length": len(raw_input)},
            tokens_used,
        )

        return {
            "tool": "ask_profile_agent",
            "provider": "groq",
            "model": self._groq.model,
            "answer": answer_trunc,
            "tokens_used": tokens_used,
            "context_used": {"entities_retrieved": len(entity_rows)},
        }

    # ── job_match ─────────────────────────────────────────────────────────────

    def job_match(
        self,
        conn: sqlite3.Connection,
        token_info: TokenInfo,
        job_description: str,
        endpoint: str = "/mcp/intelligence/job_match",
    ) -> dict:
        """
        Score how well the profile matches a job description.

        DB queries: skills, technologies, stages (career) for profile snapshot.
        Provider: Groq (llama-3.3-70b-versatile).
        Returns a match score (0-100), matched strengths, gaps, and verdict.
        """
        _check_daily_budget(conn, token_info)
        max_in = _resolve(token_info, "max_input_chars")
        max_out = _resolve(token_info, "max_output_chars")

        raw_input = job_description
        jd_trunc = _truncate(job_description, max_in, "job_description")

        # Skills snapshot
        skill_rows = conn.execute(
            """
            SELECT tag_name, proficiency, experience_years
            FROM tag_metrics
            WHERE tag_type = 'skill'
            ORDER BY relevance_score DESC
            LIMIT 15
            """,
        ).fetchall()

        # Tech snapshot
        tech_rows = conn.execute(
            """
            SELECT tag_name, proficiency, experience_years, growth_trend
            FROM tag_metrics
            WHERE tag_type = 'technology'
            ORDER BY relevance_score DESC
            LIMIT 20
            """,
        ).fetchall()

        # Career stages (most recent jobs)
        stage_rows = conn.execute(
            """
            SELECT e.title, e.category, e.description, e.start_date, e.end_date,
                   e.is_current, GROUP_CONCAT(t.tag, ', ') AS tags
            FROM entities e
            LEFT JOIN tags t ON t.entity_id = e.id
            WHERE e.flavor = 'stages' AND e.visibility = 'public'
            GROUP BY e.id
            ORDER BY e.start_date DESC
            LIMIT 6
            """,
        ).fetchall()

        skills_ctx = ", ".join(
            f"{r['tag_name']} ({r['proficiency']:.0f}%)" for r in skill_rows
        )
        tech_ctx = ", ".join(
            f"{r['tag_name']} ({r['proficiency']:.0f}%)" for r in tech_rows
        )
        stages_ctx = "\n".join(
            f"- {r['title']} [{r['category']}] {r['start_date'] or '?'}"
            f"{'→present' if r['is_current'] else ('→' + (r['end_date'] or '?'))}: "
            f"{(r['description'] or '')[:150]}  [tags: {r['tags'] or 'n/a'}]"
            for r in stage_rows
        )

        system_prompt = textwrap.dedent("""
            You are a talent-matching AI.  Analyse a job description against a
            candidate's profile and produce a structured match report.
            Your response must include:
              1. Match Score: an integer 0-100.
              2. Matched Strengths: bullet list of requirements the candidate clearly meets.
              3. Gaps: bullet list of requirements the candidate does not meet or lacks evidence for.
              4. Verdict: one sentence summarising fit (e.g. "Strong match", "Partial match",
                 "Poor match") with a brief rationale.
            Be objective and specific.
        """).strip()

        user_prompt = textwrap.dedent(f"""
            JOB DESCRIPTION:
            {jd_trunc}

            CANDIDATE SKILLS: {skills_ctx or 'none listed'}
            CANDIDATE TECHNOLOGIES: {tech_ctx or 'none listed'}
            CAREER HISTORY:
            {stages_ctx or 'No career history available.'}

            Produce the match report.
        """).strip()

        _check_session_token_budget(conn, token_info, 400)
        answer, tokens_used = self._groq.complete(system_prompt, user_prompt, max_tokens=700)
        answer_trunc = _truncate(answer, max_out, "job_match response")

        log_intelligence_call(
            conn, token_info, endpoint, "groq", raw_input,
            {"tool": "job_match", "job_description_length": len(raw_input)},
            tokens_used,
        )

        return {
            "tool": "job_match",
            "provider": "groq",
            "model": self._groq.model,
            "match_report": answer_trunc,
            "tokens_used": tokens_used,
            "context_used": {
                "skills": len(skill_rows),
                "technologies": len(tech_rows),
                "career_stages": len(stage_rows),
            },
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_hub: Optional[IntelligenceHub] = None


def get_hub() -> IntelligenceHub:
    """Return the module-level IntelligenceHub singleton (lazy-initialised)."""
    global _hub
    if _hub is None:
        _hub = IntelligenceHub()
    return _hub
