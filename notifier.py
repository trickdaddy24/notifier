# -*- coding: utf-8 -*-
# notifier.py — Notification App v2.0.0

import sqlite3
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
                          (int(dt.timestamp()), row_id))

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


def _next_recurrence_ts(due_ts, recurrence, repeat_time=None):
    """Compute the next due_ts for a recurring notification. Rolls forward if past."""
    now_ts = int(time.time())

    if recurrence == "daily" and repeat_time:
        next_dt = _next_daily_time(repeat_time)
        return int(next_dt.timestamp()) if next_dt else None

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
            print(f"{Fore.GREEN}✅ Telegram message sent!{Style.RESET_ALL}")
            return True, f"HTTP {r.status_code}"
        print(f"{Fore.RED}❌ Telegram API error: {r.status_code}{Style.RESET_ALL}")
        return False, f"HTTP {r.status_code} - {r.text}"
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}❌ Failed to send Telegram: {e}{Style.RESET_ALL}")
        return False, str(e)


def send_discord_message(message):
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        return False, "Missing DISCORD_WEBHOOK_URL"
    try:
        r = requests.post(webhook_url, json={"content": message}, timeout=10)
        if r.status_code in (200, 204):
            print(f"{Fore.GREEN}✅ Discord message sent!{Style.RESET_ALL}")
            return True, f"HTTP {r.status_code}"
        print(f"{Fore.RED}❌ Discord API error: {r.status_code}{Style.RESET_ALL}")
        return False, f"HTTP {r.status_code} - {r.text}"
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}❌ Failed to send Discord: {e}{Style.RESET_ALL}")
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
            print(f"{Fore.GREEN}✅ Pushover message sent!{Style.RESET_ALL}")
            return True, f"HTTP {r.status_code}"
        print(f"{Fore.RED}❌ Pushover API error: {r.status_code}{Style.RESET_ALL}")
        return False, f"HTTP {r.status_code} - {r.text}"
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}❌ Failed to send Pushover: {e}{Style.RESET_ALL}")
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
        print(f"{Fore.GREEN}✅ Email sent!{Style.RESET_ALL}")
        return True, "Email sent"
    except Exception as e:
        print(f"{Fore.RED}❌ Failed to send email: {e}{Style.RESET_ALL}")
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
    print(f"{Fore.MAGENTA}💓 Sending heartbeat...{Style.RESET_ALL}")
    logging.info("Heartbeat: %s", msg)

    _notify = getattr(notification, "notify", None) if notification is not None else None
    if NOTIFICATIONS_AVAILABLE and callable(_notify):
        try:
            _notify(title="💓 Heartbeat", message="Notifier is running", timeout=5)
        except Exception:
            pass

    for fn, ch in [
        (send_telegram_message, "telegram"),
        (send_discord_message,  "discord"),
        (send_pushover_message, "pushover"),
        (send_email_message,    "email"),
    ]:
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


def telegram_menu():
    while True:
        _box(Fore.BLUE, "📱 TELEGRAM SETTINGS")
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id   = os.getenv('TELEGRAM_CHAT_ID')
        configured = bool(bot_token and chat_id)
        status = (f"{Fore.GREEN}{Style.BRIGHT}✅ CONFIGURED{Style.RESET_ALL}"
                  if configured else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}")
        print(f"  Status: {status}\n")
        choice = _service_menu_options(Fore.BLUE)
        if choice == "1":
            verify_telegram_config()
        elif choice == "2":
            set_telegram_credentials()
        elif choice == "3":
            msg = _prompt("Test message (Enter for default): ") or "🧪 Test from Notification App!"
            send_telegram_message(msg)
        elif choice == "4":
            print(f"\n  {Fore.GREEN}TELEGRAM_BOT_TOKEN={masked(bot_token)}{Style.RESET_ALL}")
            print(f"  {Fore.GREEN}TELEGRAM_CHAT_ID={chat_id or '(not set)'}{Style.RESET_ALL}")
        elif choice == "5":
            print(f"\n  {Fore.CYAN}{Style.BRIGHT}📚 Telegram Setup:{Style.RESET_ALL}")
            print(f"  1. Message @BotFather on Telegram")
            print(f"  2. Send /newbot and follow instructions")
            print(f"  3. Get your bot token")
            print(f"  4. Message @userinfobot to get your chat ID")
            print(f"  5. Add both to .env file")
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "0":
            break


def discord_menu():
    while True:
        _box(Fore.MAGENTA, "💬 DISCORD SETTINGS")
        webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
        status = (f"{Fore.GREEN}{Style.BRIGHT}✅ CONFIGURED{Style.RESET_ALL}"
                  if webhook_url else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}")
        print(f"  Status: {status}\n")
        choice = _service_menu_options(Fore.MAGENTA)
        if choice == "1":
            verify_discord_config()
        elif choice == "2":
            set_discord_credentials()
        elif choice == "3":
            msg = _prompt("Test message (Enter for default): ") or "🧪 Test from Notification App!"
            send_discord_message(msg)
        elif choice == "4":
            print(f"\n  {Fore.GREEN}DISCORD_WEBHOOK_URL={masked(webhook_url)}{Style.RESET_ALL}")
        elif choice == "5":
            print(f"\n  {Fore.CYAN}{Style.BRIGHT}📚 Discord Setup:{Style.RESET_ALL}")
            print(f"  1. Go to your Discord server")
            print(f"  2. Edit Channel → Integrations → Webhooks")
            print(f"  3. Create New Webhook and copy the URL")
            print(f"  4. Add DISCORD_WEBHOOK_URL to .env file")
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "0":
            break


def pushover_menu():
    while True:
        _box(Fore.YELLOW, "📲 PUSHOVER SETTINGS")
        user_key  = os.getenv('PUSHOVER_USER_KEY')
        api_token = os.getenv('PUSHOVER_API_TOKEN')
        status = (f"{Fore.GREEN}{Style.BRIGHT}✅ CONFIGURED{Style.RESET_ALL}"
                  if (user_key and api_token) else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}")
        print(f"  Status: {status}\n")
        choice = _service_menu_options(Fore.YELLOW)
        if choice == "1":
            verify_pushover_config()
        elif choice == "2":
            set_pushover_credentials()
        elif choice == "3":
            msg = _prompt("Test message (Enter for default): ") or "🧪 Test from Notification App!"
            send_pushover_message(msg)
        elif choice == "4":
            print(f"\n  {Fore.GREEN}PUSHOVER_USER_KEY={masked(user_key)}{Style.RESET_ALL}")
            print(f"  {Fore.GREEN}PUSHOVER_API_TOKEN={masked(api_token)}{Style.RESET_ALL}")
        elif choice == "5":
            print(f"\n  {Fore.CYAN}{Style.BRIGHT}📚 Pushover Setup:{Style.RESET_ALL}")
            print(f"  1. Go to https://pushover.net")
            print(f"  2. Create an account and note your User Key")
            print(f"  3. Create an Application to get an API Token")
            print(f"  4. Add both to .env file")
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "0":
            break


def email_menu():
    while True:
        _box(Fore.RED, "📧 GMAIL SETTINGS")
        sender    = os.getenv('EMAIL_SENDER')
        recipient = os.getenv('EMAIL_RECIPIENT')
        password  = os.getenv('EMAIL_PASSWORD')
        status = (f"{Fore.GREEN}{Style.BRIGHT}✅ CONFIGURED{Style.RESET_ALL}"
                  if (sender and password and recipient) else f"{Fore.RED}❌ NOT CONFIGURED{Style.RESET_ALL}")
        print(f"  Status: {status}\n")
        choice = _service_menu_options(Fore.RED)
        if choice == "1":
            verify_email_config()
        elif choice == "2":
            set_email_credentials()
        elif choice == "3":
            msg = _prompt("Test message (Enter for default): ") or "🧪 Test from Notification App!"
            send_email_message(msg)
        elif choice == "4":
            print(f"\n  {Fore.GREEN}EMAIL_SMTP_SERVER={os.getenv('EMAIL_SMTP_SERVER', 'smtp.gmail.com')}{Style.RESET_ALL}")
            print(f"  {Fore.GREEN}EMAIL_SMTP_PORT={os.getenv('EMAIL_SMTP_PORT', '587')}{Style.RESET_ALL}")
            print(f"  {Fore.GREEN}EMAIL_SENDER={masked(sender)}{Style.RESET_ALL}")
            print(f"  {Fore.GREEN}EMAIL_PASSWORD={masked(password)}{Style.RESET_ALL}")
            print(f"  {Fore.GREEN}EMAIL_RECIPIENT={masked(recipient)}{Style.RESET_ALL}")
        elif choice == "5":
            print(f"\n  {Fore.CYAN}{Style.BRIGHT}📚 Gmail Setup:{Style.RESET_ALL}")
            print(f"  1. Go to Google Account → Security")
            print(f"  2. Enable 2-Step Verification")
            print(f"  3. Go to App Passwords → Generate one for 'Mail'")
            print(f"  4. Use that password (NOT your regular password)")
            print(f"  5. Add all EMAIL_* variables to .env file")
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
        elif choice == "0":
            break


def notification_services_menu():
    while True:
        _box(Fore.MAGENTA, "📬 NOTIFICATION SERVICES")
        tg_ok = bool(os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID'))
        dc_ok = bool(os.getenv('DISCORD_WEBHOOK_URL'))
        po_ok = bool(os.getenv('PUSHOVER_USER_KEY') and os.getenv('PUSHOVER_API_TOKEN'))
        em_ok = bool(os.getenv('EMAIL_SENDER') and os.getenv('EMAIL_PASSWORD') and os.getenv('EMAIL_RECIPIENT'))

        def _svc_line(num, ok, emoji, label):
            tick = f"{Fore.GREEN}✅{Style.RESET_ALL}" if ok else f"{Fore.RED}❌{Style.RESET_ALL}"
            clr  = Fore.WHITE if ok else Fore.WHITE + Style.DIM
            print(f"  {Fore.YELLOW}{Style.BRIGHT}{num}{Style.RESET_ALL}  {tick} {clr}{emoji}  {label}{Style.RESET_ALL}")

        _svc_line("1", tg_ok, "📱", "Telegram")
        _svc_line("2", dc_ok, "💬", "Discord")
        _svc_line("3", po_ok, "📲", "Pushover")
        _svc_line("4", em_ok, "📧", "Gmail")
        _div()
        _opt("5", Fore.WHITE + Style.DIM, "📋", "Show Complete .env Example")
        _opt("0", Fore.RED  + Style.DIM,  "⬅️ ", "Back to Main Menu")

        choice = _prompt("Choose: ")
        if choice == "1":
            telegram_menu()
        elif choice == "2":
            discord_menu()
        elif choice == "3":
            pushover_menu()
        elif choice == "4":
            email_menu()
        elif choice == "5":
            show_complete_env_example()
        elif choice == "0":
            break


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
    print(f"  {Fore.YELLOW}# Heartbeat interval in hours (0 = disabled){Style.RESET_ALL}")
    print(f"  {Fore.GREEN}HEARTBEAT_INTERVAL=6{Style.RESET_ALL}\n")
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


def set_telegram_credentials():
    print(f"\n{Fore.BLUE}{Style.BRIGHT}📱 Enter Telegram Credentials{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{Style.DIM}Press Enter to skip a field and keep its current value.{Style.RESET_ALL}\n")
    _set_credential('TELEGRAM_BOT_TOKEN', 'Bot Token')
    _set_credential('TELEGRAM_CHAT_ID', 'Chat ID')
    load_dotenv(str(ENV_PATH), override=True)


def set_discord_credentials():
    print(f"\n{Fore.MAGENTA}{Style.BRIGHT}💬 Enter Discord Credentials{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{Style.DIM}Press Enter to skip and keep current value.{Style.RESET_ALL}\n")
    _set_credential('DISCORD_WEBHOOK_URL', 'Webhook URL')
    load_dotenv(str(ENV_PATH), override=True)


def set_pushover_credentials():
    print(f"\n{Fore.YELLOW}{Style.BRIGHT}📲 Enter Pushover Credentials{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{Style.DIM}Press Enter to skip a field and keep its current value.{Style.RESET_ALL}\n")
    _set_credential('PUSHOVER_USER_KEY', 'User Key')
    _set_credential('PUSHOVER_API_TOKEN', 'API Token')
    load_dotenv(str(ENV_PATH), override=True)


def set_email_credentials():
    print(f"\n{Fore.RED}{Style.BRIGHT}📧 Enter Gmail Credentials{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{Style.DIM}Press Enter to skip a field and keep its current value.{Style.RESET_ALL}\n")
    _set_credential('EMAIL_SENDER', 'Sender Email')
    _set_credential('EMAIL_PASSWORD', 'App Password', secret=True)
    _set_credential('EMAIL_RECIPIENT', 'Recipient Email')
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
            due_ts = int(due_dt.timestamp())
            print(f"  {Fore.CYAN}First occurrence: {Fore.WHITE}{Style.BRIGHT}{due} ({_tz_label()}){Style.RESET_ALL}")

        elif rtype in ("2", "3"):
            recurrence = "weekly" if rtype == "2" else "biweekly"
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
            due_ts = int(due_dt.timestamp())
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
        due_ts = int(due_dt.timestamp())

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
            "SELECT id, message, due_time, sent, recurrence, repeat_time"
            " FROM notifications ORDER BY due_ts"
        )
        rows = c.fetchall()

    if not rows:
        print(f"{Fore.YELLOW}⚠️  No notifications scheduled.{Style.RESET_ALL}")
        return

    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}")
    for nid, msg, due_time, sent, recurrence, repeat_time in rows:
        status = (f"{Fore.GREEN}{Style.BRIGHT}✅ SENT{Style.RESET_ALL}"
                  if sent else f"{Fore.YELLOW}⏳ PENDING{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}{Style.BRIGHT}#{nid}{Style.RESET_ALL}  {Fore.WHITE}{Style.BRIGHT}{msg}{Style.RESET_ALL}")
        print(f"     {Fore.WHITE}{Style.DIM}Due:{Style.RESET_ALL} {Fore.CYAN}{due_time}{Style.RESET_ALL}  {status}")
        if recurrence:
            repeat_label = f" at {repeat_time}" if repeat_time else ""
            print(f"     {Fore.WHITE}{Style.DIM}Repeat:{Style.RESET_ALL} {Fore.MAGENTA}{recurrence.title()}{repeat_label}{Style.RESET_ALL}")
        print(f"  {Fore.WHITE}{Style.DIM}{'─'*39}{Style.RESET_ALL}")


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
                                new_due_ts = int(next_dt.timestamp())
                        except ValueError:
                            print(f"{Fore.RED}❌ Invalid format. Keeping original.{Style.RESET_ALL}")
                else:
                    print(f"  {Fore.YELLOW}▶  New first due time (Enter to keep '{due_time}'): {Style.RESET_ALL}", end="")
                    due_raw = input().strip()
                    if due_raw:
                        due_dt = _parse_due_time(due_raw)
                        if due_dt and due_dt > _now_in_tz():
                            new_due = due_dt.strftime("%Y-%m-%d %H:%M")
                            new_due_ts = int(due_dt.timestamp())
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
                    new_due_ts = int(due_dt.timestamp())
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
                        new_due_ts = int(due_dt.timestamp())
            elif sched_edit == "2":
                print(f"\n  {Fore.CYAN}Repeat type:{Style.RESET_ALL}")
                _opt("1", Fore.WHITE, "📆", "Daily (at a specific time)")
                _opt("2", Fore.WHITE, "📅", "Weekly")
                _opt("3", Fore.WHITE, "🗓️ ", "Biweekly")
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
                            new_due_ts = int(next_dt.timestamp())
                    except ValueError:
                        print(f"{Fore.RED}❌ Invalid format! Keeping original.{Style.RESET_ALL}")
                        new_recurrence = None
                elif rtype in ("2", "3"):
                    new_recurrence = "weekly" if rtype == "2" else "biweekly"

        if new_due_ts is None:
            dt = _parse_due_time(new_due)
            new_due_ts = int(dt.timestamp()) if dt else 0

        c.execute(
            "UPDATE notifications SET message=?, due_time=?, due_ts=?, sent=0, recurrence=?, repeat_time=?"
            " WHERE id=?",
            (new_msg, new_due, new_due_ts, new_recurrence, new_repeat_time, notif_id),
        )
        conn.commit()

    db_log(int(notif_id), "system", "EDITED", f"Updated ID {notif_id}")
    print(f"{Fore.GREEN}✅ Updated notification ID {notif_id}!{Style.RESET_ALL}")

# ── Send notifications (epoch-based, db_log, ≥1-success logic) ────────────────

def send_notifications(verbose=False):
    """Check for due notifications and send them. verbose=True prints when nothing is due."""
    now_ts = int(time.time())
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, message, due_ts, recurrence, repeat_time"
            " FROM notifications WHERE sent=0 AND due_ts <= ?",
            (now_ts,),
        )
        pending = c.fetchall()

        if not pending:
            if verbose:
                print(f"{Fore.YELLOW}⚠️  No due notifications right now.{Style.RESET_ALL}")
            return

        for nid, msg, orig_due_ts, recurrence, repeat_time in pending:
            print(f"{Fore.GREEN}{Style.BRIGHT}📢 Sending: {msg}{Style.RESET_ALL}")

            # Desktop notification
            _notify = getattr(notification, "notify", None) if notification is not None else None
            if NOTIFICATIONS_AVAILABLE and callable(_notify):
                try:
                    _notify(title="⏰ Reminder!", message=msg, timeout=10)
                except Exception:
                    pass

            any_success = False
            full_msg    = f"⏰ Reminder: {msg}"

            for fn, ch in [
                (send_telegram_message, "telegram"),
                (send_discord_message,  "discord"),
                (send_pushover_message, "pushover"),
            ]:
                ok, resp = fn(full_msg)
                status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
                db_log(nid, ch, status, resp)
                if ok:
                    any_success = True

            ok, resp = send_email_message(full_msg, subject="⏰ Reminder")
            status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
            db_log(nid, "email", status, resp)
            if ok:
                any_success = True

            if any_success:
                c.execute("UPDATE notifications SET sent=1 WHERE id=?", (nid,))
                if recurrence:
                    next_ts = _next_recurrence_ts(orig_due_ts, recurrence, repeat_time)
                    if next_ts:
                        next_due_str = datetime.fromtimestamp(next_ts).strftime("%Y-%m-%d %H:%M")
                        c.execute(
                            "INSERT INTO notifications (message, due_time, due_ts, recurrence, repeat_time)"
                            " VALUES (?, ?, ?, ?, ?)",
                            (msg, next_due_str, next_ts, recurrence, repeat_time),
                        )
                        print(f"{Fore.CYAN}🔁 Next {recurrence}: {next_due_str}{Style.RESET_ALL}")
                conn.commit()
                print(f"{Fore.GREEN}✅ ID {nid} marked sent.{Style.RESET_ALL}")
            else:
                conn.commit()
                print(f"{Fore.RED}❌ ID {nid} not marked sent — no channel succeeded.{Style.RESET_ALL}")

        print(f"{Fore.GREEN}✅ Processed {len(pending)} notification(s).{Style.RESET_ALL}")

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
            if recurrence and recurrence not in ("daily", "weekly", "biweekly"):
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

            due_ts = int(dt.timestamp())
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
            due_ts    = int(dt.timestamp())
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
                     values=["None", "daily", "weekly", "biweekly"], state="readonly").pack(pady=4)
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
    root.title("Notifier GUI — v2.0.0")
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


def do_update():
    """Pull latest code via git and reinstall dependencies."""
    import subprocess
    import sys
    script_dir = Path(__file__).parent
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
        hb_interval = os.getenv('HEARTBEAT_INTERVAL', '0')
        hb_label   = f"every {hb_interval}h" if hb_interval != '0' else "disabled"
        _opt("1", Fore.CYAN,                   "📜", "View Version History")
        _opt("2", Fore.GREEN  + Style.BRIGHT,  "➕", "Add New Version Release")
        _opt("3", Fore.BLUE   + Style.BRIGHT,  "✏️ ", "Edit Version Notes")
        _opt("4", Fore.MAGENTA + Style.BRIGHT, "🔄", "Check for Updates")
        _opt("5", Fore.YELLOW + Style.BRIGHT,  "🕐", f"Set Timezone  {Fore.WHITE}{Style.DIM}[{tz_label}]")
        _opt("6", Fore.MAGENTA + Style.BRIGHT, "💓", f"Heartbeat  {Fore.WHITE}{Style.DIM}[{hb_label}]")
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
            current_hb = os.getenv('HEARTBEAT_INTERVAL', '0')
            print(f"\n{Fore.MAGENTA}{Style.BRIGHT}💓 Configure Heartbeat{Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{Style.DIM}Current interval: {current_hb}h (0 = disabled){Style.RESET_ALL}")
            print(f"  {Fore.WHITE}{Style.DIM}Sends a ping to all configured services at the given interval.{Style.RESET_ALL}")
            new_hb = _prompt("Interval in hours (0 to disable): ")
            if new_hb.isdigit():
                set_key(str(ENV_PATH), 'HEARTBEAT_INTERVAL', new_hb)
                os.environ['HEARTBEAT_INTERVAL'] = new_hb
                load_dotenv(str(ENV_PATH), override=True)
                status = f"every {new_hb}h" if new_hb != '0' else "disabled"
                print(f"{Fore.GREEN}✅ Heartbeat {status}. Restart app to apply.{Style.RESET_ALL}")
            else:
                print(f"{Fore.YELLOW}⚠️  Invalid input — unchanged.{Style.RESET_ALL}")
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
        return "2.0.0"

# ── Background runner ──────────────────────────────────────────────────────────

def background_runner():
    """Daemon thread — runs scheduled notification checks and heartbeat."""
    while True:
        try:
            schedule.run_pending()
        except Exception as exc:
            logging.exception("Scheduler error: %s", exc)
        time.sleep(60)

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    init_db()

    # Schedule notification check every minute
    try:
        schedule.every(1).minutes.do(send_notifications)
    except Exception as e:
        print(f"{Fore.RED}⚠️  Warning: Could not set up scheduler: {e}{Style.RESET_ALL}")

    # Schedule heartbeat if configured
    try:
        interval_h = int(os.getenv('HEARTBEAT_INTERVAL', '0'))
        if interval_h > 0:
            schedule.every(interval_h).hours.do(send_heartbeat)
    except (ValueError, Exception):
        pass

    # Start background thread
    t = threading.Thread(target=background_runner, daemon=True)
    t.start()

    # Optional admin startup alert
    send_admin_notification(
        f"✅ Notifier v2.0.0 started at {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        include_system_info=True,
    )

    # Startup banner
    ver = _get_app_version()
    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}🔔  Notifier  {Fore.WHITE}v{ver}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}{'═'*41}{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}✅  Background scheduler started{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}{Style.DIM}🕐  Timezone: {Fore.YELLOW}{Style.BRIGHT}{_tz_label()}{Style.RESET_ALL}")
    try:
        interval_h = int(os.getenv('HEARTBEAT_INTERVAL', '0'))
        if interval_h > 0:
            print(f"  {Fore.MAGENTA}💓  Heartbeat every {interval_h}h{Style.RESET_ALL}")
    except ValueError:
        pass
    if not NOTIFICATIONS_AVAILABLE:
        print(f"  {Fore.YELLOW}⚠️   Desktop notifications disabled (install plyer){Style.RESET_ALL}")
    print()

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


if __name__ == "__main__":
    main()
