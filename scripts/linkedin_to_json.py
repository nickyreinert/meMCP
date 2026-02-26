#!/usr/bin/env python3
"""
scripts/linkedin_to_json.py — LinkedIn YAML to JSON Converter
==============================================================
Converts linkedin_export.yaml to stages.json template for manual editing.

Usage:
    python scripts/linkedin_to_json.py

This creates stages.json from linkedin_export.yaml, which can then be manually
edited and used with:
    stages.source_type = 'manual_json'
    stages.source_path = 'stages.json'
"""

import yaml
import json
from pathlib import Path


def convert_linkedin_yaml_to_json(yaml_path: Path, json_path: Path):
    """Convert LinkedIn YAML export to editable JSON template."""

    if not yaml_path.exists():
        print(f"❌ Error: {yaml_path} not found")
        print(f"   Please export your LinkedIn data first.")
        return

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    template = {
        "_comment": "Manually edit your stages. Use this with stages.source_type = 'manual_json'",
        "experience": data.get("experience", []),
        "education": data.get("education", []),
        "certifications": data.get("certifications", []),
    }

    with open(json_path, 'w') as f:
        json.dump(template, f, indent=2, ensure_ascii=False)

    print(f"✓ Converted {yaml_path} → {json_path}")
    print(f"\n  {len(template['experience'])} experience entries")
    print(f"  {len(template['education'])} education entries")
    print(f"  {len(template['certifications'])} certifications")
    print(f"\nNext steps:")
    print(f"  1. Edit {json_path} manually as needed")
    print(f"  2. Update config.content.yaml:")
    print(f"       stages:")
    print(f"         source_type: manual_json")
    print(f"         source_path: {json_path}")
    print(f"  3. Run: python ingest.py --disable-llm")


if __name__ == "__main__":
    convert_linkedin_yaml_to_json(
        Path("linkedin_export.yaml"),
        Path("stages.json")
    )
