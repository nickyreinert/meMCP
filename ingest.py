import argparse
import yaml
import logging
import sys
import warnings
from pathlib import Path
from scrapers.base import ScraperFactory
from scrapers.seeder import Seeder
from llm.enricher import LLMEnricher # Assuming this exists or mocked if not used yet

# Suppress SSL warnings
warnings.filterwarnings('ignore', message='.*NotOpenSSLWarning.*')
warnings.filterwarnings('ignore', category=Warning, module='urllib3')

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("mcp.ingest")

def load_config(path: str = None):
    from config_loader import load_config as _load
    if path and path != "config.yaml":
        # explicit legacy path — fall back to direct load
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    return _load()

def main():
    parser = argparse.ArgumentParser(description="Ingest data from configured sources.")
    parser.add_argument("--force", action="store_true",
                        help="Force re-fetch of all content, ignoring cache.")
    parser.add_argument("--source", type=str,
                        help="Run only specific source (by name).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch but don't save to DB.")

    # NEW FLAGS for two-step processing
    parser.add_argument("--disable-llm", action="store_true",
                        help="Fetch and seed raw entities without LLM processing.")
    parser.add_argument("--llm-only", action="store_true",
                        help="Skip fetching, only process entities with LLM (incremental).")
    parser.add_argument("--item", type=str,
                        help="Process only a specific entity by ID (use with --llm-only).")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Number of entities to process in LLM batch mode (default: 50).")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of items to process per source (0 = no limit, for testing).")
    
    # YAML update flags (manual editing workflow)
    parser.add_argument("--yaml-update", action="store_true",
                        help="Update entities from manually edited YAML file.")
    parser.add_argument("--file", type=str,
                        help="Path to YAML file for --yaml-update (e.g., data/medium_export.yaml or linkedin_profile.pdf.yaml).")
    parser.add_argument("--id", type=str,
                        help="Update only specific entity by ID (use with --yaml-update).")
    parser.add_argument("--export-yaml", action="store_true",
                        help="Export entities to YAML files after ingestion.")

    args = parser.parse_args()
    config = load_config()
    db_path = Path(config.get("db_path", "db/profile.db"))

    # Initialize DB (runs migrations)
    from db.models import init_db
    init_db(db_path)

    # Initialize LLM (if configured and not disabled)
    llm_cfg = config.get("llm", {})
    enricher = None
    if not args.disable_llm:
        try:
            enricher = LLMEnricher(llm_cfg)
        except Exception:
            log.warning("LLM Enricher not initialized (missing dependencies or config). Proceeding without enrichment.")

    seeder = Seeder(llm=enricher, db_path=db_path, config=config)

    # MODE 0: YAML update (manual editing workflow)
    if args.yaml_update:
        if not args.file:
            log.error("--yaml-update requires --file argument")
            sys.exit(1)
        
        yaml_path = Path(args.file)
        if not yaml_path.exists():
            log.error(f"YAML file not found: {yaml_path}")
            sys.exit(1)
        
        log.info(f"YAML update mode: {yaml_path}")
        if args.id:
            log.info(f"  Updating entity: {args.id}")
        else:
            log.info("  Updating all entities in file")
        
        count = seeder.update_from_yaml(
            yaml_path=yaml_path,
            entity_id=args.id,
            enrich_llm=not args.disable_llm
        )
        
        log.info(f"✓ Updated {count} entities from {yaml_path}")
        return

    # MODE 1: LLM-only processing (skip fetching)
    if args.llm_only:
        log.info("LLM-only mode: processing entities...")

        # Determine YAML path for the source
        yaml_path = None
        if args.source:
            source_cfg = config.get("oeuvre", {}).get(args.source, {})
            if source_cfg:
                # For oeuvre sources like medium_raw
                html_path = source_cfg.get("html_path")
                if html_path:
                    yaml_path = Path(str(html_path) + ".yaml")
            else:
                # Check if it's a stages source (linkedin)
                stages_cfg = config.get("stages", {})
                if stages_cfg.get("source_type") == "linkedin_pdf":
                    source_path = Path(stages_cfg.get("source_path", "linkedin_profile.pdf"))
                    yaml_path = Path(str(source_path) + ".yaml")

        if args.item:
            # Single entity processing
            log.info(f"Processing single entity: {args.item}")
            success = seeder.enrich_entity(args.item, force=True, yaml_path=yaml_path)
            if success:
                log.info(f"Successfully enriched entity {args.item}")
            else:
                log.error(f"Failed to enrich entity {args.item}")
        else:
            # Batch processing
            if yaml_path:
                log.info(f"Will update YAML cache: {yaml_path}")
            count = seeder.enrich_all(
                source=args.source,
                batch_size=args.batch_size,
                yaml_path=yaml_path
            )
            log.info(f"Enriched {count} entities")

        return

    # MODE 2: Normal fetch + seed (with optional LLM)
    all_items = []
    scrapers_with_yaml = []  # Track (scraper, items) for YAML sync

    # ── IDENTITY INGESTION ──────────────────────────────────────────
    identity_cfg = config.get("identity", {})
    if identity_cfg and not args.llm_only:
        if args.source and args.source != "identity":
            # Skip if source filter doesn't match
            pass
        else:
            log.info("Running identity source")
            # Create identity config for ScraperFactory
            identity_scraper_cfg = {
                "enabled": True,
                "connector": "identity",
                "source": identity_cfg.get("source", "data/identity.yaml")
            }
            scraper = ScraperFactory.create("identity", identity_scraper_cfg, db_path=db_path, llm=enricher)
            if scraper:
                try:
                    items = scraper.run(force=args.force)
                    log.info(f"  Fetched {len(items)} items from identity")
                    all_items.extend(items)
                except Exception as e:
                    log.error(f"  Failed identity source: {e}")

    # ── STAGES INGESTION (now uses connector architecture) ──────────
    stages_cfg = config.get("stages", {})
    if stages_cfg and not args.llm_only:
        if args.source and args.source != "stages":
            # Skip if source filter doesn't match
            pass
        elif not stages_cfg.get("enabled", True):
            log.info("Stages processing disabled in config")
        else:
            log.info("Running stages source")
            scraper = ScraperFactory.create("stages", stages_cfg, db_path=db_path, llm=enricher)
            if scraper:
                try:
                    items = scraper.run(force=args.force)
                    log.info(f"  Fetched {len(items)} items from stages")
                    all_items.extend(items)
                    
                    # Track scrapers that have YAML caches for later sync
                    if hasattr(scraper, 'yaml_cache_path') and scraper.yaml_cache_path:
                        scrapers_with_yaml.append((scraper, items))
                except Exception as e:
                    log.error(f"  Failed stages source: {e}")

    # ── OEUVRE INGESTION ────────────────────────────────────────────
    oeuvre = config.get("oeuvre", {})
    
    for name, cfg in oeuvre.items():
        if args.source and name != args.source:
            continue

        if not cfg.get("enabled", True):
            continue

        log.info(f"Running oeuvre source: {name}")
        
        # Apply limit override if specified
        if args.limit > 0:
            cfg = dict(cfg)  # Make a copy
            cfg['limit'] = args.limit
            log.info(f"  Limiting to {args.limit} items")
        
        scraper = ScraperFactory.create(name, cfg, db_path=db_path, llm=enricher)
        if not scraper:
            continue

        try:
            items = scraper.run(force=args.force)
            log.info(f"  Fetched {len(items)} items from {name}")
            all_items.extend(items)
            
            # Track scrapers that have YAML caches for later sync
            if hasattr(scraper, 'yaml_cache_path') and scraper.yaml_cache_path:
                scrapers_with_yaml.append((scraper, items))
        except Exception as e:
            log.error(f"  Failed oeuvre source {name}: {e}")

    log.info(f"Total items fetched: {len(all_items)}")

    if not args.dry_run and all_items:
        owner_cfg = config.get("identity", {}).get("en", {})

        # Seed with or without LLM based on --disable-llm flag
        enrich_llm = not args.disable_llm
        entity_id_map = seeder.seed_all(all_items, owner_cfg, enrich_llm=enrich_llm)
        
        # Update YAML caches with entity_ids and LLM enrichment (if applicable)
        for scraper, items in scrapers_with_yaml:
            from scrapers.yaml_sync import update_yaml_after_db_insert, update_yaml_after_llm
            yaml_path = scraper.yaml_cache_path
            log.info(f"Updating YAML cache: {yaml_path}")
            
            # Build url -> entity_id mapping for this scraper's items
            url_to_entity_id = {}
            for item in items:
                url = item.get("url")
                key = url or item.get("title")
                if url and key and key in entity_id_map:
                    url_to_entity_id[url] = entity_id_map[key]
            
            # Update entity_ids first
            if url_to_entity_id:
                success = update_yaml_after_db_insert(yaml_path, url_to_entity_id)
                if success:
                    log.info(f"  ✓ Updated {len(url_to_entity_id)} entity_ids in {yaml_path}")
            
            # Update LLM enrichment fields if LLM was enabled
            if enrich_llm and url_to_entity_id:
                from db.models import get_db
                conn = get_db(db_path)
                entity_id_to_enrichment = {}
                
                for url, entity_id in url_to_entity_id.items():
                    # Get entity enrichment data
                    row = conn.execute(
                        """SELECT description, llm_enriched, llm_model, llm_enriched_at 
                           FROM entities WHERE id=?""",
                        (entity_id,)
                    ).fetchone()
                    
                    if row and row['llm_enriched']:
                        # Get tags
                        tags_rows = conn.execute(
                            """SELECT tag, tag_type FROM tags WHERE entity_id=?""",
                            (entity_id,)
                        ).fetchall()
                        
                        technologies = [r['tag'] for r in tags_rows if r['tag_type'] == 'technology']
                        skills = [r['tag'] for r in tags_rows if r['tag_type'] == 'skill']
                        generic_tags = [r['tag'] for r in tags_rows if r['tag_type'] == 'generic']
                        
                        entity_id_to_enrichment[entity_id] = {
                            'description': row['description'],
                            'technologies': technologies,
                            'skills': skills,
                            'tags': generic_tags,
                            'llm_model': row['llm_model'],
                            'llm_enriched_at': row['llm_enriched_at']
                        }
                
                conn.close()
                
                if entity_id_to_enrichment:
                    success = update_yaml_after_llm(yaml_path, entity_id_to_enrichment)
                    if success:
                        log.info(f"  ✓ Updated {len(entity_id_to_enrichment)} LLM enrichments in {yaml_path}")

        if args.disable_llm:
            log.info("Raw entities seeded. Run with --llm-only to enrich.")
        
        # ── YAML EXPORT (optional, for manual editing workflow) ─────────────
        # Note: Stages are auto-exported to <pdf_path>.yaml cache during parsing
        # Sources with YAML sync (medium_raw, linkedin_pdf) don't need separate export
        # Only export oeuvre sources that don't have their own YAML caches
        if args.export_yaml or config.get("auto_export_yaml", False):
            # Get sources that already have YAML caches (use new sync system)
            sources_with_yaml_cache = {scraper.name for scraper, _ in scrapers_with_yaml}
            
            log.info("Exporting oeuvre entities to YAML...")
            from scrapers.yaml_exporter import export_to_yaml
            
            # Export Medium/oeuvre entities by source
            for source_name, source_cfg in config.get("oeuvre", {}).items():
                # Skip sources not matching --source filter
                if args.source and source_name != args.source:
                    continue
                
                if not source_cfg.get("enabled", True):
                    continue
                
                # Skip sources that already use YAML sync (they have their own .yaml cache)
                if source_name in sources_with_yaml_cache:
                    log.debug(f"Skipping export for {source_name} (uses YAML sync)")
                    continue
                
                yaml_file = Path(f"data/{source_name}_export.yaml")
                count = export_to_yaml(
                    db_path=db_path,
                    output_path=yaml_file,
                    source=source_name,
                    entity_types=["side_project", "literature"]
                )
                if count > 0:
                    log.info(f"  ✓ Exported {count} oeuvre items to {yaml_file}")
        
        # ── METRICS CALCULATION ───────────────────────────────────────────
        # Calculate metrics for all tags (skills, technologies, generic tags)
        # after all data has been ingested
        metrics_cfg = config.get("metrics", {})
        if metrics_cfg.get("enabled", True) and not args.dry_run:
            log.info("Calculating tag metrics...")
            try:
                from metrics.calculator import calculate_all_metrics
                from db.models import get_db
                
                conn = get_db(db_path)
                count = calculate_all_metrics(conn)
                conn.close()
                
                log.info(f"  ✓ Calculated metrics for {count} tags")
            except Exception as e:
                log.warning(f"Metrics calculation failed: {e}")

if __name__ == "__main__":
    main()
