"""
scrapers/linkedin_pdf.py — LinkedIn PDF Parser
===============================================
Parses a LinkedIn profile PDF export using LLM to extract structured data.

Usage:
    parser = LinkedInPDFParser(pdf_path, llm_enricher)
    entities = parser.parse()
"""

import logging
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from llm.prompts import format_linkedin_pdf_prompt

log = logging.getLogger("mcp.scrapers.linkedin_pdf")

try:
    from pypdf import PdfReader
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    log.warning("pypdf not installed. Run: pip install pypdf")


class LinkedInPDFParser:
    """
    Parse LinkedIn profile PDF using LLM extraction.

    The LLM extracts structured data from unstructured PDF text.
    More flexible than layout parsing but requires LLM.
    """

    def __init__(self, pdf_path: Path, llm_enricher=None):
        self.pdf_path = pdf_path
        self.llm = llm_enricher

        if not PDF_AVAILABLE:
            raise ImportError("pypdf library required. Install: pip install pypdf")

        if not self.llm:
            raise ValueError(
                "LLM enricher required for PDF parsing. "
                "PDF text extraction works without LLM, but structured data extraction needs LLM. "
                "Either provide llm_enricher or use the pre-generated <pdf_path>.yaml cache instead."
            )

    def _extract_text(self) -> str:
        """Extract all text from PDF."""
        try:
            reader = PdfReader(str(self.pdf_path))
            text_parts = []

            log.info(f"PDF has {len(reader.pages)} pages")

            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    # Clean up common PDF artifacts
                    text = text.replace('\x00', '')  # Remove null bytes
                    text_parts.append(text)
                    log.debug(f"  Page {i+1}: {len(text)} chars")

            full_text = "\n\n".join(text_parts)
            log.info(f"Extracted {len(full_text)} characters from PDF")

            # Save extracted text to file for debugging
            debug_path = self.pdf_path.with_suffix('.txt')
            debug_path.write_text(full_text, encoding='utf-8')
            log.info(f"Saved extracted text to {debug_path} for inspection")

            return full_text

        except Exception as e:
            log.error(f"Failed to extract PDF text: {e}")
            return ""

    def parse(self) -> List[Dict[str, Any]]:
        """Parse PDF using LLM to extract entities."""
        if not self.pdf_path.exists():
            log.error(f"PDF not found: {self.pdf_path}")
            return []

        # Extract raw text
        pdf_text = self._extract_text()
        if not pdf_text:
            return []

        # Use LLM to extract structured data
        log.info("Parsing PDF with LLM...")
        structured_data = self._llm_extract_entities(pdf_text)

        if not structured_data:
            return []

        # Convert to entity format
        entities = self._convert_to_entities(structured_data)
        log.info(f"Parsed {len(entities)} entities from PDF")

        return entities

    def _llm_extract_entities(self, text: str) -> Optional[dict]:
        """Use LLM to extract structured experience/education from unstructured text."""

        # Increase limit to capture more content (LinkedIn PDFs can be long)
        max_chars = 15000
        if len(text) > max_chars:
            log.info(f"PDF text is {len(text)} chars, truncating to {max_chars} for LLM")
            text = text[:max_chars]
        else:
            log.info(f"Sending {len(text)} characters to LLM")

        # Use centralized prompt
        prompt = format_linkedin_pdf_prompt(text)

        try:
            log.info("Sending PDF text to LLM for parsing...")

            # Call LLM backend
            if hasattr(self.llm, 'backend') and self.llm.backend == 'ollama':
                response = self._call_ollama(prompt)
            elif hasattr(self.llm, 'backend') and self.llm.backend == 'groq':
                response = self._call_groq(prompt)
            else:
                log.error("Unsupported LLM backend")
                return None

            log.info("LLM response received, parsing JSON...")

            # Save LLM response for debugging
            response_path = self.pdf_path.with_suffix('.llm_response.json')
            response_path.write_text(response, encoding='utf-8')
            log.info(f"Saved LLM response to {response_path} for inspection")

            # Parse JSON response
            # Remove markdown code blocks if present
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]

            data = json.loads(response.strip())
            log.info(f"Successfully parsed: {len(data.get('experience', []))} jobs, {len(data.get('education', []))} education entries, {len(data.get('certifications', []))} certifications")
            return data

        except json.JSONDecodeError as e:
            log.error(f"Failed to parse LLM JSON response: {e}")
            log.error(f"Response was: {response[:500]}")
            return None
        except Exception as e:
            log.error(f"LLM extraction failed: {e}")
            return None

    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama API with extended timeout."""
        import requests

        url = self.llm.ollama_url + "/api/generate"
        payload = {
            "model": self.llm.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1}
        }

        log.info(f"Calling Ollama with model {self.llm.model} (this may take 1-3 minutes)...")
        resp = requests.post(url, json=payload, timeout=300)  # Increased to 5 minutes
        resp.raise_for_status()
        return resp.json()["response"]

    def _call_groq(self, prompt: str) -> str:
        """Call Groq API."""
        from groq import Groq

        client = Groq(api_key=self.llm.groq_api_key)
        completion = client.chat.completions.create(
            model=self.llm.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return completion.choices[0].message.content

    def _convert_to_entities(self, data: dict) -> List[Dict[str, Any]]:
        """Convert LLM-extracted data to entity format (same as LinkedInParser)."""
        results = []

        # Experience
        for job in data.get("experience", []):
            company_title = job.get("company", "")

            # Professional entity (stages/job)
            results.append({
                "flavor": "stages",
                "category": "job",
                "title": f"{job.get('role', 'Role')} at {company_title}",
                "description": job.get("description"),
                "source": "linkedin_pdf",
                "start_date": job.get("start_date"),
                "end_date": job.get("end_date"),
                "is_current": not bool(job.get("end_date")),
                "tags": job.get("tags", []),
            })

        # Education
        for edu in data.get("education", []):
            inst_title = edu.get("institution", "")

            results.append({
                "flavor": "stages",
                "category": "education",
                "title": f"{edu.get('degree') or edu.get('title', '')} at {inst_title}",
                "description": edu.get("description"),
                "source": "linkedin_pdf",
                "start_date": edu.get("start_date"),
                "end_date": edu.get("end_date"),
                "tags": edu.get("tags", []),
            })

        # Note: Certifications/achievements skipped in simplified model

        return results
