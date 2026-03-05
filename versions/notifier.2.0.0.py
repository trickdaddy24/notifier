# -*- coding: utf-8 -*-

import os
import sqlite3
import time
import threading
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dotenv import load_dotenv, set_key, find_dotenv, dotenv_values
import requests
import schedule
from colorama import init, Fore, Style
import platform
import socket
import json
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import random

try:
    from plyer import notification
    NOTIFICATIONS_AVAILABLE = True
except Exception:
    NOTIFICATIONS_AVAILABLE = False

if os.path.exists("multi_channel_notifier.log") and os.path.getsize("multi_channel_notifier.log") > 5_000_000:
    try:
        os.replace("multi_channel_notifier.log", f"multi_channel_notifier.{int(time.time())}.log")
    except Exception:
        pass

init(autoreset=True)
logging.basicConfig(
    filename="multi_channel_notifier.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

ENV_PATH = find_dotenv(".env", usecwd=True) or ".env"
if not os.path.exists(ENV_PATH):
    open(ENV_PATH, "a").close()
load_dotenv(ENV_PATH)

DB_NAME = "notifications.db"

def get_timestamp():
    return datetime.now().strftime("%m-%d-%Y %H:%M:%S")

def due_str_to_epoch(due_str):
    formats = ["%m-%d-%Y %H:%M", "%m-%d-%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]
    for fmt in formats:
        try:
            dt = datetime.strptime(due_str, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue
    raise ValueError("Date format not recognized. Use MM-DD-YYYY HH:MM or YYYY-MM-DD HH:MM")

def epoch_to_due_str(epoch):
    return datetime.fromtimestamp(epoch).strftime("%m-%d-%Y %H:%M")

def load_env_vars():
    return {
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID"),
        "DISCORD_WEBHOOK_URL": os.getenv("DISCORD_WEBHOOK_URL"),
        "PUSHOVER_USER_KEY": os.getenv("PUSHOVER_USER_KEY"),
        "PUSHOVER_API_TOKEN": os.getenv("PUSHOVER_API_TOKEN"),
        "EMAIL_SMTP_SERVER": os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com"),
        "EMAIL_SMTP_PORT": os.getenv("EMAIL_SMTP_PORT", "587"),
        "EMAIL_SENDER": os.getenv("EMAIL_SENDER"),
        "EMAIL_PASSWORD": os.getenv("EMAIL_PASSWORD"),
        "EMAIL_RECIPIENT": os.getenv("EMAIL_RECIPIENT"),
    }

def save_env_key(key, value):
    set_key(ENV_PATH, key, value)
    os.environ[key] = value

def masked(val):
    if not val:
        return "(not set)"
    if len(val) <= 6:
        return "******"
    return val[:3] + "..." + val[-3:]

def get_db():
    """Get a thread-safe connection configured for fewer locks."""
    conn = sqlite3.connect(DB_NAME, timeout=10, isolation_level=None)  # autocommit
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            due_time TEXT NOT NULL,
            due_ts INTEGER NOT NULL,
            sent INTEGER DEFAULT 0,
            recurrence TEXT DEFAULT NULL
        )
    ''')
    try:
        c.execute("ALTER TABLE notifications ADD COLUMN recurrence TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    c.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_id INTEGER,
            timestamp TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            response TEXT
        )
    ''')
    # Indexes to speed up periodic scans and log reads
    c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_due ON notifications(sent, due_ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(timestamp)")
    conn.commit()
    conn.close()

def db_log(notification_id, channel, status, response=None):
    ts = get_timestamp()
    with get_db() as conn:
        c = conn.cursor()
        c.execute('INSERT INTO logs (notification_id, timestamp, channel, status, response) VALUES (?, ?, ?, ?, ?)',
                  (notification_id, ts, channel, status, response))
        conn.commit()
    logging.info(f"{ts} | {channel} | {status} | nid={notification_id} | {response}")

def send_telegram_message(message):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return False, "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code} - {r.text}"
    except Exception as e:
        return False, str(e)

def verify_telegram_config():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print(f"{Fore.RED}Telegram token missing.{Style.RESET_ALL}")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and r.json().get("ok"):
            username = r.json()["result"].get("username", "unknown")
            print(f"{Fore.GREEN}Telegram bot OK: @{username}{Style.RESET_ALL}")
            return True
        print(f"{Fore.RED}Telegram verification failed: {r.status_code}{Style.RESET_ALL}")
        return False
    except Exception as e:
        print(f"{Fore.RED}Telegram verify error: {e}{Style.RESET_ALL}")
        return False

def send_discord_message(message):
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        return False, "Missing DISCORD_WEBHOOK_URL"
    payload = {"content": message}
    try:
        r = requests.post(webhook, json=payload, timeout=10)
        if r.status_code in (200, 204):
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code} - {r.text}"
    except Exception as e:
        return False, str(e)

def send_pushover_message(message):
    user = os.getenv("PUSHOVER_USER_KEY")
    token = os.getenv("PUSHOVER_API_TOKEN")
    if not user or not token:
        return False, "Missing PUSHOVER_USER_KEY or PUSHOVER_API_TOKEN"
    url = "https://api.pushover.net/1/messages.json"
    payload = {"token": token, "user": user, "message": message}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code} - {r.text}"
    except Exception as e:
        return False, str(e)

def send_email_message(message, subject="Notification"):
    smtp_server = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    sender = os.getenv("EMAIL_SENDER") or ""
    password = os.getenv("EMAIL_PASSWORD") or ""
    recipient = os.getenv("EMAIL_RECIPIENT") or ""
    if not all([sender, password, recipient]):
        return False, "Missing EMAIL_SENDER or EMAIL_PASSWORD or EMAIL_RECIPIENT"
    try:
        msg = MIMEMultipart()
        msg['From'] = str(sender)
        msg['To'] = str(recipient)
        msg['Subject'] = str(subject)
        msg.attach(MIMEText(message, 'plain'))
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        return True, "Email sent"
    except Exception as e:
        return False, str(e)

def add_notification():
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}📝 Add New Reminder (3-Step Process){Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")

    print(f"{Fore.YELLOW}Step 1: Enter reminder message (max 4000 characters):{Style.RESET_ALL}")
    msg = input().strip()
    if not msg:
        print(f"{Fore.RED}❌ Error: Message cannot be empty.{Style.RESET_ALL}")
        return
    if len(msg) > 4000:
        print(f"{Fore.RED}❌ Error: Message exceeds 4000 characters (current: {len(msg)}).{Style.RESET_ALL}")
        return

    print(f"{Fore.YELLOW}Step 2: Enter due time (MM-DD-YYYY HH:MM or YYYY-MM-DD HH:MM):{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Example: 10-15-2025 09:00 or 2025-10-15 09:00{Style.RESET_ALL}")
    due_in = input().strip()
    try:
        due_ts = due_str_to_epoch(due_in)
        due_text = epoch_to_due_str(due_ts)
    except ValueError:
        print(f"{Fore.RED}❌ Error: Invalid date format. Use MM-DD-YYYY HH:MM or YYYY-MM-DD HH:MM.{Style.RESET_ALL}")
        return

    print(f"{Fore.YELLOW}Step 2b: Enter recurrence (daily, weekly, biweekly, or leave blank for one-time):{Style.RESET_ALL}")
    recurrence = input().strip().lower()
    if recurrence not in ("", "daily", "weekly", "biweekly"):
        print(f"{Fore.RED}❌ Error: Invalid recurrence. Use daily, weekly, biweekly, or leave blank.{Style.RESET_ALL}")
        return

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM notifications WHERE message = ? AND due_time = ? AND recurrence = ?", (msg, due_text, recurrence or None))
        if c.fetchone():
            print(f"{Fore.RED}❌ Error: Identical reminder already exists.{Style.RESET_ALL}")
            return
        c.execute("INSERT INTO notifications (message, due_time, due_ts, recurrence) VALUES (?, ?, ?, ?)", (msg, due_text, due_ts, recurrence or None))
        nid = c.lastrowid
        conn.commit()
    print(f"{Fore.GREEN}✅ Added reminder ID {nid}: '{msg}' due {due_text} ({recurrence or 'one-time'}){Style.RESET_ALL}")
    db_log(nid, "system", "CREATED", f"Reminder added: {msg} due {due_text} recurrence {recurrence or 'none'}")

def view_notifications():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, message, due_time, sent, recurrence FROM notifications ORDER BY due_ts")
        rows = c.fetchall()
    if not rows:
        print(f"{Fore.YELLOW}No notifications found.{Style.RESET_ALL}")
        return
    print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    for r in rows:
        status = f"{Fore.GREEN}SENT{Style.RESET_ALL}" if r[3] else f"{Fore.YELLOW}PENDING{Style.RESET_ALL}"
        recurrence = r[4] or "None"
        print(f"{Fore.WHITE}ID: {r[0]} | Due: {r[2]} | Status: {status} | Recurrence: {recurrence}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}Message: {r[1]}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'-'*70}{Style.RESET_ALL}")

def delete_notification():
    print(f"{Fore.YELLOW}Enter notification ID to delete: {Style.RESET_ALL}", end="")
    nid = input().strip()
    if not nid.isdigit():
        print(f"{Fore.RED}Invalid ID.{Style.RESET_ALL}")
        return
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM notifications WHERE id = ?", (nid,))
        if not c.fetchone():
            print(f"{Fore.RED}Notification ID {nid} not found.{Style.RESET_ALL}")
            return
        c.execute("DELETE FROM notifications WHERE id = ?", (nid,))
        conn.commit()
    print(f"{Fore.GREEN}Deleted notification ID {nid}.{Style.RESET_ALL}")

def edit_notification():
    print(f"{Fore.YELLOW}Enter notification ID to edit: {Style.RESET_ALL}", end="")
    nid = input().strip()
    if not nid.isdigit():
        print(f"{Fore.RED}Invalid ID.{Style.RESET_ALL}")
        return
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, message, due_time, recurrence FROM notifications WHERE id = ?", (nid,))
        row = c.fetchone()
        if not row:
            print(f"{Fore.RED}Notification ID {nid} not found.{Style.RESET_ALL}")
            return
        print(f"{Fore.CYAN}Current message: {row[1]}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Current due: {row[2]}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Current recurrence: {row[3] or 'None'}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}New message (press Enter to keep): {Style.RESET_ALL}", end="")
        new_msg = input().strip() or row[1]
        print(f"{Fore.YELLOW}New due (MM-DD-YYYY HH:MM or YYYY-MM-DD HH:MM) (press Enter to keep): {Style.RESET_ALL}", end="")
        new_due = input().strip() or row[2]
        print(f"{Fore.YELLOW}New recurrence (daily, weekly, biweekly, or leave blank for one-time) (press Enter to keep): {Style.RESET_ALL}", end="")
        new_recurrence = input().strip().lower() or (row[3] or "")
        if new_recurrence not in ("", "daily", "weekly", "biweekly"):
            print(f"{Fore.RED}Invalid recurrence. Keeping original.{Style.RESET_ALL}")
            new_recurrence = row[3] or None
        try:
            new_due_ts = due_str_to_epoch(new_due) if new_due != row[2] else due_str_to_epoch(row[2])
            new_due_text = epoch_to_due_str(new_due_ts)
        except ValueError:
            print(f"{Fore.RED}Invalid date format. Keeping original.{Style.RESET_ALL}")
            new_due_text = row[2]
            new_due_ts = due_str_to_epoch(row[2])
        c.execute("UPDATE notifications SET message = ?, due_time = ?, due_ts = ?, recurrence = ? WHERE id = ?",
                  (new_msg, new_due_text, new_due_ts, new_recurrence or None, nid))
        conn.commit()
    print(f"{Fore.GREEN}Updated notification ID {nid}.{Style.RESET_ALL}")

def send_notifications():
    now_ts = int(time.time())
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, message, due_ts, recurrence FROM notifications WHERE due_ts <= ? AND sent = 0", (now_ts,))
        pending = c.fetchall()

        if not pending:
            print(f"{Fore.YELLOW}No pending notifications to send.{Style.RESET_ALL}")
            return

        for row in pending:
            nid, msg, orig_due_ts, recurrence = row[0], row[1], row[2], row[3]
            print(f"{Fore.GREEN}Sending notification ID {nid}: {msg}{Style.RESET_ALL}")

            if NOTIFICATIONS_AVAILABLE and notification is not None:
                try:
                    if hasattr(notification, "notify") and callable(notification.notify):
                        notification.notify(title="Reminder", message=msg, timeout=8)
                except Exception:
                    pass

            any_success = False

            ok, resp = send_telegram_message(f"⏰ Reminder: {msg}")
            status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
            db_log(nid, "telegram", status, resp)
            if ok: any_success = True

            ok, resp = send_discord_message(f"⏰ Reminder: {msg}")
            status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
            db_log(nid, "discord", status, resp)
            if ok: any_success = True

            ok, resp = send_pushover_message(f"⏰ Reminder: {msg}")
            status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
            db_log(nid, "pushover", status, resp)
            if ok: any_success = True

            ok, resp = send_email_message(f"⏰ Reminder: {msg}", subject="⏰ Reminder")
            status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")
            db_log(nid, "email", status, resp)
            if ok: any_success = True

            if any_success:
                # mark original as sent
                c.execute("UPDATE notifications SET sent = 1 WHERE id = ?", (nid,))

                if recurrence:
                    # step seconds for recurrence
                    if recurrence == "daily":
                        step = 24 * 3600
                    elif recurrence == "weekly":
                        step = 7 * 24 * 3600
                    elif recurrence == "biweekly":
                        step = 14 * 24 * 3600
                    else:
                        step = 0

                    next_due = orig_due_ts + step
                    # If behind, roll forward to the next future slot
                    while next_due <= now_ts and step > 0:
                        next_due += step

                    if step > 0:
                        next_due_text = epoch_to_due_str(next_due)
                        c.execute(
                            "INSERT INTO notifications (message, due_time, due_ts, recurrence) VALUES (?, ?, ?, ?)",
                            (msg, next_due_text, next_due, recurrence)
                        )
                        print(f"{Fore.GREEN}Recurring reminder added: '{msg}' due {next_due_text}{Style.RESET_ALL}")

                conn.commit()
                print(f"{Fore.GREEN}Notification ID {nid} marked sent.{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}Notification ID {nid} not marked sent (no channel succeeded).{Style.RESET_ALL}")

def send_heartbeat():
    """Sends heartbeat with randomized interval between 60-150 minutes"""
    intervals = [60, 90, 120, 150]  # minutes
    next_interval = random.choice(intervals)
    next_time = datetime.now() + timedelta(minutes=next_interval)
    next_time_str = next_time.strftime("%m-%d-%Y %H:%M:%S")

    message = f"🩺 Notifier heartbeat: Running at {get_timestamp()}"

    ok, resp = send_telegram_message(message)
    status = "SUCCESS" if ok else ("SKIPPED" if "Missing" in resp else "FAILED")

    log_msg = f"{resp} | Next heartbeat in {next_interval} mins at {next_time_str}"
    db_log(None, "telegram_heartbeat", status, log_msg)

    if ok:
        print(f"{Fore.GREEN}✅ Heartbeat sent: {message}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}⏱️  Next heartbeat in {next_interval} minutes at {next_time_str}{Style.RESET_ALL}")
    else:
        print(f"{Fore.RED}❌ Heartbeat failed: {resp}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}⏱️  Will retry in {next_interval} minutes at {next_time_str}{Style.RESET_ALL}")

    # Clear existing random heartbeat schedule and set new one
    schedule.clear('heartbeat')
    schedule.every(next_interval).minutes.do(send_heartbeat).tag('heartbeat')

    return next_interval

def background_runner():
    """Background scheduler that runs notification checks and heartbeats"""
    print(f"{Fore.CYAN}Background scheduler started (checking every minute).{Style.RESET_ALL}")

    # Schedule notification checker every minute (single source of truth)
    schedule.every(1).minutes.do(send_notifications).tag('send_notifications')

    # Send initial heartbeat immediately and schedule next one
    initial_interval = send_heartbeat()
    print(f"{Fore.CYAN}📡 Initial heartbeat sent. Random intervals: 60, 90, 120, or 150 minutes.{Style.RESET_ALL}")

    while True:
        try:
            schedule.run_pending()
        except Exception:
            logging.exception("Scheduler error")
        time.sleep(60)

def show_complete_env_example():
    print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}COMPLETE .env FILE EXAMPLE{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")
    print("TELEGRAM_BOT_TOKEN=your_bot_token_here")
    print("TELEGRAM_CHAT_ID=your_chat_id_here\n")
    print("DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...\n")
    print("PUSHOVER_USER_KEY=your_user_key")
    print("PUSHOVER_API_TOKEN=your_api_token\n")
    print("EMAIL_SMTP_SERVER=smtp.gmail.com")
    print("EMAIL_SMTP_PORT=587")
    print("EMAIL_SENDER=your_email@gmail.com")
    print("EMAIL_PASSWORD=your_app_password")
    print("EMAIL_RECIPIENT=recipient@example.com\n")
    input("Press Enter to continue...")

def telegram_menu():
    while True:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        status = f"{Fore.GREEN}CONFIGURED" if bot_token and chat_id else f"{Fore.RED}NOT CONFIGURED"
        print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}📨  TELEGRAM SERVICE  {Style.RESET_ALL}- {status}")
        print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")

        print("1️⃣  🔍  Verify Configuration")
        print("2️⃣  🧪  Send Test Message")
        print("3️⃣  🗒️   Show .env Example")
        print("0️⃣  🔙  Back")

        choice = input("Choose: ").strip()
        if choice == "1":
            verify_telegram_config()
        elif choice == "2":
            msg = input("Test message (Enter for default): ").strip() or "🧪 Test from notifier"
            ok, resp = send_telegram_message(msg)
            print(Fore.GREEN + "Success" if ok else Fore.RED + f"Failed: {resp}")
        elif choice == "3":
            print("TELEGRAM_BOT_TOKEN=your_bot_token_here")
            print("TELEGRAM_CHAT_ID=your_chat_id_here")
        elif choice == "0":
            break

def send_admin_notification(message, include_system_info=False):
    admin_token = os.getenv("TELEGRAM_ADMIN_BOT_TOKEN")
    admin_chat = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
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
            try:
                internal_ip = socket.gethostbyname(host)
            except Exception:
                pass

        external_ip = "Unknown"
        try:
            response = requests.get("https://api.ipify.org?format=json", timeout=5)
            if response.status_code == 200:
                external_ip = response.json().get("ip", "Unknown")
        except Exception:
            pass

        os_name = platform.system()
        os_ver = platform.version()
        py_ver = platform.python_version()
        cwd = os.getcwd()

        print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}System Information:{Style.RESET_ALL}")
        print(f"{Fore.WHITE}Host: {host}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}Internal IP: {internal_ip}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}External IP: {external_ip}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}OS: {os_name} ({os_ver}){Style.RESET_ALL}")
        print(f"{Fore.WHITE}Python: {py_ver}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}Working Dir: {cwd}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")

        message += (
            f"\n🖥️ Host: {host}"
            f"\n🌐 Internal IP: {internal_ip}"
            f"\n🌍 External IP: {external_ip}"
            f"\n💻 OS: {os_name} ({os_ver})"
            f"\n🐍 Python: {py_ver}"
            f"\n📁 Working Dir: {cwd}"
        )

    url = f"https://api.telegram.org/bot{admin_token}/sendMessage"
    payload = {"chat_id": admin_chat, "text": message}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return True, "Sent"
        return False, f"HTTP {r.status_code} - {r.text}"
    except Exception as e:
        return False, str(e)

def discord_menu():
    while True:
        webhook = os.getenv("DISCORD_WEBHOOK_URL")
        status = f"{Fore.GREEN}CONFIGURED" if webhook else f"{Fore.RED}NOT CONFIGURED"
        print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}💬  DISCORD SERVICE  {Style.RESET_ALL}- {status}")
        print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")

        print("1️⃣  🧪  Send Test Message")
        print("2️⃣  🗒️   Show .env Example")
        print("0️⃣  🔙  Back")

        choice = input("Choose: ").strip()
        if choice in ("1",):
            msg = input("Test message (Enter for default): ").strip() or "🧪 Test from notifier"
            ok, resp = send_discord_message(msg)
            print(Fore.GREEN + "Success" if ok else Fore.RED + f"Failed: {resp}")
        elif choice == "2":
            print("DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...")
        elif choice == "0":
            break

def pushover_menu():
    while True:
        user, token = os.getenv("PUSHOVER_USER_KEY"), os.getenv("PUSHOVER_API_TOKEN")
        status = f"{Fore.GREEN}CONFIGURED" if user and token else f"{Fore.RED}NOT CONFIGURED"
        print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}🔔  PUSHOVER SERVICE  {Style.RESET_ALL}- {status}")
        print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")

        print("1️⃣  🧪  Send Test Message")
        print("2️⃣  🗒️   Show .env Example")
        print("0️⃣  🔙  Back")

        choice = input("Choose: ").strip()
        if choice in ("1",):
            msg = input("Test message (Enter for default): ").strip() or "🧪 Test from notifier"
            ok, resp = send_pushover_message(msg)
            print(Fore.GREEN + "Success" if ok else Fore.RED + f"Failed: {resp}")
        elif choice == "2":
            print("PUSHOVER_USER_KEY=your_user_key")
            print("PUSHOVER_API_TOKEN=your_api_token")
        elif choice == "0":
            break

def email_menu():
    while True:
        sender, password, recipient = os.getenv("EMAIL_SENDER"), os.getenv("EMAIL_PASSWORD"), os.getenv("EMAIL_RECIPIENT")
        status = f"{Fore.GREEN}CONFIGURED" if sender and password and recipient else f"{Fore.RED}NOT CONFIGURED"
        print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}📧  EMAIL SERVICE  {Style.RESET_ALL}- {status}")
        print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")

        print("1️⃣  🧪  Send Test Message")
        print("2️⃣  🗒️   Show .env Example")
        print("0️⃣  🔙  Back")

        choice = input("Choose: ").strip()
        if choice in ("1",):
            msg = input("Test message (Enter for default): ").strip() or "🧪 Test from notifier"
            ok, resp = send_email_message(msg, subject="Test message")
            print(Fore.GREEN + "Success" if ok else Fore.RED + f"Failed: {resp}")
        elif choice == "2":
            print("EMAIL_SMTP_SERVER=smtp.gmail.com")
            print("EMAIL_SMTP_PORT=587")
            print("EMAIL_SENDER=your_email@gmail.com")
            print("EMAIL_PASSWORD=your_app_password")
            print("EMAIL_RECIPIENT=recipient@example.com")
        elif choice == "0":
            break

def notification_services_menu():
    while True:
        tg = "✅" if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID") else "❌"
        dc = "✅" if os.getenv("DISCORD_WEBHOOK_URL") else "❌"
        po = "✅" if os.getenv("PUSHOVER_USER_KEY") and os.getenv("PUSHOVER_API_TOKEN") else "❌"
        em = "✅" if os.getenv("EMAIL_SENDER") and os.getenv("EMAIL_PASSWORD") and os.getenv("EMAIL_RECIPIENT") else "❌"

        print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}⚙️  NOTIFICATION SERVICES{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")

        print(f"1️⃣  📨  Telegram [{tg}]")
        print(f"2️⃣  💬  Discord [{dc}]")
        print(f"3️⃣  🔔  Pushover [{po}]")
        print(f"4️⃣  📧  Email [{em}]")
        print(f"5️⃣  🗒️   Show .env Example")
        print("0️⃣  🔙  Back")

        choice = input("Choose: ").strip()
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

def config_menu():
    session = dotenv_values(ENV_PATH)
    session = {**load_env_vars(), **session}
    session = {k: (v or "") for k, v in session.items()}
    while True:
        print(f"\n.ENV CONFIGURATION")
        print("1. Telegram")
        print("2. Discord")
        print("3. Pushover")
        print("4. Email")
        print("5. Save session credentials to .env")
        print("0. Back")
        choice = input("Choose: ").strip()
        if choice == "1":
            print(f"TELEGRAM_BOT_TOKEN = {masked(session.get('TELEGRAM_BOT_TOKEN'))}")
            print(f"TELEGRAM_CHAT_ID  = {session.get('TELEGRAM_CHAT_ID') or '(not set)'}")
            sub = input("Edit (e) / Back (Enter): ").strip().lower()
            if sub == "e":
                val = input("Enter TELEGRAM_BOT_TOKEN (empty to keep): ").strip()
                if val:
                    session['TELEGRAM_BOT_TOKEN'] = val
                val = input("Enter TELEGRAM_CHAT_ID (empty to keep): ").strip()
                if val:
                    session['TELEGRAM_CHAT_ID'] = val
                os.environ.update({k: v for k, v in session.items() if v is not None})
        elif choice == "2":
            print(f"DISCORD_WEBHOOK_URL = {masked(session.get('DISCORD_WEBHOOK_URL'))}")
            sub = input("Edit (e) / Back (Enter): ").strip().lower()
            if sub == "e":
                val = input("Enter DISCORD_WEBHOOK_URL (empty to keep): ").strip()
                if val:
                    session['DISCORD_WEBHOOK_URL'] = val
                os.environ.update({k: v for k, v in session.items() if v is not None})
        elif choice == "3":
            print(f"PUSHOVER_USER_KEY  = {masked(session.get('PUSHOVER_USER_KEY'))}")
            print(f"PUSHOVER_API_TOKEN = {masked(session.get('PUSHOVER_API_TOKEN'))}")
            sub = input("Edit (e) / Back (Enter): ").strip().lower()
            if sub == "e":
                val = input("Enter PUSHOVER_USER_KEY (empty to keep): ").strip()
                if val:
                    session['PUSHOVER_USER_KEY'] = val
                val = input("Enter PUSHOVER_API_TOKEN (empty to keep): ").strip()
                if val:
                    session['PUSHOVER_API_TOKEN'] = val
                os.environ.update({k: v for k, v in session.items() if v is not None})
        elif choice == "4":
            print(f"EMAIL_SMTP_SERVER = {session.get('EMAIL_SMTP_SERVER')}")
            print(f"EMAIL_SMTP_PORT   = {session.get('EMAIL_SMTP_PORT')}")
            print(f"EMAIL_SENDER      = {masked(session.get('EMAIL_SENDER'))}")
            print(f"EMAIL_PASSWORD    = {masked(session.get('EMAIL_PASSWORD'))}")
            print(f"EMAIL_RECIPIENT   = {masked(session.get('EMAIL_RECIPIENT'))}")
            sub = input("Edit (e) / Back (Enter): ").strip().lower()
            if sub == "e":
                val = input("Enter EMAIL_SMTP_SERVER (empty to keep): ").strip()
                if val:
                    session['EMAIL_SMTP_SERVER'] = val
                val = input("Enter EMAIL_SMTP_PORT (empty to keep): ").strip()
                if val:
                    session['EMAIL_SMTP_PORT'] = val
                val = input("Enter EMAIL_SENDER (empty to keep): ").strip()
                if val:
                    session['EMAIL_SENDER'] = val
                val = input("Enter EMAIL_PASSWORD (empty to keep): ").strip()
                if val:
                    session['EMAIL_PASSWORD'] = val
                val = input("Enter EMAIL_RECIPIENT (empty to keep): ").strip()
                if val:
                    session['EMAIL_RECIPIENT'] = val
                os.environ.update({k: v for k, v in session.items() if v is not None})
        elif choice == "5":
            for k, v in session.items():
                if v is not None:
                    save_env_key(k, str(v))
            load_dotenv(ENV_PATH, override=True)
            print(f"{Fore.GREEN}Saved to {ENV_PATH}.{Style.RESET_ALL}")
        elif choice == "0":
            break

def show_logs(limit=100):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, notification_id, timestamp, channel, status, response FROM logs ORDER BY id DESC LIMIT ?", (limit,))
        rows = c.fetchall()
    if not rows:
        print(f"{Fore.YELLOW}No logs found.{Style.RESET_ALL}")
        return
    print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
    for r in rows:
        nid = r[1] if r[1] is not None else "-"
        print(f"LogID: {r[0]} | NotifID: {nid} | {r[2]} | {r[3].upper()} | {r[4]}")
        if r[5]:
            print(f"   Response: {r[5]}")
        print(f"{Fore.CYAN}{'-'*80}{Style.RESET_ALL}")

def export_notifications_to_json():
    timestamp = datetime.now().strftime("%m-%d-%Y_%H-%M-%S")
    file_path = f"notifications_export_{timestamp}.json"
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, message, due_time, sent, recurrence FROM notifications ORDER BY id")
        rows = c.fetchall()
    data = [
        {"id": r[0], "message": r[1], "due_time": r[2], "sent": bool(r[3]), "recurrence": r[4]}
        for r in rows
    ]
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print(f"{Fore.GREEN}✅ Exported {len(data)} reminders to {file_path}{Style.RESET_ALL}")
    db_log(None, "system", "EXPORT", f"Exported {len(data)} reminders → {file_path}")

def import_notifications_from_json(file_path="notifications_import.json"):
    if not os.path.exists(file_path):
        print(f"{Fore.RED}❌ File not found: {file_path}{Style.RESET_ALL}")
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"{Fore.RED}⚠️ Error reading JSON: {e}{Style.RESET_ALL}")
        return

    if not isinstance(data, list):
        print(f"{Fore.RED}⚠️ Invalid JSON format — expected a list of reminders.{Style.RESET_ALL}")
        return

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT message, due_time, recurrence FROM notifications")
        existing = c.fetchall()
        existing_tuples = {(r[0], r[1], r[2] or "") for r in existing}

        imported_count, skipped_count = 0, 0

        for item in data:
            msg = item.get("message")
            due_time = item.get("due_time")
            sent = 1 if item.get("sent") else 0
            recurrence = item.get("recurrence", "")

            if not msg or not due_time:
                skipped_count += 1
                continue
            if (msg, due_time, recurrence) in existing_tuples:
                skipped_count += 1
                continue
            if recurrence and recurrence not in ("daily", "weekly", "biweekly"):
                skipped_count += 1
                continue

            try:
                due_ts = due_str_to_epoch(due_time)
            except ValueError:
                skipped_count += 1
                continue

            c.execute(
                "INSERT INTO notifications (message, due_time, due_ts, sent, recurrence) VALUES (?, ?, ?, ?, ?)",
                (msg, due_time, due_ts, sent, recurrence or None),
            )
            imported_count += 1

        conn.commit()

    print(f"{Fore.GREEN}✅ Imported {imported_count} reminders, skipped {skipped_count} duplicates or invalid entries.{Style.RESET_ALL}")
    db_log(None, "system", "IMPORT", f"Imported {imported_count}, skipped {skipped_count}, file={file_path}")

def show_2025_holiday_example():
    holidays_2025 = [
        {"message": "New Year’s Day", "due_time": "01-01-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Martin Luther King Jr. Day", "due_time": "01-20-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Presidents’ Day", "due_time": "02-17-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Memorial Day", "due_time": "05-26-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Juneteenth National Independence Day", "due_time": "06-19-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Independence Day", "due_time": "07-04-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Labor Day", "due_time": "09-01-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Columbus Day", "due_time": "10-13-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Veterans Day", "due_time": "11-11-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Thanksgiving Day", "due_time": "11-27-2025 00:00", "sent": False, "recurrence": None},
        {"message": "Christmas Day", "due_time": "12-25-2025 00:00", "sent": False, "recurrence": None}
    ]

    example_file = "holidays_2025.json"
    with open(example_file, "w", encoding="utf-8") as f:
        json.dump(holidays_2025, f, indent=4)
    print(f"{Fore.GREEN}🎉 Created example holidays file: {example_file}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Contains {len(holidays_2025)} reminders for 2025 US holidays.{Style.RESET_ALL}")

def show_import_example():
    example = [
        {"message": "Team meeting", "due_time": "10-10-2025 09:00", "sent": False, "recurrence": "weekly"},
        {"message": "Database backup", "due_time": "10-11-2025 23:59", "sent": False, "recurrence": "daily"},
        {"message": "Server restart check", "due_time": "10-12-2025 03:00", "sent": False, "recurrence": None},
        {"message": "System security audit", "due_time": "10-15-2025 10:30", "sent": False, "recurrence": None},
        {"message": "Invoice batch upload", "due_time": "10-17-2025 15:00", "sent": False, "recurrence": None},
        {"message": "Monthly report review", "due_time": "10-20-2025 11:00", "sent": False, "recurrence": None},
        {"message": "Clean temp logs", "due_time": "10-22-2025 02:00", "sent": False, "recurrence": None},
        {"message": "Update SSL certificates", "due_time": "10-25-2025 08:00", "sent": False, "recurrence": None},
        {"message": "Renew domain license", "due_time": "10-28-2025 09:30", "sent": False, "recurrence": None},
        {"message": "Quarterly team sync", "due_time": "10-30-2025 16:00", "sent": False, "recurrence": "biweekly"}
    ]
    example_file = "import_example.json"
    with open(example_file, "w", encoding="utf-8") as f:
        json.dump(example, f, indent=4)
    print(f"{Fore.GREEN}📁 Created import example file: {example_file}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Contains {len(example)} reminders ready for import.{Style.RESET_ALL}")

def launch_tkinter_gui():
    def refresh_listbox():
        listbox.delete(0, tk.END)
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT id, message, due_time, sent, recurrence FROM notifications ORDER BY due_ts")
            rows = c.fetchall()
        for row in rows:
            status = "SENT" if row[3] else "PENDING"
            recurrence = row[4] or "None"
            listbox.insert(tk.END, f"ID: {row[0]} | Due: {row[2]} | Status: {status} | Recurrence: {recurrence} | Message: {row[1]}")

    def add_reminder():
        def save_reminder():
            msg = msg_entry.get()
            due = due_entry.get()
            recurrence = recurrence_var.get()
            if not msg:
                messagebox.showerror("Error", "Message cannot be empty.")
                return
            if len(msg) > 4000:
                messagebox.showerror("Error", f"Message exceeds 4000 characters (current: {len(msg)}).")
                return
            try:
                due_ts = due_str_to_epoch(due)
                due_text = epoch_to_due_str(due_ts)
            except ValueError:
                messagebox.showerror("Error", "Invalid date format. Use MM-DD-YYYY HH:MM or YYYY-MM-DD HH:MM.")
                return
            if recurrence not in ("None", "daily", "weekly", "biweekly"):
                messagebox.showerror("Error", "Invalid recurrence. Use None, daily, weekly, or biweekly.")
                return

            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM notifications WHERE message = ? AND due_time = ? AND recurrence = ?",
                          (msg, due_text, recurrence if recurrence != "None" else None))
                if c.fetchone():
                    messagebox.showerror("Error", "Identical reminder already exists.")
                    return
                c.execute("INSERT INTO notifications (message, due_time, due_ts, recurrence) VALUES (?, ?, ?, ?)",
                          (msg, due_text, due_ts, recurrence if recurrence != "None" else None))
                nid = c.lastrowid
                conn.commit()
            db_log(nid, "system", "CREATED", f"GUI: Reminder added: {msg} due {due_text} recurrence {recurrence}")
            messagebox.showinfo("Success", f"Added reminder ID {nid}: '{msg}' due {due_text} ({recurrence})")
            add_window.destroy()
            refresh_listbox()

        add_window = tk.Toplevel(root)
        add_window.title("Add Reminder")
        add_window.geometry("400x300")

        tk.Label(add_window, text="Message:").pack(pady=5)
        msg_entry = tk.Entry(add_window, width=50)
        msg_entry.pack(pady=5)

        tk.Label(add_window, text="Due Time (MM-DD-YYYY HH:MM or YYYY-MM-DD HH:MM):").pack(pady=5)
        due_entry = tk.Entry(add_window, width=50)
        due_entry.pack(pady=5)

        tk.Label(add_window, text="Recurrence:").pack(pady=5)
        recurrence_var = tk.StringVar(value="None")
        ttk.Combobox(add_window, textvariable=recurrence_var, values=["None", "daily", "weekly", "biweekly"], state="readonly").pack(pady=5)

        tk.Button(add_window, text="Save", command=save_reminder).pack(pady=10)
        tk.Button(add_window, text="Cancel", command=add_window.destroy).pack(pady=5)

    def edit_reminder():
        selection = listbox.curselection()
        if not selection:
            messagebox.showerror("Error", "Select a reminder to edit.")
            return
        item = listbox.get(selection[0])
        nid = int(item.split(" | ")[0].replace("ID: ", ""))

        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT message, due_time, recurrence FROM notifications WHERE id = ?", (nid,))
            row = c.fetchone()
        if not row:
            messagebox.showerror("Error", "Reminder not found.")
            return

        def save_edit():
            new_msg = msg_entry.get()
            new_due = due_entry.get()
            new_recurrence = recurrence_var.get()
            if not new_msg:
                messagebox.showerror("Error", "Message cannot be empty.")
                return
            if len(new_msg) > 4000:
                messagebox.showerror("Error", f"Message exceeds 4000 characters (current: {len(new_msg)}).")
                return
            try:
                new_due_ts = due_str_to_epoch(new_due)
                new_due_text = epoch_to_due_str(new_due_ts)
            except ValueError:
                messagebox.showerror("Error", "Invalid date format. Use MM-DD-YYYY HH:MM or YYYY-MM-DD HH:MM.")
                return
            if new_recurrence not in ("None", "daily", "weekly", "biweekly"):
                messagebox.showerror("Error", "Invalid recurrence. Use None, daily, weekly, or biweekly.")
                return

            with get_db() as conn:
                c = conn.cursor()
                c.execute("UPDATE notifications SET message = ?, due_time = ?, due_ts = ?, recurrence = ? WHERE id = ?",
                          (new_msg, new_due_text, new_due_ts, new_recurrence if new_recurrence != "None" else None, nid))
                conn.commit()
            db_log(nid, "system", "EDITED", f"GUI: Reminder updated: {new_msg} due {new_due_text} recurrence {new_recurrence}")
            messagebox.showinfo("Success", f"Updated reminder ID {nid}.")
            edit_window.destroy()
            refresh_listbox()

        edit_window = tk.Toplevel(root)
        edit_window.title("Edit Reminder")
        edit_window.geometry("400x300")

        tk.Label(edit_window, text="Message:").pack(pady=5)
        msg_entry = tk.Entry(edit_window, width=50)
        msg_entry.insert(0, row[0])
        msg_entry.pack(pady=5)

        tk.Label(edit_window, text="Due Time (MM-DD-YYYY HH:MM or YYYY-MM-DD HH:MM):").pack(pady=5)
        due_entry = tk.Entry(edit_window, width=50)
        due_entry.insert(0, row[1])
        due_entry.pack(pady=5)

        tk.Label(edit_window, text="Recurrence:").pack(pady=5)
        recurrence_var = tk.StringVar(value=row[2] or "None")
        ttk.Combobox(edit_window, textvariable=recurrence_var, values=["None", "daily", "weekly", "biweekly"], state="readonly").pack(pady=5)

        tk.Button(edit_window, text="Save", command=save_edit).pack(pady=10)
        tk.Button(edit_window, text="Cancel", command=edit_window.destroy).pack(pady=5)

    def delete_reminder():
        selection = listbox.curselection()
        if not selection:
            messagebox.showerror("Error", "Select a reminder to delete.")
            return
        item = listbox.get(selection[0])
        nid = int(item.split(" | ")[0].replace("ID: ", ""))

        if messagebox.askyesno("Confirm", "Delete this reminder?"):
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM notifications WHERE id = ?", (nid,))
                conn.commit()
            db_log(nid, "system", "DELETED", f"GUI: Reminder ID {nid} deleted")
            messagebox.showinfo("Success", f"Deleted reminder ID {nid}.")
            refresh_listbox()

    root = tk.Tk()
    root.title("Notifier GUI")
    root.geometry("600x400")

    tk.Label(root, text="Reminders", font=("Arial", 14)).pack(pady=10)

    listbox = tk.Listbox(root, width=80, height=15)
    listbox.pack(pady=10)

    button_frame = tk.Frame(root)
    button_frame.pack(pady=10)
    tk.Button(button_frame, text="Add Reminder", command=add_reminder).pack(side=tk.LEFT, padx=5)
    tk.Button(button_frame, text="Edit Reminder", command=edit_reminder).pack(side=tk.LEFT, padx=5)
    tk.Button(button_frame, text="Delete Reminder", command=delete_reminder).pack(side=tk.LEFT, padx=5)
    tk.Button(button_frame, text="Refresh", command=refresh_listbox).pack(side=tk.LEFT, padx=5)
    tk.Button(button_frame, text="Close", command=root.destroy).pack(side=tk.LEFT, padx=5)

    refresh_listbox()
    root.mainloop()
    db_log(None, "system", "GUI_CLOSED", "Tkinter GUI closed")

def main():
    init_db()
    send_admin_notification(f"✅ System started at {get_timestamp()}", include_system_info=True)
    print(f"{Fore.GREEN}System started successfully.{Style.RESET_ALL}")

    # All scheduling happens in the background thread only
    t = threading.Thread(target=background_runner, daemon=True)
    t.start()

    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}🚨 Notification App v0.0.7 Started!{Style.RESET_ALL}")
    print(f"{Fore.CYAN}🔄 Background scheduler is running (randomized Telegram heartbeat intervals).{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")

    while True:
        print(f"\n{Fore.WHITE}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
        print(f"{Fore.WHITE}║       📋 NOTIFICATION MENU            ║{Style.RESET_ALL}")
        print(f"{Fore.WHITE}╚═══════════════════════════════════════╝{Style.RESET_ALL}")
        print("1️⃣  ➕  Add Notification")
        print("2️⃣  📋  View Notifications")
        print("3️⃣  🚀  Send Due Notifications Now")
        print("4️⃣  ✏️   Edit Notification")
        print("5️⃣  🗑️   Delete Notification")
        print("6️⃣  ⚙️   Notification Services")
        print("7️⃣  🛠️   Configure Credentials (.env)")
        print("8️⃣  📜   View Logs (last 100)")
        print("9️⃣  📤  Export Reminders to JSON")
        print("🔟 📥  Import Reminders from JSON")
        print("🖼️  🗓️   Manage Reminders with Tkinter GUI")
        print("🗓️  🧾  Show 2025 Holidays Example JSON")
        print("💡  📚  Show Import Example JSON (10 items)")
        print("0️⃣  ❌   Exit")

        choice = input("Choose: ").strip()
        if choice == "1":
            add_notification()
        elif choice == "2":
            view_notifications()
        elif choice == "3":
            send_notifications()
        elif choice == "4":
            edit_notification()
        elif choice == "5":
            delete_notification()
        elif choice == "6":
            notification_services_menu()
        elif choice == "7":
            config_menu()
        elif choice == "8":
            show_logs()
        elif choice == "9":
            export_notifications_to_json()
        elif choice == "10":
            print(f"{Fore.YELLOW}Enter JSON file path (or press Enter for default 'notifications_import.json'):{Style.RESET_ALL}", end=" ")
            file_path = input().strip() or "notifications_import.json"
            import_notifications_from_json(file_path)
        elif choice.lower() in ("t", "11"):
            launch_tkinter_gui()
        elif choice.lower() in ("h", "12"):
            show_2025_holiday_example()
        elif choice.lower() in ("e", "13"):
            show_import_example()
        elif choice == "0":
            print("Goodbye.")
            send_admin_notification(f"🛑 System stopped at {get_timestamp()}", include_system_info=True)
            print(f"{Fore.GREEN}System stopped successfully.{Style.RESET_ALL}")
            break
        else:
            print("Invalid choice.")

if __name__ == "__main__":
    main()