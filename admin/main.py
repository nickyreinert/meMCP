# Admin Backend Main Application
# Purpose: Initialize and configure the FastAPI admin backend
# Main functions: create_admin_app()
# Dependent files: admin/routers/admin.py, admin/dependencies/access_control.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import logging
import os
from pathlib import Path

_STATIC = Path(__file__).parent / "static"
_MIME = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}

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

    # Serve the admin UI at /ui (no auth — login is handled client-side)
    @app.get("/ui", include_in_schema=False)
    async def ui_index():
        return Response(
            content=(_STATIC / "index.html").read_bytes(),
            media_type="text/html",
        )

    @app.get("/ui/{filename}", include_in_schema=False)
    async def ui_static(filename: str):
        safe = Path(filename).name   # strip any path traversal
        path = _STATIC / safe
        if not path.exists() or not path.is_file():
            return Response(status_code=404)
        suffix = path.suffix.lower()
        return Response(
            content=path.read_bytes(),
            media_type=_MIME.get(suffix, "application/octet-stream"),
        )

    logger.info("Admin backend application initialized successfully")
    return app


# Module-level app for gunicorn: `gunicorn admin.main:app`
app = create_admin_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
