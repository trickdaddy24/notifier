"""
Shared database layer for Notifier (CLI + Web UI).

This module provides a single source of truth for:
- Database connection management
- Schema initialization + migrations
- (Future) query helpers

Both `notifier.py` and `web/main.py` should import from here.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DB_NAME = "notifications.db"


def get_db_path() -> Path:
    """
    Resolve the database path with the following priority:
    1. NOTIFIER_DB_PATH env var (highest priority)
    2. If running inside Docker → /app/data/notifications.db
    3. Otherwise → notifications.db in current working directory
    """
    env_path = os.getenv("NOTIFIER_DB_PATH")
    if env_path:
        return Path(env_path)

    # Detect Docker environment
    if Path("/.dockerenv").exists() or os.getenv("DOCKER_CONTAINER"):
        return Path("/app/data/notifications.db")

    return Path(DEFAULT_DB_NAME)


DB_PATH: Path = get_db_path()


# ---------------------------------------------------------------------------
# Connection Management
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    """
    Thread-safe SQLite connection with WAL mode and sensible defaults.
    Use as a context manager:

        with get_db() as conn:
            conn.execute(...)
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema Initialization
# ---------------------------------------------------------------------------

def init_db(backfill_legacy: bool = True) -> None:
    """
    Initialize (or migrate) the notifier database.

    This function is the single source of truth for the schema.
    It is safe to call multiple times.

    Args:
        backfill_legacy: Whether to backfill due_ts for very old records.
                         Set to False in web UI for faster startup.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_db() as conn:
        c = conn.cursor()

        # notifications table
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

        # audit logs table
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

        # Simple key-value settings table for web UI configuration
        c.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        # Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(sent, due_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(timestamp)")

        # Migrations for older databases
        for col, definition in [
            ("due_ts", "INTEGER DEFAULT 0"),
            ("recurrence", "TEXT DEFAULT NULL"),
            ("repeat_time", "TEXT DEFAULT NULL"),
        ]:
            try:
                c.execute(f"ALTER TABLE notifications ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Optional legacy backfill (expensive on large old DBs)
        if backfill_legacy:
            c.execute("SELECT id, due_time FROM notifications WHERE due_ts = 0 OR due_ts IS NULL")
            for row_id, due_str in c.fetchall():
                try:
                    # Use the local _to_ts defined in this module
                    dt = datetime.strptime(due_str, "%Y-%m-%d %H:%M")
                    c.execute("UPDATE notifications SET due_ts = ? WHERE id = ?",
                              (_to_ts(dt), row_id))
                except Exception:
                    # Skip silently if parsing fails
                    pass

        conn.commit()


def get_setting(key: str, default: str = None) -> str:
    """Get a setting value from the database."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Save or update a setting in the database."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Timezone helpers (shared between CLI and Web UI)
# ---------------------------------------------------------------------------

def _get_user_tz():
    """Return a ZoneInfo for the configured TIMEZONE env var, or None for system local."""
    tz_name = os.getenv('TIMEZONE', '').strip()
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        # Only print warning when running in CLI context (web suppresses this)
        if os.getenv("NOTIFIER_WEB_PASSWORD") is None:
            print(f"⚠️  Unknown timezone '{tz_name}' — using system local time.")
        return None


def _now_in_tz() -> datetime:
    """Current time in the configured timezone as a naive datetime."""
    tz = _get_user_tz()
    if tz is None:
        return datetime.now()
    return datetime.now(tz).replace(tzinfo=None)


def _tz_label() -> str:
    """Short timezone label for display."""
    tz_name = os.getenv('TIMEZONE', '').strip()
    return tz_name if tz_name else "system local"


def _to_ts(dt: datetime) -> int:
    """Epoch seconds for a *naive* wall-clock datetime, interpreted in the
    configured TIMEZONE (or system local if unset).

    A bare ``datetime.timestamp()`` assumes the machine's local zone, which is
    wrong whenever TIMEZONE differs from the host clock (e.g. a UTC server).
    All scheduling math must go through this helper.
    """
    tz = _get_user_tz()
    if tz is None:
        return int(dt.timestamp())
    return int(dt.replace(tzinfo=tz).timestamp())


def _from_ts(ts: int) -> datetime:
    """Inverse of _to_ts: naive wall-clock datetime in the configured TIMEZONE."""
    tz = _get_user_tz()
    if tz is None:
        return datetime.fromtimestamp(ts)
    return datetime.fromtimestamp(ts, tz).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Time sync mode (NTP/Server vs Local PC / Browser)
# ---------------------------------------------------------------------------

def get_time_mode() -> str:
    """Return the current time interpretation mode: 'server' (NTP) or 'local' (browser/PC)."""
    val = get_setting("time_mode", "server")
    return val if val in ("server", "local") else "server"


def set_time_mode(mode: str) -> None:
    """Persist the time mode. Accepts 'server' or 'local'."""
    if mode not in ("server", "local"):
        mode = "server"
    set_setting("time_mode", mode)


def to_epoch(dt: datetime, browser_tz: str | None = None) -> int:
    """Convert a naive wall-clock datetime to epoch seconds.

    Behavior depends on the persisted time_mode setting:
    - 'server' (default, NTP): use the configured TIMEZONE env (same as _to_ts)
    - 'local': if browser_tz (IANA name) is provided, interpret dt in that zone.
               Falls back to server mode if browser_tz is missing or invalid.

    This is the single function web UI reminder creation should use.
    """
    mode = get_time_mode()

    if mode == "local" and browser_tz:
        try:
            tz = ZoneInfo(browser_tz)
            return int(dt.replace(tzinfo=tz).timestamp())
        except (ZoneInfoNotFoundError, KeyError, Exception):
            # Bad tz name → fall back to server time so we never lose the reminder
            pass

    # Server / NTP mode (or fallback)
    return _to_ts(dt)
