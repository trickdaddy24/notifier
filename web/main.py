"""
Notifier Web UI — FastAPI

Uses raw Jinja2 (no Starlette Jinja2Templates helper) to avoid
the "TypeError: unhashable type: 'dict'" crash that some
reverse-proxy + middleware combinations trigger in the Starlette layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import jinja2
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

# Use the mounted data volume so the web UI and CLI/daemon share the same database
DB_PATH = Path("/app/data/notifications.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_notifications_table():
    """Ensure the notifications table exists (same schema as the main notifier)."""
    with get_db() as conn:
        c = conn.cursor()
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
        c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(sent, due_ts)")
        conn.commit()


def get_upcoming_reminders(limit: int = 50):
    """Return upcoming (not yet sent) reminders, sorted by due time."""
    init_notifications_table()
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
    for rid, msg, due_time, due_ts, rec, rep in rows:
        reminders.append({
            "id": rid,
            "message": msg,
            "due_time": due_time,
            "due_ts": due_ts,
            "recurrence": rec,
            "repeat_time": rep,
            "is_overdue": due_ts > 0 and due_ts < now,
        })
    return reminders

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


app = FastAPI(title="Notifier Web")

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
    return {"status": "ok", "auth": "on" if is_auth_enabled() else "off"}


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

    init_notifications_table()
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
