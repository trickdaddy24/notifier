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
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from .auth import (
    perform_login,
    get_session_cookie,
    clear_session_cookie,
    get_current_user,
    is_auth_enabled,
    login_redirect,
)

# Import shared notification delivery
try:
    from notifier.notifications import send_notifications, set_quiet_mode
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from notifier.notifications import send_notifications, set_quiet_mode


BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Use the shared database module from the main notifier package.
# This ensures the web UI and CLI use exactly the same schema and logic.
try:
    from notifier.db import get_db, init_db, DB_PATH
except ImportError:
    # Fallback for when running web/ in isolation (development)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from notifier.db import get_db, init_db, DB_PATH


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


# Global APScheduler instance
scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run on application startup and shutdown."""
    global scheduler

    # Setup proper logging for the web container
    from notifier.notifications import configure_logging
    configure_logging()

    logger = logging.getLogger("notifier.web")
    logger.info("Notifier Web starting up...")

    print(f"[notifier-web] Initializing database at {DB_PATH} (using shared notifier.db)")
    init_db(backfill_legacy=False)
    print("[notifier-web] Database ready using shared schema.")

    # Optional: Seed sample data
    if os.getenv("SEED_SAMPLE_DATA", "0").lower() in ("1", "true", "yes"):
        _seed_sample_data()

    # Start APScheduler (much better than the old 'schedule' library for web apps)
    set_quiet_mode(True)
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_notifications, "interval", minutes=1, id="send_notifications")
    scheduler.start()
    print("[notifier-web] APScheduler started — notifications will be sent every minute.")

    yield

    # Graceful shutdown
    if scheduler:
        scheduler.shutdown(wait=False)
        print("[notifier-web] APScheduler shut down.")


def _seed_sample_data():
    """Seed a few example reminders if the database is empty (dev/demo helper)."""
    from datetime import timedelta

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM notifications")
        count = c.fetchone()["cnt"]
        if count > 0:
            return

        samples = [
            ("Take out the trash", "in 2 hours", None, None),
            ("Pay electricity bill", "tomorrow 09:00", "weekly", None),
            ("Call mom", "2025-06-01 18:00", None, None),
        ]
        for msg, due, rec, rep in samples:
            try:
                if due.startswith("in "):
                    hours = int(due.split()[1])
                    due_dt = datetime.now() + timedelta(hours=hours)
                else:
                    due_dt = datetime.fromisoformat(due.replace(" ", "T"))
                due_str = due_dt.strftime("%Y-%m-%d %H:%M")
                due_ts = int(due_dt.timestamp())
            except Exception:
                due_str = due
                due_ts = 0

            c.execute(
                "INSERT INTO notifications (message, due_time, due_ts, recurrence, repeat_time) "
                "VALUES (?, ?, ?, ?, ?)",
                (msg, due_str, due_ts, rec, rep),
            )
        conn.commit()
        print("[notifier-web] Seeded sample reminders for demo purposes.")

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
    due: str = Form(...),
    recurrence: str = Form(""),
    user: Optional[str] = Depends(get_current_user),
):
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    if not message or not message.strip():
        return JSONResponse({"error": "Message is required"}, status_code=400)

    try:
        dt = datetime.fromisoformat(due.replace("T", " "))
        due_str = dt.strftime("%Y-%m-%d %H:%M")
        due_ts = int(dt.timestamp())
    except Exception:
        return JSONResponse({"error": "Invalid date/time format"}, status_code=400)

    rec = recurrence or None
    repeat_time = None
    if rec == "daily":
        repeat_time = dt.strftime("%H:%M")

    try:
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
    except Exception as e:
        return JSONResponse({"error": f"Database error: {str(e)}"}, status_code=500)


@app.delete("/api/reminders/{reminder_id}")
async def delete_reminder(
    reminder_id: int,
    user: Optional[str] = Depends(get_current_user),
):
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM notifications WHERE id = ?", (reminder_id,))
            conn.commit()
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": f"Failed to delete reminder: {str(e)}"}, status_code=500)


@app.post("/api/test-telegram")
async def test_telegram(user: Optional[str] = Depends(get_current_user)):
    """
    Send a test message via Telegram.
    Returns very clear error messages to help the user debug configuration.
    """
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        from notifier.notifications import send_telegram_message

        test_message = (
            f"✅ Test from Notifier Web UI (v2.2.0)\n"
            f"User: {user}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        success, response = send_telegram_message(test_message)

        if success:
            logger.info(f"User '{user}' successfully sent Telegram test message")
            return {
                "success": True,
                "message": "✅ Test message sent successfully to Telegram!"
            }
        else:
            # Give the user actionable advice
            if "Missing" in response:
                helpful = (
                    f"{response}\n\n"
                    "→ Please add these two lines to your .env file on the server:\n"
                    "TELEGRAM_BOT_TOKEN=your_bot_token\n"
                    "TELEGRAM_CHAT_ID=your_chat_id\n\n"
                    "Then run: docker compose -f compose.yml up -d --force-recreate"
                )
            else:
                helpful = response

            logger.warning(f"Telegram test failed for user '{user}': {response}")
            return JSONResponse(
                {"success": False, "error": helpful},
                status_code=400
            )

    except Exception as e:
        logger.exception("Unexpected error in /api/test-telegram")
        return JSONResponse(
            {"success": False, "error": f"Unexpected error: {str(e)}"},
            status_code=500
        )


@app.get("/api/logs")
async def get_logs(limit: int = 30, user: Optional[str] = Depends(get_current_user)):
    """Return recent notification activity logs (joined with reminder message when possible)."""
    if user is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT 
                    l.id,
                    l.timestamp,
                    l.channel,
                    l.status,
                    l.response,
                    l.notification_id,
                    n.message
                FROM logs l
                LEFT JOIN notifications n ON l.notification_id = n.id
                ORDER BY l.id DESC
                LIMIT ?
            """, (limit,))
            rows = c.fetchall()

        logs = []
        for row in rows:
            logs.append({
                "id": row["id"],
                "timestamp": row["timestamp"],
                "channel": row["channel"],
                "status": row["status"],
                "response": row["response"],
                "notification_id": row["notification_id"],
                "message": row["message"] or "(deleted reminder)"
            })

        return {"logs": logs}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
