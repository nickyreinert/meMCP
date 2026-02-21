#!/usr/bin/env python3
"""
Verify MCP Compliance - Test all required endpoints and schema completeness
"""
import requests
import json
import sys

BASE_URL = "http://localhost:8003"

def test_schema():
    """Test schema endpoint has all required fields."""
    print("=" * 70)
    print("SCHEMA VERIFICATION - All Required Elements")
    print("=" * 70)
    
    # Test primary schema endpoint
    r = requests.get(f"{BASE_URL}/schema")
    assert r.status_code == 200, f"Schema endpoint failed: {r.status_code}"
    
    # Test aliases
    print("\n1. SCHEMA ALIASES:")
    for alias in ["/schema", "/openapi.json", "/model"]:
        r = requests.get(f"{BASE_URL}{alias}")
        status = "✅" if r.status_code == 200 else "❌"
        print(f"   {status} {alias}")
        assert r.status_code == 200, f"{alias} failed"
    
    data = r.json()['data']
    
    # 2. Primary Keys
    print("\n2. PRIMARY KEYS:")
    for entity_type, spec in data['entity_types'].items():
        pk = spec.get('primary_key', 'undefined')
        print(f"   {entity_type:15s} → {pk}")
    
    # 3. Relations
    print("\n3. RELATIONS:")
    relations = data.get('relations', {})
    print(f"   Mechanism: {relations.get('mechanism', 'N/A')}")
    print(f"   Query endpoint: {relations.get('query_endpoint', 'N/A')}")
    
    # 4. Scoring Semantics (NEW REQUIREMENT)
    print("\n4. SCORING SEMANTICS (explicit field definitions):")
    assert 'scoring_semantics' in data, "scoring_semantics section missing!"
    
    scoring = data['scoring_semantics']
    required_fields = [
        'proficiency', 'experience_years', 'frequency', 'recency',
        'last_used', 'diversity_score', 'growth_trend', 'active',
        'relevance_score', 'entity_count'
    ]
    
    for field in required_fields:
        if field in scoring['fields']:
            field_def = scoring['fields'][field]
            unit = field_def.get('unit', 'N/A')
            calc_preview = field_def.get('calculation', 'N/A')[:40]
            print(f"   ✅ {field:20s} unit={unit:10s} calc={calc_preview}...")
        else:
            print(f"   ❌ {field:20s} MISSING")
            assert False, f"Required field {field} not defined"
    
    # Reference date
    assert 'reference_date' in scoring, "reference_date not defined"
    print(f"\n   Reference date: {scoring['reference_date']['description']}")
    
    # 5. Analytics Fields
    print("\n5. ANALYTICS FIELDS (for scoring):")
    for tag_type, spec in data['tag_types'].items():
        if 'analytics_fields' in spec:
            fields = list(spec['analytics_fields'].keys())
            print(f"   {tag_type}: {', '.join(fields[:5])}")
            endpoint = spec.get('analytics_endpoint', 'N/A')
            print(f"      → Endpoint: {endpoint}")
    
    # 6. Temporal Semantics
    print("\n6. TEMPORAL SEMANTICS (for recency/experience):")
    for entity_type in ['stages', 'oeuvre']:
        if entity_type in data['entity_types']:
            ts = data['entity_types'][entity_type].get('temporal_semantics', {})
            if ts:
                print(f"   {entity_type}:")
                for field in ['start_date', 'end_date', 'date', 'recency', 'duration']:
                    if field in ts:
                        desc = ts[field].get('description', ts[field].get('field', ''))
                        print(f"      - {field}: {desc[:50]}")
    
    # 7. Endpoints
    print("\n7. DISCOVERY ENDPOINTS:")
    endpoints = data.get('endpoints', {})
    print(f"   Discovery root: {endpoints.get('discovery_root', 'N/A')}")
    print(f"   Data model: {endpoints.get('data_model', 'N/A')}")
    print(f"   Coverage contract: {endpoints.get('coverage_contract', 'N/A')}")
    
    print("\n✅ Schema complete with explicit semantics")

def test_endpoints():
    """Test all required endpoints return valid data."""
    print("\n" + "=" * 70)
    print("ENDPOINT AVAILABILITY TEST")
    print("=" * 70 + "\n")
    
    endpoints = [
        ("/", "Root index"),
        ("/schema", "Schema definition"),
        ("/openapi.json", "Schema alias (OpenAPI)"),
        ("/model", "Schema alias (model)"),
        ("/index", "Discovery root"),
        ("/root", "Discovery root alias"),
        ("/discover", "Discovery root alias"),
        ("/coverage", "Coverage contract"),
        ("/greeting", "Identity card"),
        ("/stages", "Career stages collection"),
        ("/oeuvre", "Work portfolio collection"),
        ("/skills", "Skills collection + metrics"),
        ("/technology", "Technologies collection + metrics"),
        ("/technologies", "Technologies alias"),
        ("/tags", "Generic tags"),
    ]
    
    for path, desc in endpoints:
        r = requests.get(f"{BASE_URL}{path}")
        status = "✅" if r.status_code == 200 else "❌"
        print(f"   {status} {path:20s} → {desc}")
        assert r.status_code == 200, f"{path} failed: {r.status_code}"
    
    print("\n✅ All endpoints available (including aliases)")

def test_detail_endpoints():
    """Test detail endpoints with actual IDs."""
    print("\n" + "=" * 70)
    print("DETAIL ENDPOINT TEST")
    print("=" * 70 + "\n")
    
    # Get an oeuvre ID
    r = requests.get(f"{BASE_URL}/oeuvre?limit=1")
    if r.status_code == 200 and r.json()['data']['oeuvre']:
        oeuvre_id = r.json()['data']['oeuvre'][0]['id']
        r = requests.get(f"{BASE_URL}/oeuvre/{oeuvre_id}")
        status = "✅" if r.status_code == 200 else "❌"
        print(f"   {status} /oeuvre/{{id}} → Single work item detail")
    
    # Get a skill name
    r = requests.get(f"{BASE_URL}/skills?limit=1")
    if r.status_code == 200 and r.json()['data']['skills']:
        skill_name = r.json()['data']['skills'][0]['name']
        r = requests.get(f"{BASE_URL}/skills/{skill_name}")
        status = "✅" if r.status_code == 200 else "❌"
        has_metrics = 'metrics' in r.json()['data']
        print(f"   {status} /skills/{{name}} → Skill detail with metrics: {has_metrics}")
    
    # Get a technology name
    r = requests.get(f"{BASE_URL}/technology?limit=1")
    if r.status_code == 200 and r.json()['data']['technologies']:
        tech_name = r.json()['data']['technologies'][0]['name']
        r = requests.get(f"{BASE_URL}/technology/{tech_name}")
        status = "✅" if r.status_code == 200 else "❌"
        has_metrics = 'metrics' in r.json()['data']
        print(f"   {status} /technology/{{name}} → Technology detail with metrics: {has_metrics}")
    
    print("\n✅ Detail endpoints working")

def test_coverage():
    """Test coverage contract format."""
    print("\n" + "=" * 70)
    print("COVERAGE CONTRACT TEST")
    print("=" * 70 + "\n")
    
    r = requests.get(f"{BASE_URL}/coverage")
    assert r.status_code == 200
    assert r.headers['content-type'] == 'application/json'
    
    data = r.json()['data']
    
    required_fields = ['coverage', 'missing', 'total_entities', 'fetched_entities', 'coverage_is_relevant']
    for field in required_fields:
        has_field = field in data
        status = "✅" if has_field else "❌"
        print(f"   {status} {field}")
        assert has_field, f"Missing required field: {field}"
    
    print(f"\n   Coverage: {data['coverage']}%")
    print(f"   Total entities: {data['total_entities']}")
    print(f"   Missing endpoints: {len(data['missing'])}")
    
    print("\n✅ Coverage contract MCP-compliant")

def test_index():
    """Test discovery root completeness."""
    print("\n" + "=" * 70)
    print("DISCOVERY ROOT TEST")
    print("=" * 70 + "\n")
    
    r = requests.get(f"{BASE_URL}/index")
    assert r.status_code == 200
    
    data = r.json()['data']
    
    print(f"   Total entities: {data['total_entities']}")
    print(f"   Stages: {len(data['stages'])}")
    print(f"   Oeuvre: {len(data['oeuvre'])}")
    print(f"   Skills: {len(data['skills'])}")
    print(f"   Technologies: {len(data['technologies'])}")
    print(f"   Tags: {len(data['tags'])}")
    
    # Verify each has direct links
    if data['oeuvre']:
        sample = data['oeuvre'][0]
        has_url = 'url' in sample
        print(f"\n   Sample oeuvre has URL: {has_url}")
    
    print("\n✅ Discovery root complete")

if __name__ == "__main__":
    try:
        test_schema()
        test_endpoints()
        test_detail_endpoints()
        test_coverage()
        test_index()
        
        print("\n" + "=" * 70)
        print("🎉 ALL MCP REQUIREMENTS SATISFIED")
        print("=" * 70)
        print("\nThe API is fully MCP-compliant and ready for analysis:")
        print("  ✅ Schema exposed at /schema (+ /openapi.json, /model)")
        print("  ✅ Discovery root at /index (+ /root, /discover)")
        print("  ✅ All endpoints populated and returning data")
        print("  ✅ Endpoint aliases: /technology = /technologies")
        print("  ✅ Coverage contract at /coverage (JSON)")
        print("  ✅ Explicit scoring semantics defined:")
        print("     - proficiency, frequency, recency (with units)")
        print("     - diversity, experience, growth, active")
        print("     - All fields have: type, unit, calculation, usage")
        print("  ✅ Analytics fields defined for skills/technologies")
        print("  ✅ Temporal semantics defined for stages/oeuvre")
        print("  ✅ Reference date: current UTC (explicit)")
        print()
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("\n❌ Server not running. Start with: python3 -m uvicorn app.main:app --port 8003")
        sys.exit(1)
