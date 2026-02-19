"""
scrapers/stages_template_generator.py — Stages Template Generator
====================================================================
Exports parsed LinkedIn stages data to a JSON template file for manual editing.
This allows users to manually curate their stages after initial LinkedIn parsing.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def export_stages_template(stages_data: list[dict], output_path: Path):
    """
    Export parsed LinkedIn stages to JSON template for manual editing.

    Args:
        stages_data: List of stage entities (professional, education, achievement)
        output_path: Path to write the JSON template
    """
    template = {
        "_comment": "Template for manual stages definition. Edit and set stages.source_type = 'manual_json'",
        "experience": [],
        "education": [],
        "certifications": []
    }

    # Group parsed entities by type
    for item in stages_data:
        ext_data = item.get("ext", {})

        if item["type"] == "professional":
            # Reverse engineer back to LinkedIn export format
            exp_entry = {
                "company": ext_data.get("company_name") or item.get("title", "").split(" at ")[-1],
                "role": ext_data.get("role") or item.get("title", "").split(" at ")[0],
                "employment_type": ext_data.get("employment_type", "full_time"),
                "location": ext_data.get("location"),
                "remote": ext_data.get("remote", False),
                "start_date": item.get("start_date"),
                "end_date": item.get("end_date"),
                "description": item.get("description"),
                "tags": list(set(
                    item.get("tags", []) +
                    item.get("capability_tags", []) +
                    item.get("tech_tags", [])
                )),
            }
            template["experience"].append(exp_entry)

        elif item["type"] == "education":
            edu_entry = {
                "institution": ext_data.get("institution_name") or item.get("title", "").split(" - ")[0],
                "degree": ext_data.get("degree") or item.get("title", "").split(" - ")[1] if " - " in item.get("title", "") else None,
                "field": ext_data.get("field"),
                "start_date": item.get("start_date"),
                "end_date": item.get("end_date"),
                "description": item.get("description"),
                "exchange": ext_data.get("exchange", False),
            }
            template["education"].append(edu_entry)

        elif item["type"] == "achievement":
            cert_entry = {
                "title": item.get("title"),
                "issuer": ext_data.get("issuer"),
                "credential_id": ext_data.get("credential_id"),
                "credential_url": ext_data.get("credential_url") or item.get("url"),
                "date": item.get("start_date") or item.get("end_date"),
            }
            template["certifications"].append(cert_entry)

    # Write JSON with nice formatting
    output_path.write_text(json.dumps(template, indent=2, ensure_ascii=False))
    log.info(f"✓ Stages template exported to {output_path}")
    log.info(f"  {len(template['experience'])} experience entries")
    log.info(f"  {len(template['education'])} education entries")
    log.info(f"  {len(template['certifications'])} certifications")
    log.info(f"\nTo use this template:")
    log.info(f"  1. Edit {output_path} manually")
    log.info(f"  2. Set config: stages.source_type = 'manual_json'")
    log.info(f"  3. Set config: stages.source_path = '{output_path.name}'")
