# Admin Access Control
# Purpose: Handle authentication and authorization for admin endpoints
# Main functions: authenticate_admin(), get_current_admin_user()
# Dependent files: None (standalone security module)

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from typing import Optional
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
import jwt

logger = logging.getLogger(__name__)

# --- Configuration from environment variables ---

SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning(
        "ADMIN_SECRET_KEY not set — generated a random key. "
        "JWTs will be invalidated on restart. Set ADMIN_SECRET_KEY env var for persistence."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ADMIN_TOKEN_EXPIRE_MINUTES", "60"))

# Admin credentials from env vars
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/admin/login", auto_error=True)


# --- Authentication ---

def authenticate_admin(username: str, password: str) -> bool:
    """
    Validate admin credentials against env vars.
    Returns True if credentials match.
    """
    if not ADMIN_PASSWORD:
        logger.error("ADMIN_PASSWORD env var not set — login disabled")
        return False
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token for an authenticated admin user."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": subject,
        "role": "admin",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_admin_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    FastAPI dependency: extract and validate admin user from JWT.
    Raises HTTP 401 if token is invalid or expired.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired admin token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        if username is None or role != "admin":
            raise credentials_exception
        return {"username": username, "role": role}
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise credentials_exception
