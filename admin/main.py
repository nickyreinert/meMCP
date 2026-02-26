# Admin Backend Main Application
# Purpose: Initialize and configure the FastAPI admin backend
# Main functions: create_admin_app()
# Dependent files: admin/routers/admin.py, admin/dependencies/access_control.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import os

logger = logging.getLogger(__name__)


def create_admin_app() -> FastAPI:
    """Create and configure the FastAPI admin application."""
    logger.info("Initializing admin backend application")

    app = FastAPI(
        title="MCP Admin Backend",
        description="Administrative interface for MCP server",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # CORS — configurable via env var (comma-separated origins)
    cors_origins_str = os.environ.get("ADMIN_CORS_ORIGINS", "*")
    cors_origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include admin routers at root (runs on its own subdomain, no prefix needed)
    from admin.routers import admin as admin_router
    app.include_router(admin_router.router, tags=["admin"])

    logger.info("Admin backend application initialized successfully")
    return app


# Module-level app for gunicorn: `gunicorn admin.main:app`
app = create_admin_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
