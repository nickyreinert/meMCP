"""
llm/enricher.py — LLM Description Enrichment
==============================================
Takes raw scraped text and produces clean, concise entity descriptions.
Supports GROQ (fast, free tier) and local Ollama (fully private).
Falls back gracefully to raw text if neither is configured.

Also provides: tag suggestion, entity type classification.
"""

import os
import time
import logging
from typing import Optional

from llm.prompts import (
    DESCRIPTION_SYSTEM,
    TAG_SYSTEM,
    TYPE_SYSTEM,
    TRANSLATION_SYSTEM,
    GREETING_SYSTEM,
)

log = logging.getLogger("mcp.llm")

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

import requests


class LLMEnricher:

    def __init__(self, cfg: dict):
        self.backend   = cfg.get("backend", "none")
        self.model     = cfg.get("model", "llama3-8b-8192")
        self.ollama_url = cfg.get("ollama_url", "http://localhost:11434")
        self._groq: Optional[object] = None
        self._call_count = 0
        self._error_count = 0

        if self.backend == "groq":
            if not GROQ_AVAILABLE:
                log.warning("groq package not installed → pip install groq. Disabling LLM.")
                self.backend = "none"
            else:
                api_key = cfg.get("groq_api_key") or os.environ.get("GROQ_API_KEY", "")
                if not api_key:
                    log.warning("No GROQ_API_KEY found. Disabling LLM enrichment.")
                    self.backend = "none"
                else:
                    self._groq = Groq(api_key=api_key)
                    log.info(f"LLM: GROQ backend ready ({self.model})")

        elif self.backend == "ollama":
            log.info(f"LLM: Ollama backend ({self.ollama_url}, model={self.model})")

        else:
            log.info("LLM: disabled (backend=none)")

    # ── Public methods ────────────────────────────────────────────────────────

    def enrich_description(self, raw_text: str, context: str = "") -> str:
        """Turn raw scraped text into a clean description."""
        if not self._ready() or not raw_text.strip():
            return raw_text.strip()[:500]
        prompt = f"Context: {context}\n\nRaw text:\n{raw_text[:1200]}"
        return self._call(DESCRIPTION_SYSTEM, prompt, max_tokens=100) or raw_text.strip()[:500]

    def suggest_tags(self, text: str) -> list[str]:
        """Return a list of suggested tags for the given text."""
        if not self._ready() or not text.strip():
            return []
        result = self._call(TAG_SYSTEM, text[:800], max_tokens=60)
        if not result:
            return []
        return [t.strip() for t in result.split(",") if t.strip()]

    def classify_type(self, text: str) -> Optional[str]:
        """Guess the entity type from raw text."""
        valid = {"professional","company","education","institution",
                 "side_project","literature","technology","skill","achievement","event"}
        if not self._ready():
            return None
        result = self._call(TYPE_SYSTEM, text[:500], max_tokens=10)
        if result and result.strip() in valid:
            return result.strip()
        return None

    def stats(self) -> dict:
        return {
            "backend": self.backend,
            "model":   self.model,
            "calls":   self._call_count,
            "errors":  self._error_count,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _ready(self) -> bool:
        return self.backend in ("groq", "ollama")

    def _call(self, system: str, user: str, max_tokens: int = 100,
              retries: int = 2) -> Optional[str]:
        self._call_count += 1
        for attempt in range(retries + 1):
            try:
                if self.backend == "groq":
                    return self._groq_call(system, user, max_tokens)
                elif self.backend == "ollama":
                    return self._ollama_call(system, user, max_tokens)
            except Exception as e:
                self._error_count += 1
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    log.warning(f"LLM attempt {attempt+1} failed: {e} — retrying")
                else:
                    log.error(f"LLM failed after {retries+1} attempts: {e}")
        return None

    def _groq_call(self, system: str, user: str, max_tokens: int) -> str:
        resp = self._groq.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()

    def _ollama_call(self, system: str, user: str, max_tokens: int) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.2},
        }
        r = requests.post(
            f"{self.ollama_url}/api/chat",
            json=payload,
            timeout=90,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()


# ─────────────────────────────────────────────────────────────────────────────
# TRANSLATION  (added for multi-language support)
# ─────────────────────────────────────────────────────────────────────────────

LANG_NAMES = {
    "en": "English",
    "de": "German (Deutsch)",
    "fr": "French (Français)",
    "es": "Spanish (Español)",
}


class TranslationMixin:
    """
    Mixed into LLMEnricher to add translation capabilities.
    All translation calls go through the same GROQ/Ollama backend.
    """

    def translate(self, text: str, target_lang: str,
                  context: str = "description",
                  source_lang: str = "en") -> Optional[str]:
        """
        Translate `text` into `target_lang`.
        Returns None if LLM not available or text is empty.
        If source == target, returns text unchanged immediately.
        """
        if not text or not text.strip():
            return None
        if source_lang == target_lang:
            return text.strip()
        if not self._ready():
            return None

        lang_name = LANG_NAMES.get(target_lang, target_lang)
        system = (
            GREETING_SYSTEM if context == "greeting"
            else TRANSLATION_SYSTEM
        ).format(target_lang=lang_name)

        result = self._call(system, text.strip()[:1500],
                            max_tokens=300, retries=2)
        if result:
            log.debug(f"Translated [{source_lang}→{target_lang}] {text[:40]!r}…")
        return result

    def translate_entity(self, entity: dict,
                         target_lang: str) -> tuple[Optional[str], Optional[str]]:
        """
        Translate both title and description of an entity dict.
        Returns (translated_title, translated_description).
        Skips translation for technology entities (names are universal).
        """
        if entity.get("type") in ("technology", "person"):
            return None, None

        source_lang = entity.get("language", "en") or "en"

        translated_title = self.translate(
            entity.get("title", ""),
            target_lang=target_lang,
            source_lang=source_lang,
            context="title",
        )
        translated_desc = self.translate(
            entity.get("description", ""),
            target_lang=target_lang,
            source_lang=source_lang,
            context="description",
        )
        return translated_title, translated_desc

    def translate_greeting(self, greeting_data: dict,
                           target_lang: str) -> dict:
        """
        Translate the static greeting/identity fields.
        Input: {"tagline": ..., "short": ..., "greeting": ...}
        Returns dict with same keys, translated.
        """
        result = {}
        for key in ("tagline", "short", "greeting"):
            text = greeting_data.get(key, "")
            if text:
                translated = self.translate(
                    text, target_lang=target_lang,
                    context="greeting", source_lang="en"
                )
                result[key] = translated or text
            else:
                result[key] = text
        return result


# Inject mixin into LLMEnricher retroactively (clean inheritance alternative)
# FIXME: This causes TypeError in some Python versions, commented out for now
# LLMEnricher.__bases__ = (TranslationMixin,) + LLMEnricher.__bases__
