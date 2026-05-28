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
from pathlib import Path
from typing import Optional

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
                # We import these helpers locally to avoid circular imports
                try:
                    from . import _parse_due_time, _to_ts
                    dt = _parse_due_time(due_str)
                    if dt:
                        c.execute("UPDATE notifications SET due_ts = ? WHERE id = ?",
                                  (_to_ts(dt), row_id))
                except Exception:
                    # If helpers are not available or parsing fails, skip silently
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
