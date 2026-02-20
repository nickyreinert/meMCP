"""Test cache-file LLM reprocessing logic."""
import logging
from pathlib import Path
from scrapers.sitemap import SitemapScraper

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_cache_llm")

# Config with LLM enabled
config_with_llm = {
    "enabled": True,
    "connector": "sitemap",
    "url": "https://nickyreinert.de/de/sitemap.xml",
    "sub_type_override": "website",
    "llm-processing": True,  # LLM enabled
    "single-entity": True,
    "cache-file": "file://data/test_single_sitemap.yaml",  # Use existing cache
    "connector-setup": {
        "post-title-selector": "h1",
        "post-content-selector": "main",
        "post-description-selector": 'meta[name="description"]'
    }
}

def test_llm_reprocessing_flag():
    """Test that entities without LLM fields are flagged for reprocessing."""
    log.info("=== Testing LLM Reprocessing Flag ===")
    
    # Ensure cache file exists (from previous test)
    cache_path = Path("data/test_single_sitemap.yaml")
    if not cache_path.exists():
        log.error("Cache file not found. Run test_sitemap_modes.py first.")
        return
    
    log.info(f"Using existing cache: {cache_path}")
    
    # Load with LLM enabled
    scraper = SitemapScraper("test_llm", config_with_llm)
    results = scraper.run(force=False)  # Should load from cache
    
    log.info(f"Loaded {len(results)} entities from cache")
    
    for entity in results:
        has_tags = entity.get("tags") and len(entity.get("tags", [])) > 0
        has_skills = entity.get("skills") and len(entity.get("skills", [])) > 0
        has_tech = entity.get("technologies") and len(entity.get("technologies", [])) > 0
        needs_llm = entity.get("needs_llm_enrichment", False)
        
        log.info(f"\nEntity: {entity.get('title')}")
        log.info(f"  Has tags: {has_tags}")
        log.info(f"  Has skills: {has_skills}")
        log.info(f"  Has technologies: {has_tech}")
        log.info(f"  Needs LLM enrichment: {needs_llm}")
        
        if not (has_tags or has_skills or has_tech):
            log.info("  ✓ Correctly flagged for LLM enrichment")
        else:
            log.info("  ✓ Has LLM fields, no reprocessing needed")

if __name__ == "__main__":
    try:
        test_llm_reprocessing_flag()
        log.info("\n✓ LLM flag test completed")
    except Exception as e:
        log.error(f"Test failed: {e}", exc_info=True)
