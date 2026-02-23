"""
config_loader.py — Unified configuration loader
================================================
Merges config.tech.yaml (infrastructure settings) and config.content.yaml
(personal data-source settings) into a single dict, so all existing code
can call load_config() and get the combined result transparently.

Precedence: config.content.yaml values overwrite config.tech.yaml values
on key collision (content is user-specific, tech is more generic defaults).
"""

import yaml
from pathlib import Path


def load_config(root: Path | str | None = None) -> dict:
    """
    Load and merge config.tech.yaml + config.content.yaml.

    Args:
        root: Project root directory. Defaults to the directory containing
              this file (i.e. the project root).

    Returns:
        Merged configuration dict (tech settings + content/source settings).
    """
    if root is None:
        root = Path(__file__).parent
    root = Path(root)

    merged: dict = {}
    for name in ("config.tech.yaml", "config.content.yaml"):
        path = root / name
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            merged.update(data)

    return merged
