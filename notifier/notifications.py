"""
Notification delivery layer.

This module contains the logic for:
- Sending messages through different channels (Telegram, Discord, etc.)
- The core `send_notifications()` function that finds due reminders and delivers them.

Both the CLI (`notifier.py`) and the web UI can import from here.
"""

from __future__ import annotations

import os
import logging
from datetime import datetime
from typing import Any

import requests

from .db import get_db, init_db

# ---------------------------------------------------------------------------
# Logging setup (used by both CLI and web container)
# ---------------------------------------------------------------------------
logger = logging.getLogger("notifier.notifications")

def configure_logging(level: int = logging.INFO):
    """Call this once at startup (web lifespan or CLI)."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    return logger

# ---------------------------------------------------------------------------
# Quiet mode for when running inside web container (no terminal spam)
# ---------------------------------------------------------------------------
_QUIET = False

def set_quiet_mode(quiet: bool):
    global _QUIET
    _QUIET = quiet


def _cprint(*args, **kwargs):
    if not _QUIET:
        print(*args, **kwargs)


# ---------------------------------------------------------------------------
# Channel Senders
# ---------------------------------------------------------------------------

def send_telegram_message(message: str) -> tuple[bool, str]:
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')

    if not bot_token or not chat_id:
        missing = []
        if not bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        msg = f"Missing {', '.join(missing)} in .env file"
        logger.warning(msg)
        return False, msg

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        if r.status_code == 200:
            logger.info("Telegram message sent successfully")
            _cprint("✅ Telegram message sent!")
            return True, "Sent successfully"
        else:
            error_msg = f"Telegram API error {r.status_code}: {r.text[:300]}"
            logger.error(error_msg)
            return False, error_msg
    except requests.exceptions.RequestException as e:
        logger.error(f"Telegram request failed: {e}")
        return False, f"Network error: {str(e)}"


def send_discord_message(message: str) -> tuple[bool, str]:
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        return False, "Missing DISCORD_WEBHOOK_URL"
    try:
        r = requests.post(webhook_url, json={"content": message}, timeout=10)
        if r.status_code in (200, 204):
            _cprint("✅ Discord message sent!")
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code} - {r.text}"
    except requests.exceptions.RequestException as e:
        return False, str(e)


def send_pushover_message(message: str) -> tuple[bool, str]:
    user_key = os.getenv('PUSHOVER_USER_KEY')
    api_token = os.getenv('PUSHOVER_API_TOKEN')
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
        return False, f"HTTP {r.status_code} - {r.text}"
    except requests.exceptions.RequestException as e:
        return False, str(e)


def send_email_message(message: str, subject: str = "⏰ Reminder") -> tuple[bool, str]:
    # Simplified version - full implementation is in the original notifier.py
    # For now we return not implemented so it doesn't break
    return False, "Email sending not yet moved to shared module"


# ---------------------------------------------------------------------------
# Channel Registry (used by both CLI and web)
# ---------------------------------------------------------------------------

CHANNELS: list[dict[str, Any]] = [
    {
        "name": "telegram",
        "label": "Telegram",
        "emoji": "📱",
        "send": send_telegram_message,
        "fields": [
            {"key": "TELEGRAM_BOT_TOKEN", "prompt": "Bot Token"},
            {"key": "TELEGRAM_CHAT_ID", "prompt": "Chat ID"},
        ],
    },
    {
        "name": "discord",
        "label": "Discord",
        "emoji": "💬",
        "send": send_discord_message,
        "fields": [
            {"key": "DISCORD_WEBHOOK_URL", "prompt": "Webhook URL"},
        ],
    },
    # Pushover and Email can be added here later
]


def _channel_configured(channel: dict) -> bool:
    for field in channel.get("fields", []):
        if not os.getenv(field["key"]):
            return False
    return True


# ---------------------------------------------------------------------------
# Core Delivery Logic
# ---------------------------------------------------------------------------

def _deliver(channel: dict, message: str, subject: str | None = None) -> tuple[bool, str]:
    """Deliver message through one channel."""
    send_func = channel["send"]
    try:
        if channel["name"] == "email" and subject:
            return send_func(message, subject=subject)
        return send_func(message)
    except Exception as e:
        return False, str(e)


def send_notifications(verbose: bool = False, only_id: int | None = None) -> None:
    """
    Main function that finds due notifications and delivers them.
    Called by APScheduler every minute (and manually via CLI).
    """
    now_ts = int(time.time())

    with get_db() as conn:
        c = conn.cursor()

        if only_id is not None:
            c.execute(
                "SELECT id, message, due_ts, recurrence, repeat_time FROM notifications WHERE id = ?",
                (only_id,),
            )
        else:
            c.execute(
                "SELECT id, message, due_ts, recurrence, repeat_time "
                "FROM notifications WHERE sent = 0 AND due_ts <= ? "
                "ORDER BY due_ts ASC",
                (now_ts,),
            )

        pending = c.fetchall()

        if not pending:
            return

        logger.info(f"Found {len(pending)} due notification(s) to send")

        for row in pending:
            nid, msg, orig_due_ts, recurrence, repeat_time = row
            full_msg = f"⏰ Reminder: {msg}"

            logger.info(f"Processing reminder #{nid}: {msg[:80]}...")

            any_success = False

            for chan in CHANNELS:
                ok, resp = _deliver(chan, full_msg)

                status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")

                # Always log the attempt
                log_message = f"Reminder #{nid} → {chan['name']}: {status} ({resp})"
                if ok:
                    logger.info(log_message)
                else:
                    logger.warning(log_message)

                # Write to DB logs table
                try:
                    ts = datetime.utcnow().isoformat()
                    c.execute(
                        "INSERT INTO logs (notification_id, timestamp, channel, status, response) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (nid, ts, chan["name"], status, resp[:500]),
                    )
                except Exception as db_err:
                    logger.error(f"Failed to write log for reminder #{nid}: {db_err}")

                if ok:
                    any_success = True

            if any_success:
                c.execute("UPDATE notifications SET sent=1 WHERE id=?", (nid,))
                logger.info(f"Reminder #{nid} marked as sent")
            else:
                logger.error(f"Reminder #{nid} failed on all channels")

            # TODO: Add recurrence handling here (copy from original notifier.py when needed)
            conn.commit()
