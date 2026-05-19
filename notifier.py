# -*- coding: utf-8 -*-
# notifier.py — Notification App v2.1.0

import calendar
import random
import sqlite3
import sys
import time
import threading
import os
import smtplib
import logging
import json
import platform
import socket
from contextlib import contextmanager
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from colorama import init, Fore, Style
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import schedule
import requests
from dotenv import load_dotenv, set_key
from pathlib import Path

try:
    import tkinter as _tk_check  # noqa: F401
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

try:
    from plyer import notification
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    notification = None
    NOTIFICATIONS_AVAILABLE = False

# On headless Linux (no DISPLAY / WAYLAND_DISPLAY) plyer spawns notify-send
# which fails with a GDBus D-Bus error.  Disable desktop toasts in that case.
if NOTIFICATIONS_AVAILABLE and sys.platform.startswith("linux"):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        NOTIFICATIONS_AVAILABLE = False

# ── Console encoding guard ────────────────────────────────────────────────────
# Emoji + box-drawing glyphs crash on a non-UTF-8 stdout (Windows cp1252 when
# output is piped or run head-less under Task Scheduler). Force UTF-8 and
# degrade gracefully instead of taking the daemon down on a stray glyph.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── File logging setup (5 MB rotation) ────────────────────────────────────────

LOG_FILE = "multi_channel_notifier.log"
if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5_000_000:
    try:
        os.replace(LOG_FILE, f"multi_channel_notifier.{int(time.time())}.log")
    except Exception:
        pass

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)

init(autoreset=False)  # Colorama — we manage reset manually

ENV_PATH = Path(__file__).parent / '.env'
load_dotenv(str(ENV_PATH))

DB_NAME = "notifications.db"

# ── Database context manager ───────────────────────────────────────────────────

@contextmanager
def get_db():
    """Thread-safe SQLite connection with WAL journal mode."""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the database schema and migrate from older versions."""
    with get_db() as conn:
        c = conn.cursor()

        # Notifications table
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

        # Audit log table
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

        # Indexes for fast queries
        c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(sent, due_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(timestamp)")

        # ── Migrations (v1.0.43 → v2.0.0) ──────────────────────────────────────
        for col, defn in [
            ("due_ts",      "INTEGER DEFAULT 0"),
            ("recurrence",  "TEXT DEFAULT NULL"),
            ("repeat_time", "TEXT DEFAULT NULL"),
        ]:
            try:
                c.execute(f"ALTER TABLE notifications ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass  # column already exists

        # Backfill due_ts for any rows where it is 0 or NULL
        c.execute("SELECT id, due_time FROM notifications WHERE due_ts = 0 OR due_ts IS NULL")
        for row_id, due_str in c.fetchall():
            dt = _parse_due_time(due_str)
            if dt:
                c.execute("UPDATE notifications SET due_ts = ? WHERE id = ?",
                          (_to_ts(dt), row_id))

        conn.commit()

# ── Shared UI helpers ──────────────────────────────────────────────────────────

def _box(color, title, ver_str=None):
    """Print a colored box header. Optionally show a version on the second line."""
    c = color + Style.BRIGHT
    print(f"\n{c}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
    print(f"{c}║  {title:<37}║{Style.RESET_ALL}")
    if ver_str:
        lpad = (39 - len(ver_str)) // 2
        rpad = 39 - len(ver_str) - lpad
        print(f"{c}║{' ' * lpad}{Fore.WHITE}{Style.BRIGHT}{ver_str}{Style.RESET_ALL}{c}{' ' * rpad}║{Style.RESET_ALL}")
    print(f"{c}╚═══════════════════════════════════════╝{Style.RESET_ALL}")


def _div():
    print(f"  {Fore.WHITE}{Style.DIM}{'─' * 39}{Style.RESET_ALL}")


def _prompt(text="Choose: "):
    return input(f"\n  {Fore.GREEN}{Style.BRIGHT}▶  {text}{Style.RESET_ALL}").strip()


def _opt(num, color, emoji, label):
    print(f"  {Fore.YELLOW}{Style.BRIGHT}{num}{Style.RESET_ALL}  {color}{emoji}  {label}{Style.RESET_ALL}")


def masked(val):
    """Safely display a credential value — shows first 3 and last 3 chars."""
    if not val:
        return "(not set)"
    if len(val) <= 6:
        return "******"
    return val[:3] + "..." + val[-3:]


# When the background scheduler fires a send, its stdout would scramble the
# interactive menu the user is sitting in. _QUIET suppresses sender chatter
# during scheduled runs; everything still goes to the log + audit DB.
_QUIET = False


def _cprint(*args, **kwargs):
    """print() that is silenced while the scheduler thread is running a job."""
    if not _QUIET:
        print(*args, **kwargs)

# ── Timezone helpers ───────────────────────────────────────────────────────────

def _get_user_tz():
    """Return a ZoneInfo for the configured TIMEZONE env var, or None for system local."""
    tz_name = os.getenv('TIMEZONE', '').strip()
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        print(f"{Fore.YELLOW}⚠️  Unknown timezone '{tz_name}' — using system local time.{Style.RESET_ALL}")
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

# ── Date / recurrence helpers ──────────────────────────────────────────────────

def _parse_due_time(due_str):
    """Parse a due time string into a datetime. Supports YYYY-MM-DD and MM-DD-YYYY."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%m-%d-%Y %H:%M", "%m-%d-%Y %H:%M:%S"):
        try:
            return datetime.strptime(due_str, fmt)
        except ValueError:
            continue
    return None


def _next_daily_time(time_str):
    """Return the next future datetime for a daily HH:MM schedule."""
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
    """Return the same day next calendar month, clamped to the last day if needed."""
    month = dt.month + 1
    year  = dt.year
    if month > 12:
        month = 1
        year += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _next_recurrence_ts(due_ts, recurrence, repeat_time=None):
    """Compute the next due_ts for a recurring notification. Rolls forward if past."""
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

# ── Audit log ──────────────────────────────────────────────────────────────────

def db_log(notification_id, channel, status, response=None):
    """Write a send-attempt record to the logs table."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO logs (notification_id, timestamp, channel, status, response)"
                " VALUES (?, ?, ?, ?, ?)",
                (notification_id, ts, channel, status, response),
            )
            conn.commit()
        logging.info("%s | %s | %s | nid=%s | %s", ts, channel, status, notification_id, response)
    except Exception as exc:
        logging.error("db_log failed: %s", exc)

# ── Senders (all return (bool, str)) ──────────────────────────────────────────

def send_telegram_message(message):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id   = os.getenv('TELEGRAM_CHAT_ID')
    if not bot_token or not chat_id:
        return False, "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        if r.status_code == 200:
            _cprint(f"{Fore.GREEN}✅ Telegram message sent!{Style.RESET_ALL}")
            return True, f"HTTP {r.status_code}"
        _cprint(f"{Fore.RED}❌ Telegram API error: {r.status_code}{Style.RESET_ALL}")
        return False, f"HTTP {r.status_code} - {r.text}"
    except requests.exceptions.RequestException as e:
        _cprint(f"{Fore.RED}❌ Failed to send Telegram: {e}{Style.RESET_ALL}")
        return False, str(e)


def send_discord_message(message):
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        return False, "Missing DISCORD_WEBHOOK_URL"
    try:
        r = requests.post(webhook_url, json={"content": message}, timeout=10)
        if r.status_code in (200, 204):
            _cprint(f"{Fore.GREEN}✅ Discord message sent!{Style.RESET_ALL}")
            return True, f"HTTP {r.status_code}"
        _cprint(f"{Fore.RED}❌ Discord API error: {r.status_code}{Style.RESET_ALL}")
        return False, f"HTTP {r.status_code} - {r.text}"
    except requests.exceptions.RequestException as e:
        _cprint(f"{Fore.RED}❌ Failed to send Discord: {e}{Style.RESET_ALL}")
        return False, str(e)


def send_pushover_message(message):
    user_key  = os.getenv('PUSHOVER_USER_KEY')
    api_token = os.getenv('PUSHOVER_API_TOKEN')
    if not user_key or not api_token:
        return False, "Missing PUSHOVER_USER_KEY or PUSHOVER_API_TOKEN"
    url = "https://api.pushover.net/1/messages.json"
    try:
        r = requests.post(url, data={"token": api_token, "user": user_key, "message": message}, timeout=10)
        if r.status_code == 200:
            _cprint(f"{Fore.GREEN}✅ Pushover message sent!{Style.RESET_ALL}")
            return True, f"HTTP {r.status_code}"
        _cprint(f"{Fore.RED}❌ Pushover API error: {r.status_code}{Style.RESET_ALL}")
        return False, f"HTTP {r.status_code} - {r.text}"
    except requests.exceptions.RequestException as e:
        _cprint(f"{Fore.RED}❌ Failed to send Pushover: {e}{Style.RESET_ALL}")
        return False, str(e)


def send_email_message(message, subject="⏰ Notification Reminder"):
    smtp_server = os.getenv('EMAIL_SMTP_SERVER', 'smtp.gmail.com')
    smtp_port   = int(os.getenv('EMAIL_SMTP_PORT', '587'))
    sender      = os.getenv('EMAIL_SENDER', '')
    password    = os.getenv('EMAIL_PASSWORD', '')
    recipient   = os.getenv('EMAIL_RECIPIENT', '')
    if not all([sender, password, recipient]):
        return False, "Missing EMAIL_SENDER or EMAIL_PASSWORD or EMAIL_RECIPIENT"
    try:
        msg = MIMEMultipart()
        msg['From']    = sender
        msg['To']      = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(message, 'plain'))
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        _cprint(f"{Fore.GREEN}✅ Email sent!{Style.RESET_ALL}")
        return True, "Email sent"
    except Exception as e:
        _cprint(f"{Fore.RED}❌ Failed to send email: {e}{Style.RESET_ALL}")
        return False, str(e)

# ── Verify functions ───────────────────────────────────────────────────────────

def verify_telegram_config():
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id   = os.getenv('TELEGRAM_CHAT_ID')
    if not bot_token or not chat_id:
        print(f"{Fore.RED}❌ Telegram not configured!{Style.RESET_ALL}")
        return False
    print(f"{Fore.CYAN}ℹ️  Verifying Telegram...{Style.RESET_ALL}")
    try:
        r = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
        if r.status_code == 200 and r.json().get('ok'):
            bot_name = r.json()['result'].get('username', 'Unknown')
            print(f"{Fore.GREEN}✅ Bot valid! Username: @{bot_name}{Style.RESET_ALL}")
            ok, _ = send_telegram_message("✅ Telegram verification successful!")
            return ok
        print(f"{Fore.RED}❌ Bot token is invalid!{Style.RESET_ALL}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}❌ Connection failed: {e}{Style.RESET_ALL}")
        return False


def verify_discord_config():
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        print(f"{Fore.RED}❌ Discord not configured!{Style.RESET_ALL}")
        return False
    print(f"{Fore.CYAN}ℹ️  Verifying Discord webhook...{Style.RESET_ALL}")
    ok, _ = send_discord_message("✅ Discord verification successful!")
    return ok


def verify_pushover_config():
    user_key  = os.getenv('PUSHOVER_USER_KEY')
    api_token = os.getenv('PUSHOVER_API_TOKEN')
    if not user_key or not api_token:
        print(f"{Fore.RED}❌ Pushover not configured!{Style.RESET_ALL}")
        return False
    print(f"{Fore.CYAN}ℹ️  Verifying Pushover...{Style.RESET_ALL}")
    ok, _ = send_pushover_message("✅ Pushover verification successful!")
    return ok


def verify_email_config():
    smtp_server = os.getenv('EMAIL_SMTP_SERVER', 'smtp.gmail.com')
    smtp_port   = os.getenv('EMAIL_SMTP_PORT', '587')
    sender      = os.getenv('EMAIL_SENDER')
    recipient   = os.getenv('EMAIL_RECIPIENT')
    if not all([sender, os.getenv('EMAIL_PASSWORD'), recipient]):
        print(f"{Fore.RED}❌ Gmail not configured!{Style.RESET_ALL}")
        return False
    print(f"{Fore.CYAN}ℹ️  Verifying Gmail...{Style.RESET_ALL}")
    print(f"{Fore.WHITE}SMTP: {smtp_server}:{smtp_port}  From: {sender}  To: {recipient}{Style.RESET_ALL}")
    ok, _ = send_email_message("✅ Gmail verification successful!")
    return ok

# ── Channel registry ───────────────────────────────────────────────────────────
# One row per delivery channel. Adding a channel is now a data change here plus
# a send_/verify_ pair — no more parallel menu/setter/loop copies to keep in sync.

CHANNELS = [
    {
        "name": "telegram", "label": "Telegram", "emoji": "📱", "color": Fore.BLUE,
        "send": send_telegram_message, "verify": verify_telegram_config,
        "fields": [
            {"key": "TELEGRAM_BOT_TOKEN", "prompt": "Bot Token", "secret": False, "mask": True},
            {"key": "TELEGRAM_CHAT_ID",   "prompt": "Chat ID",   "secret": False, "mask": False},
        ],
        "display_extra": [],
        "setup": [
            "1. Message @BotFather on Telegram",
            "2. Send /newbot and follow instructions",
            "3. Get your bot token",
            "4. Message @userinfobot to get your chat ID",
            "5. Add both to .env file",
        ],
    },
    {
        "name": "discord", "label": "Discord", "emoji": "💬", "color": Fore.MAGENTA,
        "send": send_discord_message, "verify": verify_discord_config,
        "fields": [
            {"key": "DISCORD_WEBHOOK_URL", "prompt": "Webhook URL", "secret": False, "mask": True},
        ],
        "display_extra": [],
        "setup": [
            "1. Go to your Discord server",
            "2. Edit Channel → Integrations → Webhooks",
            "3. Create New Webhook and copy the URL",
            "4. Add DISCORD_WEBHOOK_URL to .env file",
        ],
    },
    {
        "name": "pushover", "label": "Pushover", "emoji": "📲", "color": Fore.YELLOW,
        "send": send_pushover_message, "verify": verify_pushover_config,
        "fields": [
            {"key": "PUSHOVER_USER_KEY",  "prompt": "User Key",  "secret": False, "mask": True},
            {"key": "PUSHOVER_API_TOKEN", "prompt": "API Token", "secret": False, "mask": True},
        ],
        "display_extra": [],
        "setup": [
            "1. Go to https://pushover.net",
            "2. Create an account and note your User Key",
            "3. Create an Application to get an API Token",
            "4. Add both to .env file",
        ],
    },
    {
        "name": "email", "label": "Gmail", "emoji": "📧", "color": Fore.RED,
        "send": send_email_message, "verify": verify_email_config,
        "fields": [
            {"key": "EMAIL_SENDER",    "prompt": "Sender Email",    "secret": False, "mask": True},
            {"key": "EMAIL_PASSWORD",  "prompt": "App Password",    "secret": True,  "mask": True},
            {"key": "EMAIL_RECIPIENT", "prompt": "Recipient Email", "secret": False, "mask": True},
        ],
        "display_extra": [("EMAIL_SMTP_SERVER", "smtp.gmail.com"), ("EMAIL_SMTP_PORT", "587")],
        "setup": [
            "1. Go to Google Account → Security",
            "2. Enable 2-Step Verification",
            "3. Go to App Passwords → Generate one for 'Mail'",
            "4. Use that password (NOT your regular password)",
            "5. Add all EMAIL_* variables to .env file",
        ],
    },
]


def _channel_configured(ch) -> bool:
    """True when every required env field for this channel is set."""
    return all(os.getenv(f["key"]) for f in ch["fields"])


def _is_transient(resp: str) -> bool:
    """Heuristic: is this failure worth retrying?

    Retry network blips, timeouts, 5xx and 429. Do NOT retry a missing-config
    skip or a hard 4xx (bad token / forbidden) — those won't fix themselves
    inside a few seconds and would just delay the other channels.
    """
    if not resp:
        return False
    r = resp.lower()
    if "missing" in r:
        return False
    if "http 429" in r or "http 5" in r:
        return True
    if "http 4" in r:                      # 4xx other than 429 → permanent
        return False
    # No HTTP code → transport-level error (timeout, conn reset, DNS, SMTP)
    return True


def _deliver(chan, message, subject=None, retries=2, backoff=1.5):
    """Send through one channel with bounded retry on transient failures.

    The single chokepoint for delivery so retry/backoff lives in exactly one
    place. Only the email sender takes a subject; everything else ignores it.
    Returns (ok, response) — response is from the final attempt.
    """
    send = chan["send"]

    def _attempt():
        if chan["name"] == "email" and subject is not None:
            return send(message, subject=subject)
        return send(message)

    ok, resp = _attempt()
    attempt = 1
    while not ok and attempt <= retries and _is_transient(resp):
        delay = backoff * attempt
        logging.warning("Channel %s failed (%s) — retry %d/%d in %.1fs",
                         chan["name"], resp, attempt, retries, delay)
        time.sleep(delay)
        ok, resp = _attempt()
        attempt += 1
    return ok, resp

# ── Heartbeat ──────────────────────────────────────────────────────────────────

def send_heartbeat():
    """Send a heartbeat ping to all configured services with enriched system info."""
    now  = _now_in_tz()
    host = socket.gethostname()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "unknown"

    msg = (
        f"💓 Heartbeat — {now.strftime('%Y-%m-%d %H:%M')} ({_tz_label()})\n"
        f"🖥️ {host} | 🌐 {ip} | 🐍 Python {platform.python_version()}"
    )
    logging.info("Heartbeat: %s", msg)

    _notify = getattr(notification, "notify", None) if notification is not None else None
    if NOTIFICATIONS_AVAILABLE and callable(_notify):
        try:
            _notify(title="💓 Heartbeat", message="Notifier is running", timeout=5)
        except Exception:
            pass

    # Check whether any external service is configured
    any_configured = any(_channel_configured(ch) for ch in CHANNELS)

    if not any_configured:
        db_log(None, "heartbeat", "LOGGED", "No services configured — heartbeat logged only")
        logging.info("Heartbeat logged only — no notification services configured")
        return

    _cprint(f"{Fore.MAGENTA}💓 Sending heartbeat...{Style.RESET_ALL}")
    for chan in CHANNELS:
        fn, ch = chan["send"], chan["name"]
        ok, resp = fn(msg)
        status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
        db_log(None, f"heartbeat_{ch}", status, resp)

# ── Admin notification ─────────────────────────────────────────────────────────

def send_admin_notification(message, include_system_info=False):
    """Optional admin Telegram alert (requires TELEGRAM_ADMIN_BOT_TOKEN)."""
    admin_token = os.getenv("TELEGRAM_ADMIN_BOT_TOKEN")
    admin_chat  = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not admin_token or not admin_chat:
        return False, "Admin Telegram not configured"

    if include_system_info:
        host = socket.gethostname()
        internal_ip = "Unknown"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            internal_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        message += (
            f"\n🖥️ Host: {host}"
            f"\n🌐 IP: {internal_ip}"
            f"\n💻 OS: {platform.system()} {platform.version()}"
            f"\n🐍 Python: {platform.python_version()}"
        )

    url = f"https://api.telegram.org/bot{admin_token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": admin_chat, "text": message}, timeout=10)
        if r.status_code == 200:
            return True, "Sent"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)

# ── Service menus (v1.0.43 5-option style) ────────────────────────────────────

def _service_menu_options(prompt_color):
    """Print the standard 5-option service sub-menu and return the user's choice."""
    _opt("1", Fore.GREEN + Style.BRIGHT,   "✅", "Verify Configuration")
    _opt("2", Fore.BLUE  + Style.BRIGHT,   "✏️ ", "Set Credentials")
    _opt("3", Fore.CYAN,                    "📤", "Send Test Message")
    _opt("4", Fore.WHITE + Style.DIM,       "📋", "Show .env Variables")
    _opt("5", Fore.WHITE + Style.DIM,       "ℹ️ ", "Setup Instructions")
    _div()
    _opt("0", Fore.RED   + Style.DIM,       "⬅️ ", "Back")
    return _prompt("Choose: ")


def channel_menu(chan):
    """Generic per-channel settings screen. Behaviour matches the old
    telegram_menu/discord_menu/… one-to-one, driven by the registry row."""
    while True:
        _box(chan["color"], f'{chan["emoji"]} {chan["label"].upper()} SETTINGS')
        configured = _channel_configured(chan)
        status = (f"{Fore.GREEN}{Style.BRIGHT}✅ CONFIGURED{Style.RESET_ALL}"
                  if configured else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}")
        print(f"  Status: {status}\n")
        choice = _service_menu_options(chan["color"])
        if choice == "1":
            chan["verify"]()
        elif choice == "2":
            set_channel_credentials(chan)
        elif choice == "3":
            msg = _prompt("Test message (Enter for default): ") or "🧪 Test from Notification App!"
            _deliver(chan, msg)
        elif choice == "4":
            print()
            for key, default in chan["display_extra"]:
                print(f"  {Fore.GREEN}{key}={os.getenv(key, default)}{Style.RESET_ALL}")
            for f in chan["fields"]:
                val = os.getenv(f["key"])
                shown = masked(val) if f["mask"] else (val or "(not set)")
                print(f"  {Fore.GREEN}{f['key']}={shown}{Style.RESET_ALL}")
        elif choice == "5":
            print(f"\n  {Fore.CYAN}{Style.BRIGHT}📚 {chan['label']} Setup:{Style.RESET_ALL}")
            for line in chan["setup"]:
                print(f"  {line}")
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "0":
            break


def notification_services_menu():
    while True:
        _box(Fore.MAGENTA, "📬 NOTIFICATION SERVICES")
        for i, chan in enumerate(CHANNELS, start=1):
            ok = _channel_configured(chan)
            tick = f"{Fore.GREEN}✅{Style.RESET_ALL}" if ok else f"{Fore.RED}❌{Style.RESET_ALL}"
            clr  = Fore.WHITE if ok else Fore.WHITE + Style.DIM
            print(f"  {Fore.YELLOW}{Style.BRIGHT}{i}{Style.RESET_ALL}  {tick} "
                  f"{clr}{chan['emoji']}  {chan['label']}{Style.RESET_ALL}")
        _div()
        env_opt = str(len(CHANNELS) + 1)
        _opt(env_opt, Fore.WHITE + Style.DIM, "📋", "Show Complete .env Example")
        _opt("0", Fore.RED + Style.DIM, "⬅️ ", "Back to Main Menu")

        choice = _prompt("Choose: ")
        if choice == "0":
            break
        elif choice == env_opt:
            show_complete_env_example()
        elif choice.isdigit() and 1 <= int(choice) <= len(CHANNELS):
            channel_menu(CHANNELS[int(choice) - 1])


def show_complete_env_example():
    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}📄  COMPLETE .env FILE EXAMPLE{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}\n")
    print(f"  {Fore.YELLOW}# Telegram{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUV{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}TELEGRAM_CHAT_ID=987654321{Style.RESET_ALL}\n")
    print(f"  {Fore.YELLOW}# Discord{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...{Style.RESET_ALL}\n")
    print(f"  {Fore.YELLOW}# Pushover{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}PUSHOVER_USER_KEY=your_user_key_here{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}PUSHOVER_API_TOKEN=your_api_token_here{Style.RESET_ALL}\n")
    print(f"  {Fore.YELLOW}# Gmail{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}EMAIL_SMTP_SERVER=smtp.gmail.com{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}EMAIL_SMTP_PORT=587{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}EMAIL_SENDER=your_email@gmail.com{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}EMAIL_PASSWORD=your_app_password{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}EMAIL_RECIPIENT=recipient@email.com{Style.RESET_ALL}\n")
    print(f"  {Fore.YELLOW}# Timezone (optional — leave blank for system local){Style.RESET_ALL}")
    print(f"  {Fore.GREEN}TIMEZONE=America/New_York{Style.RESET_ALL}\n")
    print(f"  {Fore.YELLOW}# Heartbeat — 1=on, 0=off (legacy HEARTBEAT_INTERVAL still honored){Style.RESET_ALL}")
    print(f"  {Fore.GREEN}HEARTBEAT_ENABLED=1{Style.RESET_ALL}\n")
    print(f"  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}")
    input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")

# ── Credential helpers ─────────────────────────────────────────────────────────

def _set_credential(key, prompt_text, secret=False):
    """Prompt for a value, write to .env, and update the running session."""
    if secret:
        import getpass
        value = getpass.getpass(f"  {Fore.YELLOW}▶  {prompt_text} (hidden): {Style.RESET_ALL}").strip()
    else:
        value = input(f"  {Fore.YELLOW}▶  {prompt_text}: {Style.RESET_ALL}").strip()
    if not value:
        print(f"{Fore.YELLOW}⚠️  Skipped — value unchanged.{Style.RESET_ALL}")
        return False
    set_key(str(ENV_PATH), key, value)
    os.environ[key] = value
    print(f"{Fore.GREEN}✅ {key} saved.{Style.RESET_ALL}")
    return True


def set_channel_credentials(chan):
    """Generic credential entry for any registry channel."""
    print(f"\n{chan['color']}{Style.BRIGHT}{chan['emoji']} Enter {chan['label']} Credentials{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{Style.DIM}Press Enter to skip a field and keep its current value.{Style.RESET_ALL}\n")
    for f in chan["fields"]:
        _set_credential(f["key"], f["prompt"], secret=f["secret"])
    load_dotenv(str(ENV_PATH), override=True)

# ── CRUD ───────────────────────────────────────────────────────────────────────

def add_notification():
    _box(Fore.GREEN, "➕ ADD NOTIFICATION")
    print(f"  {Fore.YELLOW}▶  Enter message: {Style.RESET_ALL}", end="")
    msg = input().strip()
    if not msg:
        print(f"{Fore.RED}❌ Message cannot be empty!{Style.RESET_ALL}")
        return
    if len(msg) > 4000:
        print(f"{Fore.RED}❌ Message exceeds 4000 characters.{Style.RESET_ALL}")
        return

    recurrence  = None
    repeat_time = None

    repeat_choice = _prompt("Repeat? (y/N): ").lower()
    if repeat_choice == 'y':
        print(f"\n  {Fore.CYAN}Repeat type:{Style.RESET_ALL}")
        _opt("1", Fore.WHITE, "📆", "Daily (at a specific time)")
        _opt("2", Fore.WHITE, "📅", "Weekly")
        _opt("3", Fore.WHITE, "🗓️ ", "Biweekly")
        _opt("4", Fore.WHITE, "📅", "Monthly")
        rtype = _prompt("Choose: ")

        if rtype == "1":
            recurrence = "daily"
            print(f"  {Fore.YELLOW}▶  Time of day ({_tz_label()}, e.g., '09:00'): {Style.RESET_ALL}", end="")
            time_raw = input().strip()
            try:
                datetime.strptime(time_raw, "%H:%M")
            except ValueError:
                print(f"{Fore.RED}❌ Invalid time format! Use HH:MM{Style.RESET_ALL}")
                return
            repeat_time = time_raw
            due_dt = _next_daily_time(repeat_time)
            if due_dt is None:
                print(f"{Fore.RED}❌ Could not compute next occurrence.{Style.RESET_ALL}")
                return
            due = due_dt.strftime("%Y-%m-%d %H:%M")
            due_ts = _to_ts(due_dt)
            print(f"  {Fore.CYAN}First occurrence: {Fore.WHITE}{Style.BRIGHT}{due} ({_tz_label()}){Style.RESET_ALL}")

        elif rtype in ("2", "3", "4"):
            recurrence = {"2": "weekly", "3": "biweekly", "4": "monthly"}[rtype]
            now = _now_in_tz()
            print(f"  {Fore.YELLOW}▶  First due time ({_tz_label()}, e.g., '{now.strftime('%Y-%m-%d')} 14:00'): {Style.RESET_ALL}", end="")
            due_raw = input().strip()
            due_dt = _parse_due_time(due_raw)
            if due_dt is None:
                print(f"{Fore.RED}❌ Invalid date format! Use YYYY-MM-DD HH:MM{Style.RESET_ALL}")
                return
            if due_dt <= now:
                print(f"{Fore.RED}❌ Due time is in the past!{Style.RESET_ALL}")
                return
            due = due_dt.strftime("%Y-%m-%d %H:%M")
            due_ts = _to_ts(due_dt)
        else:
            print(f"{Fore.RED}❌ Invalid choice.{Style.RESET_ALL}")
            return
    else:
        now = _now_in_tz()
        print(f"  {Fore.YELLOW}▶  Due time ({_tz_label()}, e.g., '{now.strftime('%Y-%m-%d')} 14:00'): {Style.RESET_ALL}", end="")
        due_raw = input().strip()
        due_dt = _parse_due_time(due_raw)
        if due_dt is None:
            print(f"{Fore.RED}❌ Invalid date format! Use YYYY-MM-DD HH:MM{Style.RESET_ALL}")
            return
        if due_dt <= now:
            print(f"{Fore.RED}❌ {due_dt.strftime('%Y-%m-%d %H:%M')} is in the past!{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}   Current time ({_tz_label()}): {now.strftime('%Y-%m-%d %H:%M')} — enter a future date/time.{Style.RESET_ALL}")
            return
        due = due_dt.strftime("%Y-%m-%d %H:%M")
        due_ts = _to_ts(due_dt)

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO notifications (message, due_time, due_ts, recurrence, repeat_time)"
            " VALUES (?, ?, ?, ?, ?)",
            (msg, due, due_ts, recurrence, repeat_time),
        )
        nid = c.lastrowid
        conn.commit()

    db_log(nid, "system", "CREATED",
           f"Added: {msg} | due={due} | recurrence={recurrence} | repeat_time={repeat_time}")

    if repeat_time:
        print(f"{Fore.GREEN}✅ Added ID:{nid} | Daily at {repeat_time} | Next: {due}{Style.RESET_ALL}")
    elif recurrence:
        print(f"{Fore.GREEN}✅ Added ID:{nid} | {recurrence.title()} | First: {due}{Style.RESET_ALL}")
    else:
        print(f"{Fore.GREEN}✅ Added ID:{nid} | Due: {due}{Style.RESET_ALL}")


def view_notifications():
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, message, due_time, sent, recurrence, repeat_time, due_ts"
            " FROM notifications ORDER BY due_ts"
        )
        rows = c.fetchall()

    if not rows:
        print(f"{Fore.YELLOW}⚠️  No notifications scheduled.{Style.RESET_ALL}")
        return

    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}")
    for nid, msg, due_time, sent, recurrence, repeat_time, due_ts in rows:
        status = (f"{Fore.GREEN}{Style.BRIGHT}✅ SENT{Style.RESET_ALL}"
                  if sent else f"{Fore.YELLOW}⏳ PENDING{Style.RESET_ALL}")
        rel = "" if sent else f"  {Fore.WHITE}{Style.DIM}({_relative_due(due_ts)}){Style.RESET_ALL}"
        print(f"  {Fore.YELLOW}{Style.BRIGHT}#{nid}{Style.RESET_ALL}  {Fore.WHITE}{Style.BRIGHT}{msg}{Style.RESET_ALL}")
        print(f"     {Fore.WHITE}{Style.DIM}Due:{Style.RESET_ALL} {Fore.CYAN}{due_time}{Style.RESET_ALL}  {status}{rel}")
        if recurrence:
            repeat_label = f" at {repeat_time}" if repeat_time else ""
            print(f"     {Fore.WHITE}{Style.DIM}Repeat:{Style.RESET_ALL} {Fore.MAGENTA}{recurrence.title()}{repeat_label}{Style.RESET_ALL}")
        print(f"  {Fore.WHITE}{Style.DIM}{'─'*39}{Style.RESET_ALL}")


def snooze_notification(nid, minutes) -> bool:
    """Push a notification's due time out by N minutes and re-arm it."""
    try:
        nid = int(nid)
        minutes = int(minutes)
    except (TypeError, ValueError):
        print(f"{Fore.RED}❌ ID and minutes must be integers.{Style.RESET_ALL}")
        return False
    if minutes <= 0:
        print(f"{Fore.RED}❌ Minutes must be positive.{Style.RESET_ALL}")
        return False
    new_ts = int(time.time()) + minutes * 60
    new_due = _from_ts(new_ts).strftime("%Y-%m-%d %H:%M")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM notifications WHERE id = ?", (nid,))
        if not c.fetchone():
            print(f"{Fore.RED}❌ Notification ID {nid} not found!{Style.RESET_ALL}")
            return False
        c.execute("UPDATE notifications SET due_ts=?, due_time=?, sent=0 WHERE id=?",
                  (new_ts, new_due, nid))
        conn.commit()
    db_log(nid, "system", "SNOOZED", f"Snoozed {minutes}m → {new_due}")
    print(f"{Fore.GREEN}✅ ID {nid} snoozed {minutes}m → {new_due} ({_tz_label()}){Style.RESET_ALL}")
    return True


def delete_notification():
    print(f"  {Fore.YELLOW}▶  Notification ID to delete: {Style.RESET_ALL}", end="")
    notif_id = input().strip()
    if not notif_id.isdigit():
        print(f"{Fore.RED}❌ Invalid ID!{Style.RESET_ALL}")
        return
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM notifications WHERE id = ?", (notif_id,))
        if not c.fetchone():
            print(f"{Fore.RED}❌ Notification ID {notif_id} not found!{Style.RESET_ALL}")
            return
        c.execute("DELETE FROM notifications WHERE id = ?", (notif_id,))
        conn.commit()
    db_log(int(notif_id), "system", "DELETED", f"Notification ID {notif_id} deleted")
    print(f"{Fore.GREEN}✅ Deleted notification ID {notif_id}!{Style.RESET_ALL}")


def edit_notification():
    print(f"  {Fore.YELLOW}▶  Notification ID to edit: {Style.RESET_ALL}", end="")
    notif_id = input().strip()
    if not notif_id.isdigit():
        print(f"{Fore.RED}❌ Invalid ID!{Style.RESET_ALL}")
        return

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, message, due_time, recurrence, repeat_time FROM notifications WHERE id = ?",
            (notif_id,),
        )
        row = c.fetchone()
        if not row:
            print(f"{Fore.RED}❌ Notification ID {notif_id} not found!{Style.RESET_ALL}")
            return

        nid, msg, due_time, recurrence, repeat_time = row
        print(f"  {Fore.CYAN}Current message:   {Fore.WHITE}{Style.BRIGHT}{msg}{Style.RESET_ALL}")
        if recurrence and repeat_time:
            print(f"  {Fore.CYAN}Schedule:          {Fore.WHITE}{Style.BRIGHT}{recurrence.title()} at {repeat_time}{Style.RESET_ALL}")
        elif recurrence:
            print(f"  {Fore.CYAN}Schedule:          {Fore.WHITE}{Style.BRIGHT}{recurrence.title()}{Style.RESET_ALL}")
        print(f"  {Fore.CYAN}Next/Due time:     {Fore.WHITE}{Style.BRIGHT}{due_time}{Style.RESET_ALL}")

        print(f"  {Fore.YELLOW}▶  New message (Enter to keep): {Style.RESET_ALL}", end="")
        new_msg        = input().strip() or msg
        new_due        = due_time
        new_due_ts     = None
        new_recurrence = recurrence
        new_repeat_time = repeat_time

        if recurrence:
            print(f"\n  {Fore.CYAN}Edit schedule?{Style.RESET_ALL}")
            _opt("1", Fore.WHITE + Style.DIM, "↩️ ", "Keep recurring (change time only)")
            _opt("2", Fore.WHITE + Style.DIM, "1️⃣ ", "Convert to one-time")
            _opt("0", Fore.WHITE + Style.DIM, "⏭️ ", "Keep everything as-is")
            sched_edit = _prompt("Choose: ")
            if sched_edit == "1":
                if recurrence == "daily":
                    print(f"  {Fore.YELLOW}▶  New time of day (Enter to keep '{repeat_time}'): {Style.RESET_ALL}", end="")
                    t_raw = input().strip()
                    if t_raw:
                        try:
                            datetime.strptime(t_raw, "%H:%M")
                            new_repeat_time = t_raw
                            next_dt = _next_daily_time(new_repeat_time)
                            if next_dt:
                                new_due = next_dt.strftime("%Y-%m-%d %H:%M")
                                new_due_ts = _to_ts(next_dt)
                        except ValueError:
                            print(f"{Fore.RED}❌ Invalid format. Keeping original.{Style.RESET_ALL}")
                else:
                    print(f"  {Fore.YELLOW}▶  New first due time (Enter to keep '{due_time}'): {Style.RESET_ALL}", end="")
                    due_raw = input().strip()
                    if due_raw:
                        due_dt = _parse_due_time(due_raw)
                        if due_dt and due_dt > _now_in_tz():
                            new_due = due_dt.strftime("%Y-%m-%d %H:%M")
                            new_due_ts = _to_ts(due_dt)
                        else:
                            print(f"{Fore.RED}❌ Invalid or past date. Keeping original.{Style.RESET_ALL}")
            elif sched_edit == "2":
                now = _now_in_tz()
                print(f"  {Fore.YELLOW}▶  Due time ({_tz_label()}, e.g., '{now.strftime('%Y-%m-%d')} 14:00'): {Style.RESET_ALL}", end="")
                due_raw = input().strip()
                due_dt = _parse_due_time(due_raw)
                if due_dt is None:
                    print(f"{Fore.RED}❌ Invalid format! Keeping original.{Style.RESET_ALL}")
                elif due_dt <= now:
                    print(f"{Fore.RED}❌ Time is in the past! Keeping original.{Style.RESET_ALL}")
                else:
                    new_due = due_dt.strftime("%Y-%m-%d %H:%M")
                    new_due_ts = _to_ts(due_dt)
                    new_recurrence  = None
                    new_repeat_time = None
        else:
            print(f"\n  {Fore.CYAN}Edit schedule?{Style.RESET_ALL}")
            _opt("1", Fore.WHITE + Style.DIM,    "↩️ ", "Keep one-time (change due date only)")
            _opt("2", Fore.GREEN + Style.BRIGHT,  "🔁", "Convert to recurring")
            _opt("0", Fore.WHITE + Style.DIM,     "⏭️ ", "Keep everything as-is")
            sched_edit = _prompt("Choose: ")
            if sched_edit == "1":
                print(f"  {Fore.YELLOW}▶  New due time (Enter to keep '{due_time}'): {Style.RESET_ALL}", end="")
                due_raw = input().strip()
                if due_raw:
                    due_dt = _parse_due_time(due_raw)
                    if due_dt is None:
                        print(f"{Fore.RED}❌ Invalid format! Keeping original.{Style.RESET_ALL}")
                    elif due_dt <= _now_in_tz():
                        print(f"{Fore.RED}❌ Time is in the past! Keeping original.{Style.RESET_ALL}")
                    else:
                        new_due = due_dt.strftime("%Y-%m-%d %H:%M")
                        new_due_ts = _to_ts(due_dt)
            elif sched_edit == "2":
                print(f"\n  {Fore.CYAN}Repeat type:{Style.RESET_ALL}")
                _opt("1", Fore.WHITE, "📆", "Daily (at a specific time)")
                _opt("2", Fore.WHITE, "📅", "Weekly")
                _opt("3", Fore.WHITE, "🗓️ ", "Biweekly")
                _opt("4", Fore.WHITE, "📅", "Monthly")
                rtype = _prompt("Choose: ")
                if rtype == "1":
                    new_recurrence = "daily"
                    print(f"  {Fore.YELLOW}▶  Time of day (e.g., '09:00'): {Style.RESET_ALL}", end="")
                    t_raw = input().strip()
                    try:
                        datetime.strptime(t_raw, "%H:%M")
                        new_repeat_time = t_raw
                        next_dt = _next_daily_time(new_repeat_time)
                        if next_dt:
                            new_due = next_dt.strftime("%Y-%m-%d %H:%M")
                            new_due_ts = _to_ts(next_dt)
                    except ValueError:
                        print(f"{Fore.RED}❌ Invalid format! Keeping original.{Style.RESET_ALL}")
                        new_recurrence = None
                elif rtype in ("2", "3", "4"):
                    new_recurrence = {"2": "weekly", "3": "biweekly", "4": "monthly"}[rtype]

        if new_due_ts is None:
            dt = _parse_due_time(new_due)
            new_due_ts = _to_ts(dt) if dt else 0

        c.execute(
            "UPDATE notifications SET message=?, due_time=?, due_ts=?, sent=0, recurrence=?, repeat_time=?"
            " WHERE id=?",
            (new_msg, new_due, new_due_ts, new_recurrence, new_repeat_time, notif_id),
        )
        conn.commit()

    db_log(int(notif_id), "system", "EDITED", f"Updated ID {notif_id}")
    print(f"{Fore.GREEN}✅ Updated notification ID {notif_id}!{Style.RESET_ALL}")

# ── Send notifications (epoch-based, db_log, ≥1-success logic) ────────────────

def send_notifications(verbose=False, only_id=None):
    """Deliver due notifications. With only_id, force-send that one regardless
    of its due time / sent flag (used by 'send now for ID' and --send-id)."""
    now_ts = int(time.time())
    with get_db() as conn:
        c = conn.cursor()
        if only_id is not None:
            c.execute(
                "SELECT id, message, due_ts, recurrence, repeat_time"
                " FROM notifications WHERE id = ?",
                (only_id,),
            )
        else:
            c.execute(
                "SELECT id, message, due_ts, recurrence, repeat_time"
                " FROM notifications WHERE sent=0 AND due_ts <= ?",
                (now_ts,),
            )
        pending = c.fetchall()

        if not pending:
            if verbose:
                msg = (f"❌ No notification with ID {only_id}." if only_id is not None
                       else "⚠️  No due notifications right now.")
                _cprint(f"{Fore.YELLOW}{msg}{Style.RESET_ALL}")
            return

        for nid, msg, orig_due_ts, recurrence, repeat_time in pending:
            _cprint(f"{Fore.GREEN}{Style.BRIGHT}📢 Sending: {msg}{Style.RESET_ALL}")

            # Desktop notification
            _notify = getattr(notification, "notify", None) if notification is not None else None
            if NOTIFICATIONS_AVAILABLE and callable(_notify):
                try:
                    _notify(title="⏰ Reminder!", message=msg, timeout=10)
                except Exception:
                    pass

            any_success = False
            full_msg    = f"⏰ Reminder: {msg}"

            for chan in CHANNELS:
                ok, resp = _deliver(chan, full_msg, subject="⏰ Reminder")
                status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
                db_log(nid, chan["name"], status, resp)
                if ok:
                    any_success = True

            if any_success:
                c.execute("UPDATE notifications SET sent=1 WHERE id=?", (nid,))
                if recurrence:
                    next_ts = _next_recurrence_ts(orig_due_ts, recurrence, repeat_time)
                    if next_ts:
                        next_due_str = _from_ts(next_ts).strftime("%Y-%m-%d %H:%M")
                        c.execute(
                            "INSERT INTO notifications (message, due_time, due_ts, recurrence, repeat_time)"
                            " VALUES (?, ?, ?, ?, ?)",
                            (msg, next_due_str, next_ts, recurrence, repeat_time),
                        )
                        _cprint(f"{Fore.CYAN}🔁 Next {recurrence}: {next_due_str}{Style.RESET_ALL}")
                conn.commit()
                _cprint(f"{Fore.GREEN}✅ ID {nid} marked sent.{Style.RESET_ALL}")
            else:
                conn.commit()
                _cprint(f"{Fore.RED}❌ ID {nid} not marked sent — no channel succeeded.{Style.RESET_ALL}")

        _cprint(f"{Fore.GREEN}✅ Processed {len(pending)} notification(s).{Style.RESET_ALL}")

# ── Logs ───────────────────────────────────────────────────────────────────────

def show_logs(limit=100):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, notification_id, timestamp, channel, status, response"
            " FROM logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = c.fetchall()

    if not rows:
        print(f"{Fore.YELLOW}⚠️  No logs found.{Style.RESET_ALL}")
        return

    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'═'*65}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}📜  LAST {limit} LOG ENTRIES{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}{'═'*65}{Style.RESET_ALL}\n")
    for log_id, nid, ts, ch, status, response in rows:
        nid_str = str(nid) if nid is not None else "-"
        status_color = (Fore.GREEN if status == "SUCCESS"
                        else Fore.YELLOW if status == "SKIPPED"
                        else Fore.RED)
        print(f"  {Fore.WHITE}{Style.DIM}[{log_id}]{Style.RESET_ALL} "
              f"{Fore.CYAN}{ts}{Style.RESET_ALL} | "
              f"{Fore.YELLOW}nid={nid_str}{Style.RESET_ALL} | "
              f"{Fore.WHITE}{ch}{Style.RESET_ALL} | "
              f"{status_color}{status}{Style.RESET_ALL}")
        if response:
            print(f"      {Fore.WHITE}{Style.DIM}{response}{Style.RESET_ALL}")
    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'─'*65}{Style.RESET_ALL}")

# ── JSON Import / Export ───────────────────────────────────────────────────────

def export_notifications_to_json():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_path = f"notifications_export_{timestamp}.json"
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, message, due_time, sent, recurrence, repeat_time"
            " FROM notifications ORDER BY id"
        )
        rows = c.fetchall()
    data = [
        {"id": r[0], "message": r[1], "due_time": r[2], "sent": bool(r[3]),
         "recurrence": r[4], "repeat_time": r[5]}
        for r in rows
    ]
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print(f"{Fore.GREEN}✅ Exported {len(data)} notifications to {file_path}{Style.RESET_ALL}")
    db_log(None, "system", "EXPORT", f"Exported {len(data)} → {file_path}")


def import_notifications_from_json():
    print(f"  {Fore.YELLOW}▶  JSON file path (Enter for 'notifications_import.json'): {Style.RESET_ALL}", end="")
    file_path = input().strip() or "notifications_import.json"
    if not os.path.exists(file_path):
        print(f"{Fore.RED}❌ File not found: {file_path}{Style.RESET_ALL}")
        return
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"{Fore.RED}❌ Error reading JSON: {e}{Style.RESET_ALL}")
        return
    if not isinstance(data, list):
        print(f"{Fore.RED}❌ Invalid JSON format — expected a list.{Style.RESET_ALL}")
        return

    imported, skipped = 0, 0
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT message, due_time FROM notifications")
        existing = {(r[0], r[1]) for r in c.fetchall()}

        for item in data:
            msg        = item.get("message")
            due_time   = item.get("due_time")
            sent       = 1 if item.get("sent") else 0
            recurrence = item.get("recurrence") or None
            repeat_time = item.get("repeat_time") or None

            if not msg or not due_time:
                skipped += 1
                continue
            if recurrence and recurrence not in ("daily", "weekly", "biweekly", "monthly"):
                skipped += 1
                continue
            dt = _parse_due_time(due_time)
            if not dt:
                skipped += 1
                continue
            canonical = dt.strftime("%Y-%m-%d %H:%M")
            if (msg, canonical) in existing:
                skipped += 1
                continue

            due_ts = _to_ts(dt)
            c.execute(
                "INSERT INTO notifications (message, due_time, due_ts, sent, recurrence, repeat_time)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (msg, canonical, due_ts, sent, recurrence, repeat_time),
            )
            imported += 1

        conn.commit()

    print(f"{Fore.GREEN}✅ Imported {imported} notifications, skipped {skipped}.{Style.RESET_ALL}")
    db_log(None, "system", "IMPORT", f"Imported {imported}, skipped {skipped}, file={file_path}")

# ── Tkinter GUI ────────────────────────────────────────────────────────────────

def launch_tkinter_gui():
    if not TKINTER_AVAILABLE:
        print(f"{Fore.RED}❌ tkinter is not available on this system.{Style.RESET_ALL}")
        return

    import tkinter as tk
    from tkinter import ttk, messagebox

    def refresh_listbox():
        listbox.delete(0, tk.END)
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id, message, due_time, sent, recurrence FROM notifications ORDER BY due_ts"
            )
            for row in c.fetchall():
                status = "SENT" if row[3] else "PENDING"
                rec    = row[4] or "None"
                listbox.insert(tk.END, f"ID:{row[0]} | Due:{row[2]} | {status} | {rec} | {row[1]}")

    def add_reminder():
        def save():
            msg = msg_entry.get().strip()
            due = due_entry.get().strip()
            rec = recurrence_var.get()
            if not msg:
                messagebox.showerror("Error", "Message cannot be empty.")
                return
            dt = _parse_due_time(due)
            if not dt:
                messagebox.showerror("Error", "Invalid date. Use YYYY-MM-DD HH:MM or MM-DD-YYYY HH:MM.")
                return
            rec_val   = rec if rec != "None" else None
            due_ts    = _to_ts(dt)
            canonical = dt.strftime("%Y-%m-%d %H:%M")
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO notifications (message, due_time, due_ts, recurrence) VALUES (?, ?, ?, ?)",
                    (msg, canonical, due_ts, rec_val),
                )
                nid = c.lastrowid
                conn.commit()
            db_log(nid, "system", "CREATED", f"GUI: {msg} due {canonical}")
            messagebox.showinfo("Added", f"Reminder ID {nid} added.")
            win.destroy()
            refresh_listbox()

        win = tk.Toplevel(root)
        win.title("Add Reminder")
        win.geometry("420x290")
        tk.Label(win, text="Message:").pack(pady=4)
        msg_entry = tk.Entry(win, width=55)
        msg_entry.pack(pady=4)
        tk.Label(win, text="Due Time (YYYY-MM-DD HH:MM):").pack(pady=4)
        due_entry = tk.Entry(win, width=55)
        due_entry.pack(pady=4)
        tk.Label(win, text="Recurrence:").pack(pady=4)
        recurrence_var = tk.StringVar(value="None")
        ttk.Combobox(win, textvariable=recurrence_var,
                     values=["None", "daily", "weekly", "biweekly", "monthly"], state="readonly").pack(pady=4)
        tk.Button(win, text="Save", command=save).pack(pady=8)
        tk.Button(win, text="Cancel", command=win.destroy).pack()

    def delete_reminder():
        sel = listbox.curselection()
        if not sel:
            messagebox.showerror("Error", "Select a reminder to delete.")
            return
        item = listbox.get(sel[0])
        nid = int(item.split("|")[0].replace("ID:", "").strip())
        if messagebox.askyesno("Confirm", "Delete this reminder?"):
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM notifications WHERE id = ?", (nid,))
                conn.commit()
            db_log(nid, "system", "DELETED", f"GUI: deleted ID {nid}")
            messagebox.showinfo("Deleted", f"Reminder ID {nid} deleted.")
            refresh_listbox()

    root = tk.Tk()
    root.title(f"Notifier GUI — v{_get_app_version()}")
    root.geometry("720x460")
    tk.Label(root, text="Reminders", font=("Arial", 14, "bold")).pack(pady=8)
    listbox = tk.Listbox(root, width=95, height=18)
    listbox.pack(pady=6)
    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=8)
    tk.Button(btn_frame, text="Add",     command=add_reminder).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="Delete",  command=delete_reminder).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="Refresh", command=refresh_listbox).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="Close",   command=root.destroy).pack(side=tk.LEFT, padx=5)
    refresh_listbox()
    print(f"{Fore.CYAN}🖥️  Opening Tkinter GUI...{Style.RESET_ALL}")
    root.mainloop()
    db_log(None, "system", "GUI_CLOSED", "Tkinter GUI closed")

# ── Update checker ─────────────────────────────────────────────────────────────

def _version_tuple(v):
    try:
        return tuple(int(x) for x in v.split('.'))
    except Exception:
        return (0, 0, 0)


def check_for_updates():
    """Fetch the latest version string from the GitHub CHANGELOG.md."""
    import urllib.request
    import re
    try:
        url = "https://raw.githubusercontent.com/trickdaddy24/notifier/main/CHANGELOG.md"
        with urllib.request.urlopen(url, timeout=10) as resp:
            content = resp.read().decode('utf-8')
        match = re.search(r'## \[v([0-9]+\.[0-9]+\.[0-9]+)\]', content)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def _backup_local_state(script_dir: Path) -> Path | None:
    """Copy the DB + .env aside before a destructive update.

    `git reset --hard` discards anything tracked; the DB/.env are gitignored
    so they survive a clean repo, but a stray `.gitignore` change (or a future
    tracked DB) would mean silent data loss. A timestamped copy is cheap
    insurance.
    """
    import shutil
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = script_dir / "backups" / stamp
    saved = []
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in (DB_NAME, ".env", "version_notes.db"):
            src = script_dir / name
            if src.exists():
                shutil.copy2(src, backup_dir / name)
                saved.append(name)
    except Exception as e:
        print(f"{Fore.YELLOW}⚠️  Backup failed ({e}); aborting update to be safe.{Style.RESET_ALL}")
        return None
    if saved:
        print(f"{Fore.GREEN}💾 Backed up {', '.join(saved)} → {backup_dir}{Style.RESET_ALL}")
    return backup_dir


def do_update():
    """Pull latest code via git and reinstall dependencies."""
    import subprocess
    import sys
    script_dir = Path(__file__).parent

    # Snapshot local data before the hard reset wipes the working tree.
    if _backup_local_state(script_dir) is None:
        return False

    print(f"{Fore.CYAN}📦 Fetching latest code...{Style.RESET_ALL}")
    try:
        subprocess.run(["git", "-C", str(script_dir), "fetch", "origin"], check=True)
        subprocess.run(["git", "-C", str(script_dir), "reset", "--hard", "origin/main"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"{Fore.RED}❌ Git update failed: {e}{Style.RESET_ALL}")
        return False
    print(f"{Fore.CYAN}📦 Reinstalling dependencies...{Style.RESET_ALL}")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r",
             str(script_dir / "requirements.txt"), "-q"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"{Fore.RED}❌ Dependency install failed: {e}{Style.RESET_ALL}")
        return False
    print(f"{Fore.GREEN}✅ Update complete! Please restart the app to apply changes.{Style.RESET_ALL}")
    return True

# ── System menu ────────────────────────────────────────────────────────────────

def show_about(vm):
    """Display the About screen in script-header format, evenly bordered."""
    version, revised = vm.get_latest_release_info()

    W = Fore.WHITE + Style.BRIGHT
    D = Fore.WHITE + Style.DIM
    C = Fore.CYAN  + Style.BRIGHT
    R = Style.RESET_ALL

    # Box geometry (all measurements are visible character counts)
    BOX_W   = 73   # total width: matches the ##### border
    INNER_W = BOX_W - 2   # 71 — content between the two # chars
    INDENT  = 2            # spaces between opening # and label
    LABEL_W = 16           # label field width (label text padded to 16)
    # value field = 71 - 2 - 16 = 53 visible chars max per line

    border = '#' * BOX_W

    def _row(label, value):
        label_padded = f"{label:<{LABEL_W}}"          # always exactly LABEL_W visible chars
        inner_vis    = ' ' * INDENT + label_padded + value
        pad          = max(INNER_W - len(inner_vis), 0)
        lc           = W if label else ''
        print(f"  {C}#{R}{' ' * INDENT}{lc}{label_padded}{R}{D}{value}{R}{' ' * pad}{C}#{R}")

    print(f"\n  {C}{border}{R}")
    _row("Title:",       "Notifier")
    _row("Author(s):",   "Stunna / Claude")
    _row("Revised:",     revised)
    _row("Description:", "Multi-platform CLI notification scheduler — Telegram,")
    _row("",             "Discord, Pushover, Gmail — SQLite, recurrence,")
    _row("",             "audit logging, JSON import/export, Tkinter GUI")
    _row("Version:",     f"v{version}")
    _row("Entry Point:", "notifier.py")
    _row("License:",     "MIT")
    _row("GitHub:",      "https://github.com/trickdaddy24/notifier")
    print(f"  {C}{border}{R}")


def system_menu():
    """System menu — version history powered by version_manager.py"""
    try:
        import version_manager as vm
        vm.setup_logging()
        vm.setup_database()
    except ImportError:
        print(f"{Fore.RED}❌ version_manager.py not found in project directory.{Style.RESET_ALL}")
        input(f"\n  {Fore.YELLOW}Press Enter...{Style.RESET_ALL}")
        return

    while True:
        ver     = vm.get_current_version()
        ver_str = f"v{ver}"
        _box(Fore.CYAN, "⚙️  SYSTEM", ver_str)
        tz_label   = _tz_label()
        hb_label    = "daily (00:00–12:00)" if _heartbeat_enabled() else "disabled"
        _opt("1", Fore.CYAN,                   "📜", "View Version History")
        _opt("2", Fore.GREEN  + Style.BRIGHT,  "➕", "Add New Version Release")
        _opt("3", Fore.BLUE   + Style.BRIGHT,  "✏️ ", "Edit Version Notes")
        _opt("4", Fore.MAGENTA + Style.BRIGHT, "🔄", "Check for Updates")
        _opt("5", Fore.YELLOW + Style.BRIGHT,  "🕐", f"Set Timezone  {Fore.WHITE}{Style.DIM}[{tz_label}]")
        _opt("6", Fore.MAGENTA + Style.BRIGHT, "💓", f"Heartbeat  {Fore.WHITE}{Style.DIM}[{hb_label}]")
        _opt("7", Fore.WHITE   + Style.DIM,    "ℹ️ ", "About")
        _div()
        _opt("0", Fore.RED + Style.DIM,        "⬅️ ", "Back to Main Menu")

        choice = _prompt("Choose: ")

        if choice == "1":
            vm.view_version_history()
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "2":
            vm.add_version_notes()
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "3":
            vm.edit_notes()
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "4":
            current = vm.get_current_version()
            print(f"\n{Fore.CYAN}🔍 Checking for updates...{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}Current version: {Fore.YELLOW}{Style.BRIGHT}v{current}{Style.RESET_ALL}")
            latest = check_for_updates()
            if latest is None:
                print(f"{Fore.RED}❌ Could not reach GitHub. Check your connection.{Style.RESET_ALL}")
            elif _version_tuple(latest) <= _version_tuple(current):
                print(f"  {Fore.WHITE}Latest version:  {Fore.GREEN}{Style.BRIGHT}v{latest}{Style.RESET_ALL}")
                print(f"{Fore.GREEN}✅ Already up to date!{Style.RESET_ALL}")
            else:
                print(f"  {Fore.WHITE}Latest version:  {Fore.CYAN}{Style.BRIGHT}v{latest}{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}🆕 Update available: v{current} → v{latest}{Style.RESET_ALL}")
                confirm = _prompt("Update now? (y/N): ")
                if confirm.lower() == 'y':
                    do_update()
                else:
                    print(f"{Fore.YELLOW}Update skipped.{Style.RESET_ALL}")
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "5":
            print(f"\n{Fore.YELLOW}{Style.BRIGHT}🕐 Set Timezone{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{Style.DIM}Current: {_tz_label()}{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{Style.DIM}Examples: America/New_York  America/Chicago  Europe/London  Asia/Tokyo{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{Style.DIM}Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones{Style.RESET_ALL}")
            new_tz = _prompt("Timezone (Enter to cancel): ")
            if new_tz:
                try:
                    ZoneInfo(new_tz)
                    set_key(str(ENV_PATH), 'TIMEZONE', new_tz)
                    os.environ['TIMEZONE'] = new_tz
                    load_dotenv(str(ENV_PATH), override=True)
                    print(f"{Fore.GREEN}✅ Timezone set to {new_tz}{Style.RESET_ALL}")
                except (ZoneInfoNotFoundError, KeyError):
                    print(f"{Fore.RED}❌ Unknown timezone '{new_tz}' — not saved.{Style.RESET_ALL}")
            else:
                print(f"{Fore.YELLOW}Cancelled — timezone unchanged.{Style.RESET_ALL}")
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "6":
            current_label = "enabled (daily 00:00–12:00)" if _heartbeat_enabled() else "disabled"
            print(f"\n{Fore.MAGENTA}{Style.BRIGHT}💓 Configure Heartbeat{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{Style.DIM}Current: {current_label}{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{Style.DIM}Fires once daily at a random time between 00:00 and 12:00.{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{Style.DIM}1 = enable, 0 = disable.{Style.RESET_ALL}")
            new_hb = _prompt("Enable heartbeat? (0=disable / 1=enable): ")
            if new_hb in ('0', '1'):
                set_key(str(ENV_PATH), 'HEARTBEAT_ENABLED', new_hb)
                os.environ['HEARTBEAT_ENABLED'] = new_hb
                load_dotenv(str(ENV_PATH), override=True)
                status = "enabled" if new_hb == '1' else "disabled"
                print(f"{Fore.GREEN}✅ Heartbeat {status}. Restart app to apply.{Style.RESET_ALL}")
            else:
                print(f"{Fore.YELLOW}⚠️  Invalid input — unchanged.{Style.RESET_ALL}")
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "7":
            show_about(vm)
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "0":
            break
        else:
            print(f"{Fore.RED}❌ Invalid choice.{Style.RESET_ALL}")

# ── Version helper ─────────────────────────────────────────────────────────────

def _get_app_version() -> str:
    """Read current version from version_notes.db; fall back to hardcoded."""
    try:
        import version_manager as vm
        vm.setup_database()
        return vm.get_current_version()
    except Exception:
        return "2.1.0"

# ── Background runner ──────────────────────────────────────────────────────────

def background_runner():
    """Daemon thread — runs scheduled notification checks and heartbeat.

    Scheduled jobs run with _QUIET on so their sender output never scrambles
    the interactive menu the user may be sitting in; it still hits the log
    file and the audit DB.
    """
    global _QUIET
    while True:
        try:
            _QUIET = True
            schedule.run_pending()
        except Exception as exc:
            logging.exception("Scheduler error: %s", exc)
        finally:
            _QUIET = False
        time.sleep(60)

# ── Scheduler setup (shared by interactive + daemon) ──────────────────────────

def _heartbeat_enabled() -> bool:
    """Heartbeat on unless HEARTBEAT_ENABLED/HEARTBEAT_INTERVAL is 0/false/off.

    Historically this was HEARTBEAT_INTERVAL ("hours") but the code only ever
    treated it as a boolean. HEARTBEAT_ENABLED is the new name; the old var is
    still honored for backward compatibility.
    """
    raw = os.getenv('HEARTBEAT_ENABLED', os.getenv('HEARTBEAT_INTERVAL', '1')).strip().lower()
    return raw not in ('0', 'false', 'no', 'off', '')


def setup_schedule():
    """Register the recurring jobs. Returns the heartbeat fire time or None."""
    try:
        schedule.every(1).minutes.do(send_notifications)
    except Exception as e:
        logging.error("Could not set up scheduler: %s", e)
        print(f"{Fore.RED}⚠️  Warning: Could not set up scheduler: {e}{Style.RESET_ALL}")

    hb_fire_time = None
    if _heartbeat_enabled():
        try:
            hb_fire_time = f"{random.randint(0, 11):02d}:{random.randint(0, 59):02d}"
            schedule.every().day.at(hb_fire_time).do(send_heartbeat)
            logging.info("Heartbeat scheduled daily at %s", hb_fire_time)
        except Exception:
            hb_fire_time = None
    return hb_fire_time


def _print_startup_banner(hb_fire_time, ver, daemon=False):
    mode = "daemon" if daemon else "interactive"
    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}🔔  Notifier  {Fore.WHITE}v{ver}{Style.RESET_ALL}  {Fore.WHITE}{Style.DIM}({mode}){Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}✅  Background scheduler started{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}{Style.DIM}🕐  Timezone: {Fore.YELLOW}{Style.BRIGHT}{_tz_label()}{Style.RESET_ALL}")
    if hb_fire_time:
        print(f"  {Fore.MAGENTA}💓  Heartbeat daily at {hb_fire_time} (window: 00:00–12:00){Style.RESET_ALL}")
    else:
        print(f"  {Fore.WHITE}{Style.DIM}💓  Heartbeat disabled (HEARTBEAT_ENABLED=0){Style.RESET_ALL}")
    if not NOTIFICATIONS_AVAILABLE:
        print(f"  {Fore.YELLOW}⚠️   Desktop notifications disabled (install plyer){Style.RESET_ALL}")
    print()


def _admin_startup_alert(ver):
    send_admin_notification(
        f"✅ Notifier v{ver} started at {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        include_system_info=True,
    )

# ── Headless CLI entry points ─────────────────────────────────────────────────

def create_notification_cli(msg, due_raw, repeat=None, at=None) -> bool:
    """Non-interactive notification create. Returns True on success."""
    if not msg or len(msg) > 4000:
        print(f"{Fore.RED}❌ Message must be 1–4000 characters.{Style.RESET_ALL}")
        return False

    recurrence = repeat if repeat in ("daily", "weekly", "biweekly", "monthly") else None
    repeat_time = None

    if recurrence == "daily":
        at = at or due_raw
        try:
            datetime.strptime(at, "%H:%M")
        except (ValueError, TypeError):
            print(f"{Fore.RED}❌ Daily repeat needs --at HH:MM (got {at!r}).{Style.RESET_ALL}")
            return False
        repeat_time = at
        due_dt = _next_daily_time(repeat_time)
        if due_dt is None:
            print(f"{Fore.RED}❌ Could not compute next occurrence.{Style.RESET_ALL}")
            return False
    else:
        due_dt = _parse_due_time(due_raw or "")
        if due_dt is None:
            print(f"{Fore.RED}❌ Invalid --due. Use 'YYYY-MM-DD HH:MM'.{Style.RESET_ALL}")
            return False
        if due_dt <= _now_in_tz():
            print(f"{Fore.RED}❌ --due is in the past ({_tz_label()}).{Style.RESET_ALL}")
            return False

    due = due_dt.strftime("%Y-%m-%d %H:%M")
    due_ts = _to_ts(due_dt)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO notifications (message, due_time, due_ts, recurrence, repeat_time)"
            " VALUES (?, ?, ?, ?, ?)",
            (msg, due, due_ts, recurrence, repeat_time),
        )
        nid = c.lastrowid
        conn.commit()
    db_log(nid, "system", "CREATED",
           f"CLI add: {msg} | due={due} | recurrence={recurrence} | repeat_time={repeat_time}")
    label = (f"Daily at {repeat_time}, next {due}" if repeat_time
             else f"{recurrence.title()}, first {due}" if recurrence
             else f"Due {due}")
    print(f"{Fore.GREEN}✅ Added ID:{nid} | {label}{Style.RESET_ALL}")
    return True


def run_daemon():
    """Headless scheduler loop — no menu, no stdin. For Task Scheduler/systemd."""
    init_db()
    ver = _get_app_version()
    hb_fire_time = setup_schedule()
    _admin_startup_alert(ver)
    _print_startup_banner(hb_fire_time, ver, daemon=True)
    logging.info("Notifier v%s started in daemon mode", ver)
    print(f"  {Fore.WHITE}{Style.DIM}Running. Ctrl+C to stop.{Style.RESET_ALL}\n")
    try:
        while True:
            try:
                schedule.run_pending()
            except Exception as exc:
                logging.exception("Scheduler error: %s", exc)
            time.sleep(30)
    except KeyboardInterrupt:
        print(f"\n  {Fore.GREEN}👋  Daemon stopped.{Style.RESET_ALL}")
        send_admin_notification(
            f"🛑 Notifier daemon stopped at {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )


def run_send_now():
    """One-shot: deliver everything due, then exit. Ideal for cron/schtasks."""
    init_db()
    send_notifications(verbose=True)

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    init_db()
    ver = _get_app_version()
    hb_fire_time = setup_schedule()

    # Start background thread
    t = threading.Thread(target=background_runner, daemon=True)
    t.start()

    _admin_startup_alert(ver)
    _print_startup_banner(hb_fire_time, ver)

    # Main menu loop
    while True:
        ver     = _get_app_version()
        ver_str = f"v{ver}"
        _box(Fore.CYAN, "📋 NOTIFICATION MENU", ver_str)

        _opt(" 1", Fore.GREEN  + Style.BRIGHT,  "➕", "Add Notification")
        _opt(" 2", Fore.CYAN,                    "📋", "View Notifications")
        _opt(" 3", Fore.CYAN,                    "📤", "Send Due Notifications Now")
        _opt(" 4", Fore.BLUE   + Style.BRIGHT,  "✏️ ", "Edit Notification")
        _opt(" 5", Fore.RED,                     "🗑️ ", "Delete Notification")
        _div()
        _opt(" 6", Fore.MAGENTA + Style.BRIGHT,  "📬", "Notification Services")
        _opt(" 7", Fore.CYAN,                    "📜", "View Logs")
        _opt(" 8", Fore.GREEN  + Style.BRIGHT,  "📤", "Export to JSON")
        _opt(" 9", Fore.GREEN  + Style.BRIGHT,  "📥", "Import from JSON")
        _opt("10", Fore.WHITE,                   "🖥️ ", "Open GUI (Tkinter)")
        _div()
        _opt("11", Fore.WHITE,                   "⚙️ ", f"System  {Fore.CYAN}[{ver_str}]{Style.RESET_ALL}")
        _div()
        _opt(" 0", Fore.RED + Style.DIM,         "🚪", "Exit")

        choice = _prompt("Choose an option: ")

        if choice == "1":
            add_notification()
        elif choice == "2":
            view_notifications()
        elif choice == "3":
            send_notifications(verbose=True)
            extra = _prompt("Send a specific ID now / snooze? (id | id+mins | Enter to skip): ")
            if extra:
                parts = extra.replace("+", " ").split()
                if len(parts) == 1 and parts[0].isdigit():
                    send_notifications(verbose=True, only_id=int(parts[0]))
                elif len(parts) == 2 and all(p.lstrip("-").isdigit() for p in parts):
                    snooze_notification(parts[0], parts[1])
                else:
                    print(f"{Fore.YELLOW}⚠️  Unrecognized — use '5' or '5 30'.{Style.RESET_ALL}")
        elif choice == "4":
            edit_notification()
        elif choice == "5":
            delete_notification()
        elif choice == "6":
            notification_services_menu()
        elif choice == "7":
            show_logs()
        elif choice == "8":
            export_notifications_to_json()
        elif choice == "9":
            import_notifications_from_json()
        elif choice == "10":
            launch_tkinter_gui()
        elif choice == "11":
            system_menu()
        elif choice == "0":
            print(f"\n  {Fore.GREEN}{Style.BRIGHT}👋  Goodbye!{Style.RESET_ALL}\n")
            send_admin_notification(
                f"🛑 Notifier stopped at {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            break
        else:
            print(f"{Fore.RED}❌ Invalid choice. Please try again.{Style.RESET_ALL}")


def cli():
    """Parse argv and dispatch. No args → interactive menu (legacy default)."""
    import argparse
    p = argparse.ArgumentParser(
        prog="notifier",
        description="Multi-channel notification scheduler. "
                    "Run with no arguments for the interactive menu.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--daemon", action="store_true",
                   help="Headless scheduler loop (no menu). For Task Scheduler/systemd.")
    g.add_argument("--send-now", action="store_true",
                   help="Deliver everything currently due, then exit.")
    g.add_argument("--add", metavar="MESSAGE",
                   help="Add a notification non-interactively (needs --due, or --repeat daily --at).")
    g.add_argument("--list", action="store_true",
                   help="Print scheduled notifications and exit.")
    g.add_argument("--send-id", metavar="ID", type=int,
                   help="Force-send one notification by ID now, then exit.")
    g.add_argument("--snooze", metavar="ID", type=int,
                   help="Push a notification's due time out by --minutes.")
    p.add_argument("--minutes", metavar="N", type=int, default=15,
                   help="Minutes for --snooze (default 15).")
    p.add_argument("--due", metavar="'YYYY-MM-DD HH:MM'",
                   help="Due time for --add (one-time / weekly / biweekly / monthly).")
    p.add_argument("--repeat", choices=["daily", "weekly", "biweekly", "monthly"],
                   help="Make --add recurring.")
    p.add_argument("--at", metavar="HH:MM",
                   help="Time of day for --repeat daily.")
    p.add_argument("--version", action="store_true", help="Print version and exit.")
    args = p.parse_args()

    if args.version:
        print(f"Notifier v{_get_app_version()}")
        return
    if args.daemon:
        run_daemon()
        return
    if args.send_now:
        run_send_now()
        return
    if args.list:
        init_db()
        view_notifications()
        return
    if args.send_id is not None:
        init_db()
        send_notifications(verbose=True, only_id=args.send_id)
        return
    if args.snooze is not None:
        init_db()
        ok = snooze_notification(args.snooze, args.minutes)
        sys.exit(0 if ok else 1)
    if args.add:
        init_db()
        ok = create_notification_cli(args.add, args.due, repeat=args.repeat, at=args.at)
        sys.exit(0 if ok else 1)

    main()


if __name__ == "__main__":
    cli()
