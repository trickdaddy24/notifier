"""
Simple, secure single-user authentication for the Notifier web UI.

Designed for personal / self-hosted use in Docker.

Configuration (via .env):
    NOTIFIER_WEB_PASSWORD=your-strong-password          # Plain text (simple)
    # OR preferred:
    NOTIFIER_WEB_PASSWORD_HASH=$2b$12$...               # bcrypt hash

Session uses signed cookies (itsdangerous). No database sessions needed.

Supports:
- Nice HTML login form (POST /login)
- Cookie-based sessions after login
- Optional Bearer token or ?token= for API/script access
- Logout
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import bcrypt


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SECRET_KEY = os.getenv("NOTIFIER_WEB_SECRET_KEY") or secrets.token_urlsafe(32)
SESSION_COOKIE_NAME = "notifier_session"
SESSION_MAX_AGE = int(os.getenv("NOTIFIER_WEB_SESSION_MAX_AGE", 60 * 60 * 24 * 7))  # 7 days default

# Support both plain password and pre-hashed (recommended)
PLAIN_PASSWORD = os.getenv("NOTIFIER_WEB_PASSWORD", "").strip()
PASSWORD_HASH = os.getenv("NOTIFIER_WEB_PASSWORD_HASH", "").strip()

if not PASSWORD_HASH and not PLAIN_PASSWORD:
    # Allow running without auth for development (explicit opt-in)
    AUTH_ENABLED = False
else:
    AUTH_ENABLED = True


# Lazy serializer (created on first use)
_serializer: Optional[URLSafeTimedSerializer] = None


def get_serializer() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(SECRET_KEY, salt="notifier-web-session")
    return _serializer


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return a bcrypt hash of the password."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time verification against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def _get_effective_password_hash() -> Optional[str]:
    """Return the bcrypt hash we should compare against."""
    if PASSWORD_HASH:
        return PASSWORD_HASH
    if PLAIN_PASSWORD:
        # Hash on the fly (convenient for first-time setup)
        return hash_password(PLAIN_PASSWORD)
    return None


def is_auth_enabled() -> bool:
    return AUTH_ENABLED


# ---------------------------------------------------------------------------
# Session token handling
# ---------------------------------------------------------------------------

def create_session_token(username: str = "admin") -> str:
    """Create a signed session token."""
    payload = {
        "user": username,
        "iat": datetime.now(timezone.utc).isoformat(),
    }
    return get_serializer().dumps(payload)


def decode_session_token(token: str) -> Optional[dict]:
    """Decode and validate a session token. Returns payload or None."""
    try:
        data = get_serializer().loads(token, max_age=SESSION_MAX_AGE)
        return data
    except (BadSignature, SignatureExpired, TypeError):
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """
    Dependency that returns the logged-in username or None.

    Checks (in order):
    1. Cookie session (primary for browser)
    2. Authorization: Bearer <token>
    3. ?token=... query parameter (useful for scripts / health checks)
    """
    if not is_auth_enabled():
        return "admin"  # Auth disabled — everyone is admin

    # 1. Cookie (browser sessions)
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        payload = decode_session_token(token)
        if payload and "user" in payload:
            return payload["user"]

    # 2. Bearer token
    if credentials and credentials.credentials:
        payload = decode_session_token(credentials.credentials)
        if payload and "user" in payload:
            return payload["user"]

    # 3. Query param token (for APIs / curl)
    token = request.query_params.get("token")
    if token:
        payload = decode_session_token(token)
        if payload and "user" in payload:
            return payload["user"]

    return None


def require_login(user: Optional[str] = Depends(get_current_user)):
    """Dependency that raises 401/redirect if the user is not logged in."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


def login_redirect(request: Request, next_url: str = "/") -> RedirectResponse:
    """Helper to redirect unauthenticated users to the login page."""
    login_url = f"/login?next={next_url}"
    return RedirectResponse(login_url, status_code=302)


# ---------------------------------------------------------------------------
# Login / Logout helpers (used by routes)
# ---------------------------------------------------------------------------

def perform_login(username: str, password: str) -> Optional[str]:
    """
    Attempt to log the user in with username + password.
    The password must match the configured one (username is freeform for display).
    Returns a signed session token on success, or None on failure.
    """
    if not is_auth_enabled():
        return create_session_token(username or "admin")

    effective_hash = _get_effective_password_hash()
    if not effective_hash:
        return None

    if verify_password(password, effective_hash):
        return create_session_token(username or "admin")
    return None


def perform_logout() -> None:
    """Nothing to do server-side (stateless signed cookie)."""
    pass


def get_session_cookie(token: str, *, max_age: int | None = None) -> dict:
    """Return a properly configured cookie dict for FastAPI response."""
    return {
        "key": SESSION_COOKIE_NAME,
        "value": token,
        "httponly": True,
        "secure": False,          # Set to True behind HTTPS / in production
        "samesite": "lax",
        "max_age": max_age or SESSION_MAX_AGE,
    }


def clear_session_cookie() -> dict:
    """Return values to delete the session cookie."""
    return {
        "key": SESSION_COOKIE_NAME,
        "value": "",
        "httponly": True,
        "max_age": 0,
    }