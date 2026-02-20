"""Test sitemap scraper in both single-entity and multi-entity modes."""
import logging
from pathlib import Path
from scrapers.sitemap import SitemapScraper

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_sitemap")

# Test config for multi-entity mode
config_multi = {
    "enabled": True,
    "connector": "sitemap",
    "url": "https://nickyreinert.de/de/sitemap.xml",
    "sub_type_override": "blog_post",
    "llm-processing": False,
    "limit": 3,  # Only process 3 URLs for testing
    "single-entity": False,
    "cache-file": "file://data/test_multi_sitemap.yaml",
    "connector-setup": {
        "post-title-selector": "h1",
        "post-content-selector": "article",
        "post-published-date-selector": "time[datetime]",
        "post-description-selector": 'meta[name="description"]'
    }
}

# Test config for single-entity mode
config_single = {
    "enabled": True,
    "connector": "sitemap",
    "url": "https://nickyreinert.de/de/sitemap.xml",
    "sub_type_override": "website",
    "llm-processing": False,
    "single-entity": True,
    "cache-file": "file://data/test_single_sitemap.yaml",
    "connector-setup": {
        "post-title-selector": "h1",
        "post-content-selector": "main",
        "post-description-selector": 'meta[name="description"]'
    }
}

def test_multi_entity_mode():
    """Test multi-entity mode: each page as separate entity."""
    log.info("=== Testing Multi-Entity Mode ===")
    
    # Clean up cache file if exists
    cache_path = Path("data/test_multi_sitemap.yaml")
    if cache_path.exists():
        cache_path.unlink()
        log.info("Cleaned up existing cache file")
    
    scraper = SitemapScraper("test_multi", config_multi)
    results = scraper.run(force=True)
    
    log.info(f"Found {len(results)} entities")
    for i, entity in enumerate(results, 1):
        log.info(f"Entity {i}:")
        log.info(f"  Title: {entity.get('title')}")
        log.info(f"  URL: {entity.get('url')}")
        log.info(f"  Description: {entity.get('description')[:100]}...")
    
    # Test cache loading
    log.info("\n--- Testing Cache Load ---")
    scraper2 = SitemapScraper("test_multi", config_multi)
    cached_results = scraper2.run(force=False)
    log.info(f"Loaded {len(cached_results)} entities from cache")
    
    return results

def test_single_entity_mode():
    """Test single-entity mode: whole site as one entity."""
    log.info("\n=== Testing Single-Entity Mode ===")
    
    # Clean up cache file if exists
    cache_path = Path("data/test_single_sitemap.yaml")
    if cache_path.exists():
        cache_path.unlink()
        log.info("Cleaned up existing cache file")
    
    scraper = SitemapScraper("test_single", config_single)
    results = scraper.run(force=True)
    
    log.info(f"Found {len(results)} entity(ies)")
    if results:
        entity = results[0]
        log.info(f"Entity:")
        log.info(f"  Title: {entity.get('title')}")
        log.info(f"  URL: {entity.get('url')}")
        log.info(f"  Description: {entity.get('description')[:100]}...")
        log.info(f"  Page Count: {entity.get('ext', {}).get('page_count')}")
        log.info(f"  Single Entity Mode: {entity.get('ext', {}).get('single_entity_mode')}")
    
    # Test cache loading
    log.info("\n--- Testing Cache Load ---")
    scraper2 = SitemapScraper("test_single", config_single)
    cached_results = scraper2.run(force=False)
    log.info(f"Loaded {len(cached_results)} entity(ies) from cache")
    
    return results

if __name__ == "__main__":
    try:
        multi_results = test_multi_entity_mode()
        single_results = test_single_entity_mode()
        
        log.info("\n=== Test Summary ===")
        log.info(f"Multi-entity mode: {len(multi_results)} entities")
        log.info(f"Single-entity mode: {len(single_results)} entity(ies)")
        log.info("✓ All tests completed")
        
    except Exception as e:
        log.error(f"Test failed: {e}", exc_info=True)
