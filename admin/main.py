# Admin Backend Main Application
# Purpose: Initialize and configure the FastAPI admin backend
# Main functions: create_admin_app()
# Dependent files: admin/routers/admin.py, admin/dependencies/access_control.py

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os

logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    username: str
    password: str


def create_admin_app() -> FastAPI:
    """Create and configure the FastAPI admin application."""
    logger.info("Initializing admin backend application")

    app = FastAPI(
        title="MCP Admin Backend",
        description="Administrative interface for MCP server",
        version="1.0.0",
        docs_url="/admin/docs",
        redoc_url="/admin/redoc",
        openapi_url="/admin/openapi.json",
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

    # --- Login endpoint (on app root, not behind auth) ---

    @app.post("/admin/login")
    async def admin_login(body: LoginRequest):
        """
        Authenticate with admin credentials and receive a JWT.
        Set ADMIN_USERNAME and ADMIN_PASSWORD env vars to enable.
        """
        from admin.dependencies.access_control import (
            authenticate_admin,
            create_access_token,
        )

        if not authenticate_admin(body.username, body.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid admin credentials",
            )

        token = create_access_token(subject=body.username)
        return {
            "access_token": token,
            "token_type": "bearer",
        }

    # Include admin routers (all other endpoints require JWT via Depends)
    from admin.routers import admin as admin_router
    app.include_router(admin_router.router, prefix="/admin", tags=["admin"])

    logger.info("Admin backend application initialized successfully")
    return app


# Allow gunicorn to call create_admin_app() as a factory
app = create_admin_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
