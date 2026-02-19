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

def load_config(path: str = "config.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

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
    
    # YAML update flags (manual editing workflow)
    parser.add_argument("--yaml-update", action="store_true",
                        help="Update entities from manually edited YAML file.")
    parser.add_argument("--file", type=str,
                        help="Path to YAML file for --yaml-update (e.g., data/linkedin_export.yaml).")
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

        if args.item:
            # Single entity processing
            log.info(f"Processing single entity: {args.item}")
            success = seeder.enrich_entity(args.item, force=True)
            if success:
                log.info(f"Successfully enriched entity {args.item}")
            else:
                log.error(f"Failed to enrich entity {args.item}")
        else:
            # Batch processing
            count = seeder.enrich_all(source=args.source, batch_size=args.batch_size)
            log.info(f"Enriched {count} entities")

        return

    # MODE 2: Normal fetch + seed (with optional LLM)
    all_items = []

    #  ── STAGES INGESTION ────────────────────────────────────────────
    stages_cfg = config.get("stages", {})
    if stages_cfg and not args.llm_only:
        source_type = stages_cfg.get("source_type", "linkedin_export")
        source_path = Path(stages_cfg.get("source_path", "linkedin_export.yaml"))

        if source_type == "linkedin_export" and source_path.exists():
            log.info(f"Loading stages from LinkedIn export: {source_path}")
            from scrapers.scrapers import LinkedInParser
            linkedin_parser = LinkedInParser(export_path=source_path)
            stages_items = linkedin_parser.parse()
            all_items.extend(stages_items)
            log.info(f"  Loaded {len(stages_items)} stages entities")

        elif source_type == "linkedin_pdf" and source_path.exists():
            if args.disable_llm:
                log.warning(f"Skipping LinkedIn PDF ({source_path}) - LLM required for PDF parsing. Use linkedin_export.yaml or run without --disable-llm.")
            else:
                log.info(f"Loading stages from LinkedIn PDF: {source_path}")
                from scrapers.linkedin_pdf import LinkedInPDFParser
                pdf_parser = LinkedInPDFParser(source_path, llm_enricher=enricher)
                stages_items = pdf_parser.parse()
                all_items.extend(stages_items)
                log.info(f"  Loaded {len(stages_items)} stages entities")

        elif source_type == "manual_json" and source_path.exists():
            log.info(f"Loading stages from manual JSON: {source_path}")
            import json
            with open(source_path) as f:
                manual_data = json.load(f)
            # Parse using LinkedInParser with data dict
            from scrapers.scrapers import LinkedInParser
            parser = LinkedInParser(data=manual_data)
            stages_items = parser.parse()
            all_items.extend(stages_items)
            log.info(f"  Loaded {len(stages_items)} stages entities")

    # ── OEUVRE INGESTION ────────────────────────────────────────────
    oeuvre_sources = config.get("oeuvre_sources", {})
    for name, cfg in oeuvre_sources.items():
        if args.source and name != args.source:
            continue

        if not cfg.get("enabled", True):
            continue

        log.info(f"Running oeuvre source: {name}")
        scraper = ScraperFactory.create(name, cfg, db_path=db_path)
        if not scraper:
            continue

        try:
            items = scraper.run(force=args.force)
            log.info(f"  Fetched {len(items)} items from {name}")
            all_items.extend(items)
        except Exception as e:
            log.error(f"  Failed oeuvre source {name}: {e}")

    log.info(f"Total items fetched: {len(all_items)}")

    if not args.dry_run and all_items:
        owner_cfg = config.get("identity", {}).get("en", {})

        # Seed with or without LLM based on --disable-llm flag
        enrich_llm = not args.disable_llm
        seeder.seed_all(all_items, owner_cfg, enrich_llm=enrich_llm)

        if args.disable_llm:
            log.info("Raw entities seeded. Run with --llm-only to enrich.")
        
        # ── YAML EXPORT (optional, for manual editing workflow) ─────────────
        if args.export_yaml or config.get("auto_export_yaml", False):
            log.info("Exporting entities to YAML...")
            from scrapers.yaml_exporter import export_to_yaml
            
            # Export LinkedIn/stages entities
            stages_cfg = config.get("stages", {})
            if stages_cfg:
                source_name = stages_cfg.get("source_type", "linkedin").split("_")[0]
                yaml_file = Path(f"data/{source_name}_export.yaml")
                count = export_to_yaml(
                    db_path=db_path,
                    output_path=yaml_file,
                    source=source_name,
                    entity_types=["professional", "education", "achievement", "company", "institution"]
                )
                if count > 0:
                    log.info(f"  ✓ Exported {count} stages to {yaml_file}")
            
            # Export Medium/oeuvre entities by source
            for source_name, source_cfg in config.get("oeuvre_sources", {}).items():
                if not source_cfg.get("enabled", True):
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

if __name__ == "__main__":
    main()
