# Admin Backend Tests
# Purpose: Test the admin backend functionality
# Main functions: test_admin_root(), test_logs_endpoint()
# Dependent files: admin/main.py, admin/routers/admin.py

import pytest
from starlette.testclient import TestClient
from admin.main import create_admin_app
import logging

# Initialize logging
logger = logging.getLogger(__name__)

# --- TEST SETUP ---

@pytest.fixture
def client():
    """
    Create test client for admin app
    
    Purpose: Provide a test client for API testing
    Input: None
    Output: TestClient instance
    Process: Create admin app and test client
    Dependencies: create_admin_app()
    """
    logger.info("Setting up test client")
    app = create_admin_app()
    with TestClient(app) as client:
        yield client

# --- BASIC ENDPOINT TESTS ---

def test_admin_root(client):
    """
    Test admin root endpoint
    
    Purpose: Verify admin root endpoint returns expected response
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make GET request to /
        2. Assert status code is 200
        3. Assert response contains expected data
    Dependencies: None
    """
    logger.info("Testing admin root endpoint")
    
    response = client.get("/")
    assert response.status_code == 200
    
    data = response.json()
    assert "message" in data
    assert data["message"] == "MCP Admin Backend"
    assert "status" in data
    assert data["status"] == "active"
    
    logger.info("Admin root endpoint test passed")

def test_admin_root_with_prefix(client):
    """
    Test admin root endpoint with /admin prefix
    
    Purpose: Verify admin root endpoint works with /admin prefix
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make GET request to /admin/
        2. Assert status code is 200
        3. Assert response contains expected data
    Dependencies: None
    """
    logger.info("Testing admin root endpoint with prefix")
    
    response = client.get("/admin/")
    assert response.status_code == 200
    
    data = response.json()
    assert "message" in data
    assert data["message"] == "MCP Admin Backend"
    assert "status" in data
    assert data["status"] == "active"
    
    logger.info("Admin root endpoint with prefix test passed")

# --- LOG MANAGEMENT TESTS ---

def test_get_logs_empty(client):
    """
    Test get logs endpoint with no log files
    
    Purpose: Verify logs endpoint handles empty log directory
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make GET request to /admin/logs
        2. Assert status code is 200
        3. Assert response contains empty logs list
    Dependencies: None
    """
    logger.info("Testing get logs endpoint with empty directory")
    
    response = client.get("/admin/logs")
    assert response.status_code == 200
    
    data = response.json()
    assert "logs" in data
    assert isinstance(data["logs"], list)
    assert len(data["logs"]) == 0
    
    logger.info("Get logs empty test passed")

# --- TOKEN MANAGEMENT TESTS ---

def test_create_token(client):
    """
    Test create token endpoint
    
    Purpose: Verify token creation endpoint works
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make POST request to /admin/tokens
        2. Assert status code is 200
        3. Assert response contains token data
    Dependencies: None
    """
    logger.info("Testing create token endpoint")
    
    response = client.post("/admin/tokens")
    assert response.status_code == 200
    
    data = response.json()
    assert "token" in data
    assert "created_at" in data
    assert "expires_at" in data
    
    logger.info("Create token test passed")

def test_revoke_token(client):
    """
    Test revoke token endpoint
    
    Purpose: Verify token revocation endpoint works
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make DELETE request to /admin/tokens/test_token
        2. Assert status code is 200
        3. Assert response contains success message
    Dependencies: None
    """
    logger.info("Testing revoke token endpoint")
    
    token_id = "test_token_123"
    response = client.delete(f"/admin/tokens/{token_id}")
    assert response.status_code == 200
    
    data = response.json()
    assert "message" in data
    assert token_id in data["message"]
    assert "revoked successfully" in data["message"]
    assert data["status"] == "success"
    
    logger.info("Revoke token test passed")

# --- SOURCE MANAGEMENT TESTS ---

def test_add_source(client):
    """
    Test add source endpoint
    
    Purpose: Verify source addition endpoint works
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make POST request to /admin/sources
        2. Assert status code is 200
        3. Assert response contains success message
    Dependencies: None
    """
    logger.info("Testing add source endpoint")
    
    response = client.post("/admin/sources")
    assert response.status_code == 200
    
    data = response.json()
    assert "message" in data
    assert "added successfully" in data["message"]
    assert "source_id" in data
    assert data["status"] == "success"
    
    logger.info("Add source test passed")

def test_update_source(client):
    """
    Test update source endpoint
    
    Purpose: Verify source update endpoint works
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make PUT request to /admin/sources/test_source
        2. Assert status code is 200
        3. Assert response contains success message
    Dependencies: None
    """
    logger.info("Testing update source endpoint")
    
    source_id = "test_source_123"
    response = client.put(f"/admin/sources/{source_id}")
    assert response.status_code == 200
    
    data = response.json()
    assert "message" in data
    assert source_id in data["message"]
    assert "updated successfully" in data["message"]
    assert data["status"] == "success"
    
    logger.info("Update source test passed")

def test_remove_source(client):
    """
    Test remove source endpoint
    
    Purpose: Verify source removal endpoint works
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make DELETE request to /admin/sources/test_source
        2. Assert status code is 200
        3. Assert response contains success message
    Dependencies: None
    """
    logger.info("Testing remove source endpoint")
    
    source_id = "test_source_123"
    response = client.delete(f"/admin/sources/{source_id}")
    assert response.status_code == 200
    
    data = response.json()
    assert "message" in data
    assert source_id in data["message"]
    assert "removed successfully" in data["message"]
    assert data["status"] == "success"
    
    logger.info("Remove source test passed")

# --- SCRAPING TESTS ---

def test_trigger_scraping(client):
    """
    Test trigger scraping endpoint
    
    Purpose: Verify scraping trigger endpoint works
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make POST request to /admin/scrape
        2. Assert status code is 200
        3. Assert response contains job info
    Dependencies: None
    """
    logger.info("Testing trigger scraping endpoint")
    
    response = client.post("/admin/scrape")
    assert response.status_code == 200
    
    data = response.json()
    assert "message" in data
    assert "triggered successfully" in data["message"]
    assert "job_id" in data
    assert data["status"] == "queued"
    
    logger.info("Trigger scraping test passed")

# --- DATABASE TESTS ---

def test_browse_database(client):
    """
    Test database browser endpoint
    
    Purpose: Verify database browser endpoint works
    Input: client - TestClient instance
    Output: None (assertions)
    Process:
        1. Make GET request to /admin/db
        2. Assert status code is 200
        3. Assert response contains expected data
    Dependencies: None
    """
    logger.info("Testing database browser endpoint")
    
    response = client.get("/admin/db")
    assert response.status_code == 200
    
    data = response.json()
    assert "message" in data
    assert "Database browser" in data["message"]
    assert data["status"] == "not_implemented"
    
    logger.info("Database browser test passed")