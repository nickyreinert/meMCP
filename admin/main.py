# Admin Backend Main Application
# Purpose: Initialize and configure the FastAPI admin backend
# Main functions: create_admin_app()
# Dependent files: admin/routers/admin.py, admin/dependencies/access_control.py

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
import logging
from typing import Optional

# Initialize logging
logger = logging.getLogger(__name__)

# --- APP INITIALIZATION ---

def create_admin_app() -> FastAPI:
    """
    Create and configure the FastAPI admin application.
    
    Purpose: Initialize the admin backend with proper configuration
    Input: None
    Output: Configured FastAPI app instance
    Process:
        1. Create FastAPI app
        2. Configure CORS
        3. Include admin routers
        4. Set up security
    Dependencies: admin.routers.admin module
    """
    logger.info("Initializing admin backend application")
    
    # Create FastAPI app
    app = FastAPI(
        title="MCP Admin Backend",
        description="Administrative interface for MCP server",
        version="0.1.0",
        docs_url="/admin/docs",
        redoc_url="/admin/redoc",
        openapi_url="/admin/openapi.json"
    )
    
    # Configure CORS - restrict to specific origins in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: Restrict in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include admin routers
    from admin.routers import admin as admin_router
    app.include_router(admin_router.router, prefix="/admin", tags=["admin"])
    
    logger.info("Admin backend application initialized successfully")
    return app

# --- MAIN EXECUTION ---

if __name__ == "__main__":
    try:
        import uvicorn
        
        logger.info("Starting admin backend server")
        app = create_admin_app()
        
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8081,
            log_level="info"
        )
    except ImportError:
        logger.error("Uvicorn not available. Please install uvicorn to run the server.")
        logger.info("Admin app can still be used programmatically by importing create_admin_app()")
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        raise