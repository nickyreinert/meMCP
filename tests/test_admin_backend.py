# Admin Backend Tests
# Tests for the admin backend with JWT authentication
# Dependent files: admin/main.py, admin/routers/admin.py

import os
import pytest
from starlette.testclient import TestClient

# Set env vars BEFORE importing admin modules
os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "testpass123")
os.environ.setdefault("ADMIN_SECRET_KEY", "test-secret-key-for-testing-only")

from admin.main import create_admin_app


@pytest.fixture
def client():
    app = create_admin_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_header(client):
    """Login and return Authorization header."""
    resp = client.post("/admin/login", json={
        "username": "testadmin",
        "password": "testpass123",
    })
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- Auth tests ---

def test_login_success(client):
    resp = client.post("/admin/login", json={
        "username": "testadmin",
        "password": "testpass123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password(client):
    resp = client.post("/admin/login", json={
        "username": "testadmin",
        "password": "wrongpass",
    })
    assert resp.status_code == 401


def test_endpoint_requires_auth(client):
    """Endpoints should return 401 without a valid JWT."""
    resp = client.get("/admin/logs")
    assert resp.status_code == 401


# --- Root (no auth, for healthcheck) ---

def test_admin_root(client, auth_header):
    resp = client.get("/admin/", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"


# --- Log endpoints ---

def test_get_logs(client, auth_header):
    resp = client.get("/admin/logs", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert "logs" in data
    assert isinstance(data["logs"], list)


def test_download_log_not_found(client, auth_header):
    resp = client.get("/admin/logs/nonexistent.log", headers=auth_header)
    assert resp.status_code == 404


def test_download_log_path_traversal(client, auth_header):
    resp = client.get("/admin/logs/../../etc/passwd", headers=auth_header)
    assert resp.status_code == 400


# --- Token endpoints ---

def test_list_tokens(client, auth_header):
    resp = client.get("/admin/tokens", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert "tokens" in data
    assert "count" in data


def test_create_and_revoke_token(client, auth_header):
    # Create
    resp = client.post("/admin/tokens", json={
        "owner": "test-owner",
        "days": 7,
        "tier": "mcp",
    }, headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert "token_id" in data
    assert "token" in data
    assert data["owner"] == "test-owner"
    assert data["tier"] == "mcp"
    token_id = data["token_id"]

    # Revoke
    resp = client.delete(f"/admin/tokens/{token_id}", headers=auth_header)
    assert resp.status_code == 200
    assert "revoked" in resp.json()["message"].lower()


def test_create_token_invalid_tier(client, auth_header):
    resp = client.post("/admin/tokens", json={
        "owner": "test",
        "tier": "invalid",
    }, headers=auth_header)
    assert resp.status_code == 400


def test_revoke_nonexistent_token(client, auth_header):
    resp = client.delete("/admin/tokens/99999", headers=auth_header)
    assert resp.status_code == 404


# --- Database browser ---

def test_browse_database(client, auth_header):
    resp = client.get("/admin/db", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert "entities" in data
    assert "count" in data


def test_database_stats(client, auth_header):
    resp = client.get("/admin/db/stats", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_entities" in data
    assert "by_flavor" in data


def test_list_tags(client, auth_header):
    resp = client.get("/admin/db/tags", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert "tags" in data


# --- Source management ---

def test_list_sources(client, auth_header):
    resp = client.get("/admin/sources", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    assert "count" in data


# --- Jobs ---

def test_list_jobs(client, auth_header):
    resp = client.get("/admin/jobs", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data


def test_get_nonexistent_job(client, auth_header):
    resp = client.get("/admin/jobs/nonexistent", headers=auth_header)
    assert resp.status_code == 404


# --- Upload ---

def test_upload_non_pdf(client, auth_header):
    resp = client.post(
        "/admin/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
        headers=auth_header,
    )
    assert resp.status_code == 400
