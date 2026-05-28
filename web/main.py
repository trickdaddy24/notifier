"""
Notifier Web UI — FastAPI application entrypoint.

This is the beginning of the new web interface (Phase 2D).

Currently includes:
- Clean, secure login system (cookie + token support)
- Beautiful login form
- Protected dashboard placeholder

Run locally:
    pip install fastapi uvicorn python-multipart bcrypt itsdangerous
    uvicorn notifier.web.main:app --reload
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware  # not strictly needed but useful later

from .auth import (
    perform_login,
    get_session_cookie,
    clear_session_cookie,
    get_current_user,
    require_login,
    is_auth_enabled,
    login_redirect,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent

app = FastAPI(
    title="Notifier Web",
    description="Web interface for the multi-channel notification scheduler",
    version="0.1.0",
)

# Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Static files (for future CSS/JS)
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Optional[str] = Depends(get_current_user),
):
    """Protected main dashboard."""
    if user is None:
        return login_redirect(request, next_url="/")

    # Placeholder dashboard for now
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "auth_enabled": is_auth_enabled(),
        },
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/",
    user: Optional[str] = Depends(get_current_user),
):
    """Show the login form. If already logged in, redirect to dashboard."""
    if user is not None and is_auth_enabled():
        return RedirectResponse("/", status_code=302)

    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "next_url": next,
            "error": error,
            "auth_enabled": is_auth_enabled(),
        },
    )


@app.post("/login")
async def login(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
):
    """Handle login form submission."""
    token = perform_login(password)

    if token is None:
        # Invalid credentials — redirect back to login with error
        return RedirectResponse(
            f"/login?error=invalid&next={next}",
            status_code=status.HTTP_302_FOUND,
        )

    # Success — set signed session cookie
    response = RedirectResponse(next or "/", status_code=302)
    cookie = get_session_cookie(token)
    response.set_cookie(**cookie)
    return response


@app.post("/logout")
async def logout(request: Request, user: Optional[str] = Depends(get_current_user)):
    """Log the user out by clearing the session cookie."""
    response = RedirectResponse("/login", status_code=302)
    response.set_cookie(**clear_session_cookie())
    return response


@app.get("/logout")
async def logout_get(request: Request):
    """Convenience GET logout (useful for simple links)."""
    response = RedirectResponse("/login", status_code=302)
    response.set_cookie(**clear_session_cookie())
    return response


# ---------------------------------------------------------------------------
# Health & debug
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check endpoint (useful for Docker)."""
    return {"status": "ok", "auth": "enabled" if is_auth_enabled() else "disabled"}


@app.get("/api/me")
async def whoami(user: Optional[str] = Depends(get_current_user)):
    """Simple protected API endpoint for testing tokens."""
    if user is None:
        return JSONResponse({"authenticated": False}, status_code=401)
    return {"authenticated": True, "user": user}


# ---------------------------------------------------------------------------
# Development entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print("\n🔔  Notifier Web UI")
    print("   Run with: uvicorn notifier.web.main:app --reload\n")
    if not is_auth_enabled():
        print("   ⚠️  WARNING: Authentication is DISABLED (no WEB_PASSWORD set)\n")

    uvicorn.run("notifier.web.main:app", host="0.0.0.0", port=8000, reload=True)