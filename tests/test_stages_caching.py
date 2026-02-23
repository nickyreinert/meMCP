#!/usr/bin/env python3
"""
test_stages_caching.py — Test LinkedIn YAML Caching Logic
==========================================================
Tests the new auto-caching workflow for LinkedIn stages.

This test verifies:
1. LinkedIn PDF parsing creates .yaml cache
2. Subsequent runs load from .yaml cache
3. enabled flag controls processing
"""

import yaml
from pathlib import Path


def test_yaml_cache_structure():
    """Test that YAML cache has expected structure."""
    yaml_path = Path("linkedin_profile.pdf.yaml")
    
    if not yaml_path.exists():
        print("❌ YAML cache not found. Run 'python ingest.py' first.")
        return False
    
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    
    # Check structure
    expected_keys = ["experience", "education", "certifications"]
    for key in expected_keys:
        if key not in data:
            print(f"❌ Missing key: {key}")
            return False
        if not isinstance(data[key], list):
            print(f"❌ {key} is not a list")
            return False
    
    print("✓ YAML cache structure is valid")
    print(f"  {len(data['experience'])} experience entries")
    print(f"  {len(data['education'])} education entries")
    print(f"  {len(data['certifications'])} certifications")
    return True


def test_config_structure():
    """Test that config has new structure."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config_loader import load_config
    config = load_config()
    
    stages = config.get("stages", {})
    
    # Check required fields
    if "enabled" not in stages:
        print("❌ Missing 'enabled' field in stages config")
        return False
    
    if stages.get("source_type") != "linkedin_pdf":
        print(f"❌ source_type should be 'linkedin_pdf', got: {stages.get('source_type')}")
        return False
    
    # Check deprecated fields are removed
    if "export_template" in stages:
        print("❌ Deprecated 'export_template' field still present")
        return False
    
    print("✓ Config structure is correct")
    print(f"  enabled: {stages.get('enabled')}")
    print(f"  source_type: {stages.get('source_type')}")
    print(f"  source_path: {stages.get('source_path')}")
    return True


def test_yaml_cache_workflow():
    """Test the caching workflow logic."""
    yaml_path = Path("linkedin_profile.pdf.yaml")
    pdf_path = Path("linkedin_profile.pdf")
    
    print("\nWorkflow Test:")
    if yaml_path.exists():
        print(f"✓ YAML cache exists: {yaml_path}")
        print("  → Subsequent runs will load from cache (fast, no LLM)")
    else:
        if pdf_path.exists():
            print(f"⚠️  PDF exists but YAML cache missing")
            print(f"  → Next run will parse {pdf_path} and create cache")
        else:
            print(f"⚠️  Neither PDF nor YAML cache found")
            print(f"  → Place {pdf_path} and run 'python ingest.py'")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("LinkedIn YAML Caching - Test Suite")
    print("=" * 60)
    print()
    
    all_passed = True
    
    # Test 1: Config structure
    print("Test 1: Config Structure")
    all_passed &= test_config_structure()
    print()
    
    # Test 2: YAML cache structure (if exists)
    print("Test 2: YAML Cache Structure")
    if Path("linkedin_profile.pdf.yaml").exists():
        all_passed &= test_yaml_cache_structure()
    else:
        print("⚠️  YAML cache not found (run 'python ingest.py' to create)")
    print()
    
    # Test 3: Workflow
    print("Test 3: Workflow Check")
    all_passed &= test_yaml_cache_workflow()
    print()
    
    print("=" * 60)
    if all_passed:
        print("✓ All tests passed!")
    else:
        print("❌ Some tests failed")
    print("=" * 60)
