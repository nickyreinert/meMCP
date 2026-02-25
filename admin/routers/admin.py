# Admin Router
# Purpose: Define all admin-specific API endpoints
# Main functions: setup_admin_router()
# Dependent files: admin/dependencies/access_control.py, db/models.py

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.security import OAuth2PasswordBearer
from typing import List, Optional
import logging
import os
from datetime import datetime

# Import access control functions
from admin.dependencies.access_control import (
    create_admin_token, 
    get_token_by_id, 
    revoke_token_by_id,
    list_active_tokens
)

# Initialize logging
logger = logging.getLogger(__name__)

# Security setup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="admin/token")

# --- ROUTER SETUP ---

router = APIRouter()

@router.get("/")
async def admin_root():
    """
    Admin root endpoint
    
    Purpose: Provide basic admin backend information
    Input: None
    Output: Welcome message
    Process: Return simple welcome message
    Dependencies: None
    """
    logger.info("Admin root endpoint accessed")
    return {"message": "MCP Admin Backend", "status": "active"}

# --- LOG MANAGEMENT ENDPOINTS ---

@router.get("/logs")
async def get_logs():
    """
    Get available log files
    
    Purpose: List all available log files for download
    Input: None
    Output: List of log file information
    Process:
        1. Scan logs directory
        2. Return file list with metadata
    Dependencies: None
    """
    logger.info("Fetching available log files")
    
    log_dir = "logs"
    if not os.path.exists(log_dir):
        logger.warning(f"Log directory {log_dir} does not exist")
        return {"logs": []}
    
    try:
        files = []
        for filename in os.listdir(log_dir):
            filepath = os.path.join(log_dir, filename)
            if os.path.isfile(filepath):
                stat = os.stat(filepath)
                files.append({
                    "name": filename,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
        
        logger.info(f"Found {len(files)} log files")
        return {"logs": files}
    except Exception as e:
        logger.error(f"Error reading log directory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read log files"
        )

@router.get("/logs/{filename}")
async def download_log(filename: str):
    """
    Download specific log file
    
    Purpose: Download the contents of a specific log file
    Input: filename - name of the log file to download
    Output: File content
    Process:
        1. Validate file exists
        2. Return file content
    Dependencies: None
    """
    logger.info(f"Downloading log file: {filename}")
    
    log_dir = "logs"
    filepath = os.path.join(log_dir, filename)
    
    if not os.path.exists(filepath):
        logger.warning(f"Log file {filename} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Log file not found"
        )
    
    try:
        with open(filepath, "r") as f:
            content = f.read()
        
        logger.info(f"Successfully read log file: {filename}")
        return {"filename": filename, "content": content}
    except Exception as e:
        logger.error(f"Error reading log file {filename}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read log file"
        )

# --- TOKEN MANAGEMENT ENDPOINTS ---

@router.post("/tokens")
async def create_token():
    """
    Create new access token
    
    Purpose: Generate a new access token for API access
    Input: None
    Output: Newly created token with metadata
    Process:
        1. Generate new token using access control
        2. Store token information
        3. Return token details
    Dependencies: create_admin_token()
    """
    logger.info("Creating new access token via API")
    
    try:
        # Create token for admin user
        token, token_id = create_admin_token("admin_user")  # Default admin user
        
        token_info = get_token_by_id(token_id)
        if not token_info:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve token information"
            )
        
        logger.info(f"Created new token via API with ID: {token_id}")
        return {
            "token_id": token_id,
            "token": token,
            "user": token_info["user"],
            "created_at": token_info["created_at"].isoformat(),
            "expires_at": token_info["expires_at"].isoformat(),
            "status": "active"
        }
    except Exception as e:
        logger.error(f"Failed to create token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create token: {str(e)}"
        )

@router.get("/tokens")
async def list_tokens():
    """
    List all active tokens
    
    Purpose: Retrieve information about currently active access tokens
    Input: None
    Output: List of active token information
    Process:
        1. Get list of active tokens
        2. Return token information
    Dependencies: list_active_tokens()
    """
    logger.info("Listing active tokens via API")
    
    try:
        active_tokens = list_active_tokens()
        logger.info(f"Returning {len(active_tokens)} active tokens")
        return {
            "tokens": active_tokens,
            "count": len(active_tokens),
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Failed to list tokens: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list tokens: {str(e)}"
        )

@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str):
    """
    Revoke access token
    
    Purpose: Invalidate an existing access token
    Input: token_id - ID of the token to revoke
    Output: Confirmation message with token status
    Process:
        1. Validate token exists
        2. Invalidate token using access control
        3. Return confirmation with details
    Dependencies: revoke_token_by_id(), get_token_by_id()
    """
    logger.info(f"Revoking token via API: {token_id}")
    
    try:
        # Check if token exists
        token_info = get_token_by_id(token_id)
        if not token_info:
            logger.warning(f"Attempt to revoke non-existent token: {token_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Token {token_id} not found"
            )
        
        # Revoke the token
        success = revoke_token_by_id(token_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to revoke token {token_id}"
            )
        
        logger.info(f"Successfully revoked token via API: {token_id}")
        return {
            "message": f"Token {token_id} revoked successfully",
            "token_id": token_id,
            "user": token_info["user"],
            "status": "success"
        }
    except HTTPException:
        raise  # Re-raise HTTP exceptions
    except Exception as e:
        logger.error(f"Failed to revoke token {token_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to revoke token: {str(e)}"
        )

# --- DATABASE BROWSER ENDPOINTS ---

@router.get("/db")
async def browse_database():
    """
    Browse database
    
    Purpose: Provide interface to query and manage database
    Input: None (query parameters to be added)
    Output: Database query results
    Process:
        1. Parse query parameters
        2. Execute database query
        3. Return results
    Dependencies: Database connection, db/models.py
    """
    logger.info("Database browser endpoint accessed")
    
    # TODO: Implement actual database browsing logic
    # This is a placeholder implementation
    
    return {
        "message": "Database browser endpoint",
        "status": "not_implemented"
    }

# --- SOURCE MANAGEMENT ENDPOINTS ---

@router.post("/sources")
async def add_source():
    """
    Add new source to config.yaml
    
    Purpose: Add a new data source to the configuration
    Input: Source configuration data
    Output: Confirmation with new source ID
    Process:
        1. Validate source configuration
        2. Add to config.yaml
        3. Return new source info
    Dependencies: config.yaml access
    """
    logger.info("Adding new source to configuration")
    
    # TODO: Implement actual source addition logic
    # This is a placeholder implementation
    
    return {
        "message": "Source added successfully",
        "source_id": "new_source_id",
        "status": "success"
    }

@router.put("/sources/{source_id}")
async def update_source(source_id: str):
    """
    Update existing source in config.yaml
    
    Purpose: Update an existing data source configuration
    Input: source_id - ID of source to update
    Output: Confirmation message
    Process:
        1. Validate source exists
        2. Update configuration
        3. Return confirmation
    Dependencies: config.yaml access
    """
    logger.info(f"Updating source: {source_id}")
    
    # TODO: Implement actual source update logic
    # This is a placeholder implementation
    
    return {
        "message": f"Source {source_id} updated successfully",
        "status": "success"
    }

@router.delete("/sources/{source_id}")
async def remove_source(source_id: str):
    """
    Remove source from config.yaml
    
    Purpose: Remove a data source from configuration
    Input: source_id - ID of source to remove
    Output: Confirmation message
    Process:
        1. Validate source exists
        2. Remove from config.yaml
        3. Return confirmation
    Dependencies: config.yaml access
    """
    logger.info(f"Removing source: {source_id}")
    
    # TODO: Implement actual source removal logic
    # This is a placeholder implementation
    
    return {
        "message": f"Source {source_id} removed successfully",
        "status": "success"
    }

# --- SCRAPING AND ENRICHMENT ENDPOINTS ---

@router.post("/scrape")
async def trigger_scraping():
    """
    Trigger scraping and enrichment process
    
    Purpose: Manually trigger data processing jobs
    Input: Job configuration (source/entity IDs)
    Output: Job status and ID
    Process:
        1. Validate job configuration
        2. Start scraping/enrichment job
        3. Return job info
    Dependencies: Scraping/enrichment modules
    """
    logger.info("Triggering scraping and enrichment process")
    
    # TODO: Implement actual job triggering logic
    # This is a placeholder implementation
    
    return {
        "message": "Scraping job triggered successfully",
        "job_id": "job_12345",
        "status": "queued"
    }

# --- FILE UPLOAD ENDPOINTS ---

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Upload file for processing
    
    Purpose: Allow uploading PDF files for Medium/LinkedIn processing
    Input: file - File to upload
    Output: Confirmation with file info
    Process:
        1. Validate file type
        2. Save to data directory
        3. Return file info
    Dependencies: None
    """
    logger.info(f"Uploading file: {file.filename}")
    
    try:
        # Validate file type
        if not file.filename.endswith('.pdf'):
            logger.warning(f"Invalid file type: {file.filename}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only PDF files are allowed"
            )
        
        # Create data directory if it doesn't exist
        data_dir = "data"
        os.makedirs(data_dir, exist_ok=True)
        
        # Save file
        filepath = os.path.join(data_dir, file.filename)
        with open(filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"File saved successfully: {filepath}")
        return {
            "message": "File uploaded successfully",
            "filename": file.filename,
            "size": len(content),
            "path": filepath
        }
    except Exception as e:
        logger.error(f"Error uploading file {file.filename}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {str(e)}"
        )