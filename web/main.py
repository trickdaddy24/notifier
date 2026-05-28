"""
Notifier Web UI — FastAPI

Uses raw Jinja2 (no Starlette Jinja2Templates helper) to avoid
the "TypeError: unhashable type: 'dict'" crash that some
reverse-proxy + middleware combinations trigger in the Starlette layer.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import jinja2
import os
import sqlite3
import time
from datetime import datetime

from .auth import (
    perform_login,
    get_session_cookie,
    clear_session_cookie,
    get_current_user,
    is_auth_enabled,
    login_redirect,
)


BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Database path - configurable for Docker vs local development
# - In Docker: defaults to the mounted volume /app/data/notifications.db
# - Locally: falls back to ./notifications.db in the current working directory
if os.getenv("NOTIFIER_DB_PATH"):
    DB_PATH = Path(os.getenv("NOTIFIER_DB_PATH"))
elif Path("/.dockerenv").exists() or os.getenv("DOCKER_CONTAINER"):
    DB_PATH = Path("/app/data/notifications.db")
else:
    # Local development default
    DB_PATH = Path("notifications.db")

DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_db():
    """Get a thread-safe SQLite connection with good defaults."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    Initialize the database schema.
    This is the single source of truth for the notifications table
    (kept in sync with the main notifier.py logic).
    """
    with get_db() as conn:
        c = conn.cursor()

        # Main notifications table (matches the monolith)
        c.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message     TEXT NOT NULL,
                due_time    TEXT NOT NULL,
                due_ts      INTEGER NOT NULL DEFAULT 0,
                sent        INTEGER DEFAULT 0,
                recurrence  TEXT DEFAULT NULL,
                repeat_time TEXT DEFAULT NULL
            )
        ''')

        # Audit log table (used by the CLI daemon)
        c.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER,
                timestamp       TEXT NOT NULL,
                channel         TEXT NOT NULL,
                status          TEXT NOT NULL,
                response        TEXT
            )
        ''')

        # Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(sent, due_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(timestamp)")

        # Lightweight migrations for older databases
        for col, definition in [
            ("due_ts", "INTEGER DEFAULT 0"),
            ("recurrence", "TEXT DEFAULT NULL"),
            ("repeat_time", "TEXT DEFAULT NULL"),
        ]:
            try:
                c.execute(f"ALTER TABLE notifications ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        conn.commit()


def get_upcoming_reminders(limit: int = 50):
    """Return upcoming (not yet sent) reminders, sorted by due time."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, message, due_time, due_ts, recurrence, repeat_time
            FROM notifications
            WHERE sent = 0
            ORDER BY due_ts ASC
            LIMIT ?
        """, (limit,))
        rows = c.fetchall()

    reminders = []
    now = int(time.time())
    for row in rows:
        reminders.append({
            "id": row["id"],
            "message": row["message"],
            "due_time": row["due_time"],
            "due_ts": row["due_ts"],
            "recurrence": row["recurrence"],
            "repeat_time": row["repeat_time"],
            "is_overdue": row["due_ts"] > 0 and row["due_ts"] < now,
        })
    return reminders


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run on application startup and shutdown."""
    # Initialize database schema on startup — this is critical
    print(f"[notifier-web] Initializing database at {DB_PATH}")
    init_db()
    print("[notifier-web] Database ready.")
    yield
    # Optional shutdown logic can go here

# Raw Jinja2 environment — no Starlette wrapper, no caching tricks that can break
jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html", "htm"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
# Explicitly disable any caching that could cause the unhashable-dict key error
jinja_env.cache = {}  # type: ignore[attr-defined]


def render_template(name: str, context: dict) -> str:
    """Render a template with the given context using raw Jinja2."""
    template = jinja_env.get_template(name)
    return template.render(context)


app = FastAPI(title="Notifier Web", lifespan=lifespan)

static_dir = BASE_DIR / "static"
if static_dir.exists() and any(static_dir.iterdir()):
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root(
    request: Request,
    user: Optional[str] = Depends(get_current_user),
):
    if user is None:
        return login_redirect(request, next_url="/")

    reminders = get_upcoming_reminders()

    html = render_template(
        "dashboard.html",
        {
            "user": user,
            "auth_enabled": is_auth_enabled(),
            "reminders": reminders,
            "reminder_count": len(reminders),
        },
    )
    return HTMLResponse(html)


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/",
    user: Optional[str] = Depends(get_current_user),
):
    if user is not None and is_auth_enabled():
        return RedirectResponse("/", status_code=302)

    error = request.query_params.get("error")
    html = render_template(
        "login.html",
        {
            "next_url": next or "/",
            "error": error,
            "auth_enabled": bool(is_auth_enabled()),
        },
    )
    return HTMLResponse(html)


@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form("admin"),
    password: str = Form(...),
    next: str = Form("/"),
):
    """Handle login form submission with username + password."""
    token = perform_login(username.strip(), password)
    if token is None:
        return RedirectResponse(
            f"/login?error=invalid&next={next}",
            status_code=status.HTTP_302_FOUND,
        )
    resp = RedirectResponse(next or "/", status_code=302)
    resp.set_cookie(**get_session_cookie(token))
    return resp


@app.post("/logout")
async def logout_post(user: Optional[str] = Depends(get_current_user)):
    resp = RedirectResponse("/login", status_code=302)
    resp.set_cookie(**clear_session_cookie())
    return resp


@app.get("/logout")
async def logout_get():
    resp = RedirectResponse("/login", status_code=302)
    resp.set_cookie(**clear_session_cookie())
    return resp


@app.get("/health")
async def health():
    """Health check — useful for Docker and monitoring."""
    db_ok = False
    db_path = str(DB_PATH)
    try:
        with get_db() as conn:
            conn.execute("SELECT 1 FROM notifications LIMIT 1")
            db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "auth": "on" if is_auth_enabled() else "off",
        "database": {
            "path": db_path,
            "connected": db_ok,
        }
    }


@app.get("/api/me")
async def api_me(user: Optional[str] = Depends(get_current_user)):
    if user is None:
        return JSONResponse({"authenticated": False}, status_code=401)
    return {"authenticated": True, "user": user}


# ── Reminder API (used by the dashboard) ─────────────────────────────────────

@app.post("/api/reminders")
async def create_reminder(
    message: str = Form(...),
    due: str = Form(...),                    # ISO datetime from <input type="datetime-local">
    recurrence: str = Form(""),              # "", "daily", "weekly", "monthly"
    user: Optional[str] = Depends(get_current_user),
):
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    if not message or not message.strip():
        return JSONResponse({"error": "Message is required"}, status_code=400)

    try:
        # datetime-local comes as "2025-05-29T14:30"
        dt = datetime.fromisoformat(due.replace("T", " "))
        due_str = dt.strftime("%Y-%m-%d %H:%M")
        due_ts = int(dt.timestamp())
    except Exception:
        return JSONResponse({"error": "Invalid date/time"}, status_code=400)

    rec = recurrence or None
    repeat_time = None
    if rec == "daily":
        # For daily we also store the time portion for the legacy scheduler
        repeat_time = dt.strftime("%H:%M")

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO notifications (message, due_time, due_ts, recurrence, repeat_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (message.strip(), due_str, due_ts, rec, repeat_time),
        )
        new_id = c.lastrowid
        conn.commit()

    return {"success": True, "id": new_id, "due": due_str}


@app.delete("/api/reminders/{reminder_id}")
async def delete_reminder(
    reminder_id: int,
    user: Optional[str] = Depends(get_current_user),
):
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM notifications WHERE id = ?", (reminder_id,))
        conn.commit()

    return {"success": True}
