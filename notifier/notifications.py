"""
Notification delivery layer — the single source of truth for sending.

Both the CLI (`notifier.py`) and the FastAPI web UI import from here:
- Channel senders (Telegram, Discord, Pushover, Gmail)
- The channel registry (`CHANNELS`)
- Bounded retry/backoff delivery (`_deliver`)
- The audit-log writer (`db_log`)
- The core scheduler entry point (`send_notifications`) with recurrence
- The multi-channel heartbeat (`send_heartbeat`)

Credentials resolve DB-first (set via the web UI) then fall back to environment
variables (the classic `.env` method used by the CLI), so both front-ends work
against the same delivery code.
"""

from __future__ import annotations

import logging
import os
import platform
import smtplib
import socket
import sys
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

import requests

from .db import (
    get_db,
    get_event,
    get_setting,
    set_setting,
    format_event_message,
    _from_ts,
    _next_recurrence_ts,
    _now_in_tz,
    _parse_event_date,
    _tz_label,
)

# Optional desktop toasts (plyer). Absent/headless environments degrade quietly.
try:
    from plyer import notification as _desktop  # type: ignore
except Exception:  # pragma: no cover - plyer not installed (e.g. web container)
    _desktop = None

# On headless Linux (no DISPLAY/WAYLAND_DISPLAY) plyer shells out to notify-send
# and fails with a GDBus error — disable desktop toasts there.
if _desktop is not None and sys.platform.startswith("linux"):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        _desktop = None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("notifier.notifications")


def configure_logging(level: int = logging.INFO):
    """Attach a stream handler once (used by the web lifespan and CLI)."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    return logger


# ---------------------------------------------------------------------------
# Quiet mode — suppress sender chatter while a background scheduler is running
# so it never scrambles an interactive CLI menu (or spams web container logs).
# ---------------------------------------------------------------------------
_QUIET = False


def set_quiet_mode(quiet: bool) -> None:
    global _QUIET
    _QUIET = quiet


def _cprint(*args, **kwargs) -> None:
    if not _QUIET:
        print(*args, **kwargs)


# ---------------------------------------------------------------------------
# Credential resolution: Database (web UI) first, then environment (.env)
# ---------------------------------------------------------------------------

def _get_credential(channel: str, key: str) -> str:
    """Resolve one credential. Priority: DB setting > environment variable."""
    db_key = f"{channel}_{key}".lower()
    value = get_setting(db_key)
    if value:
        return value
    env_key = f"{channel.upper()}_{key.upper()}"
    return os.getenv(env_key, "")


def get_channel_credentials(channel_name: str) -> dict:
    """Return all configured credentials for a channel (keys without the prefix)."""
    creds: dict[str, str] = {}
    channel = next((c for c in CHANNELS if c["name"] == channel_name), None)
    if not channel:
        return creds
    for field in channel.get("fields", []):
        key = field["key"].replace(f"{channel_name.upper()}_", "")
        creds[key] = _get_credential(channel_name, key)
    return creds


def set_channel_credential(channel_name: str, key: str, value: str) -> None:
    """Persist a credential for a channel into the database (web UI path)."""
    set_setting(f"{channel_name}_{key}".lower(), value)


# ---------------------------------------------------------------------------
# Channel senders — all return (ok: bool, response: str)
#
# Failure responses are normalized to "HTTP <code> - <body>" (or "Missing ...")
# so `_is_transient` can classify them for retry. Do not change that contract
# without updating `_is_transient`.
# ---------------------------------------------------------------------------

def send_telegram_message(message: str) -> tuple[bool, str]:
    bot_token = _get_credential("telegram", "bot_token")
    chat_id = _get_credential("telegram", "chat_id")
    if not bot_token or not chat_id:
        return False, "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        if r.status_code == 200:
            _cprint("✅ Telegram message sent!")
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code} - {r.text[:300]}"
    except requests.exceptions.RequestException as e:
        return False, str(e)


def send_discord_message(message: str) -> tuple[bool, str]:
    webhook_url = _get_credential("discord", "webhook_url")
    if not webhook_url:
        return False, "Missing DISCORD_WEBHOOK_URL"
    try:
        r = requests.post(webhook_url, json={"content": message}, timeout=10)
        if r.status_code in (200, 204):
            _cprint("✅ Discord message sent!")
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code} - {r.text[:300]}"
    except requests.exceptions.RequestException as e:
        return False, str(e)


def send_pushover_message(message: str) -> tuple[bool, str]:
    user_key = _get_credential("pushover", "user_key")
    api_token = _get_credential("pushover", "api_token")
    if not user_key or not api_token:
        return False, "Missing PUSHOVER_USER_KEY or PUSHOVER_API_TOKEN"
    url = "https://api.pushover.net/1/messages.json"
    try:
        r = requests.post(
            url, data={"token": api_token, "user": user_key, "message": message}, timeout=10
        )
        if r.status_code == 200:
            _cprint("✅ Pushover message sent!")
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code} - {r.text[:300]}"
    except requests.exceptions.RequestException as e:
        return False, str(e)


def send_email_message(message: str, subject: str = "⏰ Notification Reminder") -> tuple[bool, str]:
    smtp_server = _get_credential("email", "smtp_server") or "smtp.gmail.com"
    smtp_port = int(_get_credential("email", "smtp_port") or "587")
    sender = _get_credential("email", "sender")
    password = _get_credential("email", "password")
    recipient = _get_credential("email", "recipient")
    if not all([sender, password, recipient]):
        return False, "Missing EMAIL_SENDER or EMAIL_PASSWORD or EMAIL_RECIPIENT"
    try:
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(message, "plain"))
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        _cprint("✅ Email sent!")
        return True, "Email sent"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Channel registry — single source of truth for delivery.
#
# `secret`/`mask`/`display_extra` are presentation hints consumed by the CLI;
# the web UI ignores keys it doesn't use. No Fore/colour here so this module
# stays importable in a headless web container without colorama.
# ---------------------------------------------------------------------------

CHANNELS: list[dict[str, Any]] = [
    {
        "name": "telegram", "label": "Telegram", "emoji": "📱",
        "send": send_telegram_message,
        "fields": [
            {"key": "TELEGRAM_BOT_TOKEN", "prompt": "Bot Token", "secret": False, "mask": True},
            {"key": "TELEGRAM_CHAT_ID", "prompt": "Chat ID", "secret": False, "mask": False},
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
        "name": "discord", "label": "Discord", "emoji": "💬",
        "send": send_discord_message,
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
        "name": "pushover", "label": "Pushover", "emoji": "📲",
        "send": send_pushover_message,
        "fields": [
            {"key": "PUSHOVER_USER_KEY", "prompt": "User Key", "secret": False, "mask": True},
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
        "name": "email", "label": "Gmail", "emoji": "📧",
        "send": send_email_message,
        "fields": [
            {"key": "EMAIL_SENDER", "prompt": "Sender Email", "secret": False, "mask": True},
            {"key": "EMAIL_PASSWORD", "prompt": "App Password", "secret": True, "mask": True},
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


def _channel_configured(chan: dict) -> bool:
    """True when every required credential for this channel resolves (DB or env)."""
    creds = get_channel_credentials(chan["name"])
    return bool(creds) and all(creds.values())


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def db_log(notification_id: Optional[int], channel: str, status: str,
           response: Optional[str] = None) -> None:
    """Write one send-attempt record to the logs table (and the logger)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO logs (notification_id, timestamp, channel, status, response)"
                " VALUES (?, ?, ?, ?, ?)",
                (notification_id, ts, channel, status, (response or "")[:500]),
            )
            conn.commit()
        logger.info("%s | %s | %s | nid=%s | %s", ts, channel, status, notification_id, response)
    except Exception as exc:
        logger.error("db_log failed: %s", exc)


# ---------------------------------------------------------------------------
# Bounded retry / backoff delivery
# ---------------------------------------------------------------------------

def _is_transient(resp: str) -> bool:
    """Heuristic: is this failure worth retrying?

    Retry network blips, timeouts, 5xx and 429. Do NOT retry a missing-config
    skip or a hard 4xx (bad token / forbidden) — those won't self-heal in a few
    seconds and would just delay the other channels.
    """
    if not resp:
        return False
    r = resp.lower()
    if "missing" in r:
        return False
    if "http 429" in r or "http 5" in r:
        return True
    if "http 4" in r:            # 4xx other than 429 → permanent
        return False
    return True                 # no HTTP code → transport-level error


def _deliver(chan: dict, message: str, subject: Optional[str] = None,
             retries: int = 2, backoff: float = 1.5) -> tuple[bool, str]:
    """Send through one channel with bounded retry on transient failures.

    The single chokepoint for delivery so retry/backoff lives in one place.
    Only the email sender takes a subject; everything else ignores it.
    Returns (ok, response) from the final attempt.
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
        logger.warning("Channel %s failed (%s) — retry %d/%d in %.1fs",
                       chan["name"], resp, attempt, retries, delay)
        time.sleep(delay)
        ok, resp = _attempt()
        attempt += 1
    return ok, resp


# ---------------------------------------------------------------------------
# Core scheduler entry point
# ---------------------------------------------------------------------------

def _desktop_notify(title: str, message: str) -> None:
    notify = getattr(_desktop, "notify", None) if _desktop is not None else None
    if callable(notify):
        try:
            notify(title=title, message=message, timeout=10)
        except Exception:
            pass


def send_notifications(verbose: bool = False, only_id: Optional[int] = None) -> None:
    """Deliver due notifications.

    With ``only_id`` set, force-send that one regardless of its due time / sent
    flag (used by 'send now for ID' and --send-id). A notification is marked
    sent when at least one channel succeeds; recurring ones are re-armed for
    their next occurrence.
    """
    now_ts = int(time.time())
    with get_db() as conn:
        c = conn.cursor()
        if only_id is not None:
            c.execute(
                "SELECT id, message, due_ts, recurrence, repeat_time, event_id"
                " FROM notifications WHERE id = ?",
                (only_id,),
            )
        else:
            c.execute(
                "SELECT id, message, due_ts, recurrence, repeat_time, event_id"
                " FROM notifications WHERE sent = 0 AND due_ts <= ?"
                " ORDER BY due_ts ASC",
                (now_ts,),
            )
        pending = c.fetchall()

        if not pending:
            if verbose:
                msg = (f"❌ No notification with ID {only_id}." if only_id is not None
                       else "⚠️  No due notifications right now.")
                _cprint(msg)
            return

        logger.info("Found %d due notification(s) to send", len(pending))

        # Stale-tick skip: if downtime left several ticks of the same event
        # overdue (daily cadence especially), deliver only the most current
        # one and retire the rest quietly — never spam a backlog.
        if only_id is None:
            latest_by_event = {}
            for row in pending:
                ev_id = row["event_id"]
                if ev_id is not None:
                    best = latest_by_event.get(ev_id)
                    if best is None or row["due_ts"] > best["due_ts"]:
                        latest_by_event[ev_id] = row
            stale_ids = {
                row["id"] for row in pending
                if row["event_id"] is not None
                and row["id"] != latest_by_event[row["event_id"]]["id"]
            }
            if stale_ids:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for sid in stale_ids:
                    c.execute("UPDATE notifications SET sent = 1 WHERE id = ?", (sid,))
                    c.execute(
                        "INSERT INTO logs (notification_id, timestamp, channel, status, response)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (sid, ts, "system", "SKIPPED_STALE",
                         "Outdated countdown tick superseded by a newer one"),
                    )
                    logger.info("%s | system | SKIPPED_STALE | nid=%s | "
                                "Outdated countdown tick superseded by a newer one", ts, sid)
                conn.commit()
                logger.info("Skipped %d stale event tick(s)", len(stale_ids))
                pending = [row for row in pending if row["id"] not in stale_ids]

        for row in pending:
            nid, msg, orig_due_ts, recurrence, repeat_time, ev_id = (
                row["id"], row["message"], row["due_ts"], row["recurrence"],
                row["repeat_time"], row["event_id"],
            )

            # Re-render the countdown at delivery for event-linked ticks. The
            # stored text is frozen at expansion time, so a tick delivered on a
            # later calendar day than scheduled (e.g. after the notifier was
            # down) would otherwise ship a stale day-count — that's the "22 days
            # when it's really 14" bug. Recomputing here makes late sends
            # self-correct to today's real number.
            if ev_id is not None:
                event = get_event(ev_id)
                if event:
                    live_days = event.get("days_left")
                    if live_days is not None and live_days >= 0:
                        msg = format_event_message(
                            event["title"], live_days, event["target_date"],
                            event.get("category"), event.get("details"),
                        )

            _cprint(f"📢 Sending: {msg}")
            _desktop_notify("⏰ Reminder!", msg)

            full_msg = f"⏰ Reminder: {msg}"
            any_success = False
            for chan in CHANNELS:
                ok, resp = _deliver(chan, full_msg, subject="⏰ Reminder")
                status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
                db_log(nid, chan["name"], status, resp)
                if ok:
                    any_success = True

            if any_success:
                c.execute("UPDATE notifications SET sent = 1 WHERE id = ?", (nid,))
                if recurrence:
                    next_ts = _next_recurrence_ts(orig_due_ts, recurrence, repeat_time)
                    if next_ts:
                        next_due_str = _from_ts(next_ts).strftime("%Y-%m-%d %H:%M")
                        # Recompute the days count for event-linked recurrences
                        # so the message stays accurate (e.g. daily countdowns
                        # never get stuck saying "30 days" forever).
                        next_msg = msg
                        if ev_id is not None:
                            event = get_event(ev_id)
                            if event:
                                target_d = _parse_event_date(event["target_date"])
                                if target_d:
                                    next_fire_date = _from_ts(next_ts).date()
                                    actual_days = (target_d - next_fire_date).days
                                    if actual_days >= 0:
                                        next_msg = format_event_message(
                                            event["title"], actual_days,
                                            event["target_date"],
                                            event.get("category"),
                                            event.get("details"),
                                        )
                        c.execute(
                            "INSERT INTO notifications"
                            " (message, due_time, due_ts, recurrence, repeat_time, event_id)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (next_msg, next_due_str, next_ts, recurrence, repeat_time, ev_id),
                        )
                        _cprint(f"🔁 Next {recurrence}: {next_due_str}")
                conn.commit()
                _cprint(f"✅ ID {nid} marked sent.")
            else:
                conn.commit()
                _cprint(f"❌ ID {nid} not marked sent — no channel succeeded.")

        if verbose:
            _cprint(f"✅ Processed {len(pending)} notification(s).")


# ---------------------------------------------------------------------------
# Heartbeat — pings every configured channel
# ---------------------------------------------------------------------------

def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def _remote_ip() -> str:
    try:
        r = requests.get("https://api.ipify.org", timeout=5)
        return r.text.strip()
    except Exception:
        return "unknown"


def _db_ok() -> bool:
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def send_heartbeat() -> None:
    """Send a heartbeat ping to every configured channel with system info."""
    now = _now_in_tz()

    services_lines: list[str] = []
    for ch in CHANNELS:
        ok = _channel_configured(ch)
        icon = "✅" if ok else "⚠️"
        label = "configured" if ok else "not configured"
        services_lines.append(f"  {icon} {ch['name']} — {label}")
    db_icon = "✅" if _db_ok() else "❌"
    db_label = "ok" if db_icon == "✅" else "error"
    services_lines.append(f"  {db_icon} database (sqlite) — {db_label}")

    uname = platform.uname()
    os_str = f"{uname.system} {uname.release} ({uname.machine})"

    msg = (
        f"💓 Notifier Heartbeat — {now.strftime('%Y-%m-%d')} ({_tz_label()})\n\n"
        f"Services:\n" + "\n".join(services_lines) + "\n\n"
        f"Network:\n"
        f"  Remote IP: {_remote_ip()}\n"
        f"  Local IP: {_local_ip()}\n\n"
        f"System:\n"
        f"  OS: {os_str}\n"
        f"  Host: {socket.gethostname()}\n"
        f"  Python: {platform.python_version()}\n"
        f"  Time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({_tz_label()})"
    )
    logger.info("Heartbeat: %s", msg)
    _desktop_notify("💓 Heartbeat", "Notifier is running")

    if not any(_channel_configured(ch) for ch in CHANNELS):
        db_log(None, "heartbeat", "LOGGED", "No services configured — heartbeat logged only")
        return

    _cprint("💓 Sending heartbeat...")
    for chan in CHANNELS:
        ok, resp = chan["send"](msg)
        status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
        db_log(None, f"heartbeat_{chan['name']}", status, resp)
