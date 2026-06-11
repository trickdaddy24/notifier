"""
Shared database layer for Notifier (CLI + Web UI).

This module provides a single source of truth for:
- Database connection management
- Schema initialization + migrations
- (Future) query helpers

Both `notifier.py` and `web/main.py` should import from here.
"""

from __future__ import annotations

import calendar
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
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

        # Countdown events table. An event (e.g. a cruise) is the source of
        # truth for a countdown; it "expands" into one ordinary notifications
        # row per future milestone (see expand_event), so the existing
        # scheduler delivers the countdown pings with no special-casing.
        c.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                target_date  TEXT NOT NULL,                       -- "YYYY-MM-DD"
                target_ts    INTEGER NOT NULL DEFAULT 0,          -- epoch at send_time, in TIMEZONE
                category     TEXT DEFAULT NULL,                   -- e.g. "cruise" (flavours the message) or NULL
                details      TEXT DEFAULT NULL,                   -- freeform note (ship, confirmation #, ...)
                milestones   TEXT NOT NULL DEFAULT '60,30,14,7,3,1,0',  -- CSV of day-offsets before target
                send_time    TEXT NOT NULL DEFAULT '09:00',       -- HH:MM (local) each milestone fires at
                cadence      TEXT NOT NULL DEFAULT 'milestones',  -- 'milestones' | 'daily'
                created_ts   INTEGER NOT NULL DEFAULT 0,
                active       INTEGER NOT NULL DEFAULT 1
            )
        ''')

        # Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(sent, due_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_target ON events(active, target_ts)")

        # Migrations for older databases
        for col, definition in [
            ("due_ts", "INTEGER DEFAULT 0"),
            ("recurrence", "TEXT DEFAULT NULL"),
            ("repeat_time", "TEXT DEFAULT NULL"),
            # Links a milestone notification back to its countdown event so
            # editing/deleting the event can re-expand or clean up its pings.
            ("event_id", "INTEGER DEFAULT NULL"),
        ]:
            try:
                c.execute(f"ALTER TABLE notifications ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Migrations for older events tables
        for col, definition in [
            ("cadence", "TEXT NOT NULL DEFAULT 'milestones'"),
        ]:
            try:
                c.execute(f"ALTER TABLE events ADD COLUMN {col} {definition}")
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

    # First-run seed of countdown events (e.g. migrated from cruise-notifier's
    # cruises.json). Guarded by a settings flag so deleting every event does
    # not cause it to reappear on the next startup. Done after the connection
    # above is closed because create_event opens its own.
    _seed_events_once()


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


def _relative_due(due_ts: int) -> str:
    """Human 'in 3h 12m' / 'overdue 5h' / 'due now' from an absolute epoch."""
    if not due_ts:
        return ""
    delta = int(due_ts) - int(time.time())
    overdue = delta < 0
    secs = abs(delta)
    if secs < 60:
        return "due now"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        body = f"{d}d {h}h" if h else f"{d}d"
    elif h:
        body = f"{h}h {m}m" if m else f"{h}h"
    else:
        body = f"{m}m"
    return f"overdue {body}" if overdue else f"in {body}"


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


# ---------------------------------------------------------------------------
# Date parsing & recurrence math (shared between CLI and Web UI)
# ---------------------------------------------------------------------------

def _parse_due_time(due_str: str) -> Optional[datetime]:
    """Parse a due-time string into a naive datetime.

    Supports both YYYY-MM-DD and MM-DD-YYYY, with or without seconds.
    Returns None if nothing matches.
    """
    if not due_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%m-%d-%Y %H:%M", "%m-%d-%Y %H:%M:%S"):
        try:
            return datetime.strptime(due_str, fmt)
        except ValueError:
            continue
    return None


def _next_daily_time(time_str: str) -> Optional[datetime]:
    """Return the next future datetime for a daily HH:MM schedule (in TIMEZONE)."""
    try:
        h, m = map(int, time_str.split(':'))
    except (ValueError, AttributeError):
        return None
    now = _now_in_tz()
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_month_dt(dt: datetime) -> datetime:
    """Return the same day next calendar month, clamped to the last valid day."""
    month = dt.month + 1
    year = dt.year
    if month > 12:
        month = 1
        year += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _next_recurrence_ts(due_ts: int, recurrence: str,
                        repeat_time: Optional[str] = None) -> Optional[int]:
    """Compute the next due_ts for a recurring notification. Rolls forward if past.

    Returns None when the recurrence type is unknown or has no next occurrence.
    """
    now_ts = int(time.time())

    if recurrence == "daily" and repeat_time:
        next_dt = _next_daily_time(repeat_time)
        return _to_ts(next_dt) if next_dt else None

    if recurrence == "monthly":
        next_dt = _next_month_dt(_from_ts(due_ts))
        next_ts = _to_ts(next_dt)
        while next_ts <= now_ts:
            next_dt = _next_month_dt(next_dt)
            next_ts = _to_ts(next_dt)
        return next_ts

    steps = {"daily": 86400, "weekly": 604800, "biweekly": 1209600}
    step = steps.get(recurrence, 0)
    if not step:
        return None

    next_ts = due_ts + step
    while next_ts <= now_ts:
        next_ts += step
    return next_ts


# ---------------------------------------------------------------------------
# Countdown events (shared by CLI and Web UI)
#
# An "event" is a target date you want to be reminded about as it approaches
# (a cruise, a trip, a birthday, a launch). Rather than inventing a second
# scheduler, an event is *expanded* into ordinary `notifications` rows — one
# per future milestone (e.g. 30 days before, 7 days before, the day itself) —
# which the existing send_notifications() loop then delivers across every
# configured channel. The events table stays the editable source of truth;
# the notifications it spawns carry its `event_id` so they can be regenerated
# on edit and cleaned up on delete.
# ---------------------------------------------------------------------------

DEFAULT_MILESTONES = "60,30,14,7,3,1,0"
CADENCE_MILESTONES = "milestones"
CADENCE_DAILY = "daily"
CADENCES = (CADENCE_MILESTONES, CADENCE_DAILY)
DAILY_CADENCE_CAP = 365  # max daily ticks expanded for far-future events


def _normalize_cadence(value) -> str:
    """Coerce any stored/user value to a valid cadence ('milestones' default)."""
    v = (value or "").strip().lower()
    return v if v in CADENCES else CADENCE_MILESTONES


DEFAULT_EVENT_SEND_TIME = "09:00"

# Migrated from cruise-notifier's cruises.json — seeded on first run only.
SEED_EVENTS = [
    # (title, target_date, category, details)
    ("Carnival Celebration", "7/12/26", "cruise", None),
    ("Paying for the Cruise", "4/13/26", "cruise", None),
]


def _parse_event_date(value: str):
    """Parse a calendar date for a countdown event into a `date`.

    Accepts ISO (YYYY-MM-DD) plus the US m/d/yy and m/d/yyyy forms that
    cruise-notifier used, with '-' or '/' separators. Returns None on failure.
    """
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_milestones(csv: str) -> list[int]:
    """Parse a CSV of day-offsets into a sorted, de-duped, descending list.

    Negative values are dropped; 0 (the day itself) is allowed. Falls back to
    the default set when nothing valid is supplied.
    """
    out: set[int] = set()
    for part in (csv or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            v = int(part)
            if v >= 0:
                out.add(v)
    if not out:
        out = {int(x) for x in DEFAULT_MILESTONES.split(",")}
    return sorted(out, reverse=True)


def _event_target_dt(target_date: str, send_time: str) -> Optional[datetime]:
    """Naive local datetime for an event's target date at its send time."""
    d = _parse_event_date(target_date)
    if d is None:
        return None
    try:
        h, m = map(int, (send_time or DEFAULT_EVENT_SEND_TIME).split(":"))
    except (ValueError, AttributeError):
        h, m = 9, 0
    return datetime(d.year, d.month, d.day, h, m)


def days_until(target_date: str) -> Optional[int]:
    """Whole days from today until the target date (negative if past)."""
    d = _parse_event_date(target_date)
    if d is None:
        return None
    return (d - _now_in_tz().date()).days


def format_event_message(title: str, days_left: int, target_date: str,
                         category: Optional[str] = None,
                         details: Optional[str] = None) -> str:
    """Human countdown message for one milestone.

    `days_left` is the milestone offset (60, 30, ... 0). Cruise events get a
    nautical flavour; everything else gets a neutral calendar style.
    """
    cruise = (category or "").lower() == "cruise"
    icon = "🚢" if cruise else "📅"

    if days_left > 1:
        body = f"{days_left} days until {title} on {target_date}"
    elif days_left == 1:
        body = f"Tomorrow is the day — {title} on {target_date}"
    else:
        body = f"Today is the day — {title} on {target_date}"

    msg = f"{icon} {body}!"
    if cruise:
        msg += " Bon voyage! 🌊" if days_left == 0 else " Anchors aweigh! ⚓"
    if details:
        msg += f"\n{details}"
    return msg


def create_event(title: str, target_date: str,
                 category: Optional[str] = None,
                 details: Optional[str] = None,
                 milestones: Optional[str] = None,
                 send_time: Optional[str] = None,
                 cadence: Optional[str] = None) -> Optional[int]:
    """Insert a countdown event and expand it into milestone notifications.

    Returns the new event id, or None if the date could not be parsed.
    """
    d = _parse_event_date(target_date)
    if d is None:
        return None
    send_time = (send_time or DEFAULT_EVENT_SEND_TIME).strip()
    cadence = _normalize_cadence(cadence)
    milestones_csv = ",".join(str(x) for x in _parse_milestones(milestones or DEFAULT_MILESTONES))
    canonical_date = d.strftime("%Y-%m-%d")
    target_dt = _event_target_dt(canonical_date, send_time)
    target_ts = _to_ts(target_dt) if target_dt else 0
    now_ts = int(time.time())

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO events (title, target_date, target_ts, category, details,"
            " milestones, send_time, cadence, created_ts, active)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (title.strip(), canonical_date, target_ts, category or None,
             details or None, milestones_csv, send_time, cadence, now_ts),
        )
        event_id = c.lastrowid
        conn.commit()

    expand_event(event_id)
    return event_id


def get_event(event_id: int) -> Optional[dict]:
    """Return one event as a dict (with computed days_left), or None."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = c.fetchone()
    if not row:
        return None
    event = dict(row)
    event["days_left"] = days_until(event["target_date"])
    return event


def list_events(include_inactive: bool = True) -> list[dict]:
    """Return events ordered by target date, each with a computed days_left
    and a count of its still-pending milestone notifications."""
    query = "SELECT * FROM events"
    if not include_inactive:
        query += " WHERE active = 1"
    query += " ORDER BY target_ts ASC"
    with get_db() as conn:
        c = conn.cursor()
        c.execute(query)
        rows = [dict(r) for r in c.fetchall()]
        for ev in rows:
            ev["days_left"] = days_until(ev["target_date"])
            c.execute(
                "SELECT COUNT(*) FROM notifications WHERE event_id = ? AND sent = 0",
                (ev["id"],),
            )
            ev["pending_milestones"] = c.fetchone()[0]
    return rows


def update_event(event_id: int, **fields) -> bool:
    """Update an event's editable fields and re-expand its milestones.

    Accepts any of: title, target_date, category, details, milestones,
    send_time, cadence, active. Returns False if the event does not exist or a
    supplied date is invalid.
    """
    existing = get_event(event_id)
    if not existing:
        return False

    title = fields.get("title", existing["title"])
    target_date = fields.get("target_date", existing["target_date"])
    category = fields.get("category", existing["category"])
    details = fields.get("details", existing["details"])
    send_time = (fields.get("send_time", existing["send_time"]) or DEFAULT_EVENT_SEND_TIME).strip()
    milestones_csv = ",".join(
        str(x) for x in _parse_milestones(fields.get("milestones", existing["milestones"]))
    )
    cadence = _normalize_cadence(fields.get("cadence", existing.get("cadence")))
    active = int(fields.get("active", existing["active"]))

    d = _parse_event_date(target_date)
    if d is None:
        return False
    canonical_date = d.strftime("%Y-%m-%d")
    target_dt = _event_target_dt(canonical_date, send_time)
    target_ts = _to_ts(target_dt) if target_dt else 0

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE events SET title=?, target_date=?, target_ts=?, category=?,"
            " details=?, milestones=?, send_time=?, cadence=?, active=? WHERE id=?",
            (title.strip(), canonical_date, target_ts, category or None,
             details or None, milestones_csv, send_time, cadence, active, event_id),
        )
        conn.commit()

    expand_event(event_id)
    return True


def delete_event(event_id: int) -> bool:
    """Delete an event and its still-pending milestone notifications.

    Already-sent milestones are kept so the activity log stays intact.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM events WHERE id = ?", (event_id,))
        if not c.fetchone():
            return False
        c.execute("DELETE FROM notifications WHERE event_id = ? AND sent = 0", (event_id,))
        c.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
    return True


def expand_event(event_id: int) -> int:
    """(Re)generate the pending milestone notifications for an event.

    Clears any unsent notifications already linked to the event, then inserts
    one per milestone whose fire time is still in the future. Returns the count
    inserted. Past milestones (and the day-of, if its send time has passed) are
    skipped so we never back-fire on creation or edit.
    """
    event = get_event(event_id)
    if not event:
        return 0

    now_ts = int(time.time())
    offsets = _parse_milestones(event["milestones"])
    target_dt = _event_target_dt(event["target_date"], event["send_time"])
    if target_dt is None:
        return 0

    inserted = 0
    with get_db() as conn:
        c = conn.cursor()
        # Wipe stale pending pings for this event (keeps sent history).
        c.execute("DELETE FROM notifications WHERE event_id = ? AND sent = 0", (event_id,))

        if not event["active"]:
            conn.commit()
            return 0

        for offset in offsets:
            milestone_dt = target_dt - timedelta(days=offset)
            milestone_ts = _to_ts(milestone_dt)
            if milestone_ts <= now_ts:
                continue  # already in the past — don't back-fire
            message = format_event_message(
                event["title"], offset, event["target_date"],
                event["category"], event["details"],
            )
            due_str = milestone_dt.strftime("%Y-%m-%d %H:%M")
            c.execute(
                "INSERT INTO notifications (message, due_time, due_ts, sent,"
                " recurrence, repeat_time, event_id) VALUES (?, ?, ?, 0, NULL, NULL, ?)",
                (message, due_str, milestone_ts, event_id),
            )
            inserted += 1
        conn.commit()
    return inserted


def _seed_events_once() -> None:
    """Seed SEED_EVENTS the first time the events table is used (idempotent).

    Uses a settings flag rather than an empty-table check so that deleting all
    seeded events does not make them reappear on the next startup.
    """
    # Opt-out for tests (and anyone who wants a pristine empty DB).
    if os.getenv("NOTIFIER_SKIP_EVENT_SEED", "").strip().lower() in ("1", "true", "yes"):
        return
    try:
        if get_setting("events_seeded"):
            return
        for title, target_date, category, details in SEED_EVENTS:
            create_event(title, target_date, category=category, details=details)
        set_setting("events_seeded", "1")
    except Exception:
        # Seeding is best-effort; never let it block startup.
        pass
