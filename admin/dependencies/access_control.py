# Admin Access Control
# Purpose: HTTP Basic Auth for the admin backend
# Dependent files: None (standalone security module)

import os
import logging
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logger = logging.getLogger(__name__)

# Admin credentials from env vars
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

security = HTTPBasic()


def get_current_admin_user(
    credentials: HTTPBasicCredentials = Depends(security),
) -> str:
    """
    FastAPI dependency: validate HTTP Basic Auth credentials.
    Returns the username on success, raises 401 otherwise.
    """
    if not ADMIN_PASSWORD:
        logger.error("ADMIN_PASSWORD env var not set — login disabled")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin backend not configured (ADMIN_PASSWORD not set)",
        )

    # Constant-time comparison to prevent timing attacks
    username_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        ADMIN_USERNAME.encode("utf-8"),
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        ADMIN_PASSWORD.encode("utf-8"),
    )

    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username
