<div align="center">

# Notifier

[![Tests](https://github.com/trickdaddy24/notifier/actions/workflows/tests.yml/badge.svg)](https://github.com/trickdaddy24/notifier/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-2.6.0-8A4DFF.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Python tool for scheduling and delivering reminders across multiple notification platforms — Telegram, Discord, Pushover, and Gmail — with a SQLite-backed scheduler, audit logging, JSON import/export, an optional Tkinter GUI, an integrated version management system, and an **optional FastAPI web dashboard**.

</div>

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Project Structure](#project-structure)
- [Deployment](#deployment)
- [Backup & Recovery](#backup--recovery)
- [Roadmap](#roadmap)
- [Version History](#version-history)
- [License](#license)

## Features

- **Multi-platform delivery** — send reminders to Telegram, Discord, Pushover, and Gmail simultaneously
- **SQLite scheduler** — store notifications with due times; a background thread fires them automatically every minute
- **Recurrence** — repeat notifications daily (at a specific time), weekly, or biweekly; auto-rescheduled after each fire
- **Audit log** — every send attempt is recorded to a `logs` table (channel, status, response) via `db_log()`
- **File logging** — all activity written to `multi_channel_notifier.log` with automatic 5 MB rotation
- **JSON import / export** — back up or bulk-load notifications from a JSON file
- **Countdown events** — track a target date (cruise, trip, birthday, deadline) and get notified at milestones as it approaches (default 60/30/14/7/3/1/0 days, then "today's the day!")
- **Tkinter GUI** — optional graphical window for viewing, adding, and deleting reminders (menu option 11)
- **Desktop notifications** — optional Windows/macOS/Linux system toasts via `plyer`
- **Full CRUD** — add, view, edit, and delete scheduled notifications
- **Service health checks** — verify, set credentials, test, and show setup instructions for each integration
- **Timezone support** — configure any IANA timezone; all due-time inputs and scheduler comparisons use it
- **Configurable heartbeat** — periodic ping to all services at a set interval (hours)
- **Version management** — built-in release tracker with auto-generated `CHANGELOG.md` (System menu)
- **Web dashboard (optional)** — a FastAPI + Tailwind browser UI (`web/`) to view/add/manage reminders, password-protected, deployable via Docker/Traefik or as a Cloudflare Pages frontend

---

## Architecture

Two front ends over one SQLite database. The **CLI** (`notifier.py`) is the primary
interface and runs the background scheduler thread; the optional **web dashboard**
(`web/`, FastAPI + Tailwind) reads/writes the same `notifications.db`.

```
                 ┌────────────────────────────────────────────────┐
   you ─────────▶│  CLI  notifier.py  (menu + scheduler thread)     │
                 │   • fires due reminders every minute             │
                 │   • multi-channel send + audit log               │
                 └───────────────┬──────────────────────────────────┘
                                 │ shared
   browser ─HTTPS─▶ Traefik ─▶ ┌─▼──────────────────────────────┐
   (optional)      / CF Pages   │ web/ (FastAPI + Tailwind)       │
                                │  • login (NOTIFIER_WEB_PASSWORD)│
                                │  • dashboard CRUD · /health     │
                                └─────────────┬───────────────────┘
                                              │ reads/writes
                    ┌─────────────────────────▼─────────────────────┐
                    │ SQLite  notifications.db                       │
                    │   • notifications (schedule)  • logs (audit)   │
                    │ version_notes.db  (release history)            │
                    └───────────────────────┬────────────────────────┘
                          send  ┌────────────▼──────────────┐
                                │ Telegram · Discord ·        │
                                │ Pushover · Gmail · desktop  │
                                └─────────────────────────────┘
```

| Component | Role | Where |
|---|---|---|
| **CLI** | Menu UI + the scheduler thread that fires due reminders | `notifier.py` (+ `notifier/` package) |
| **Web** | Optional FastAPI dashboard + auth + `/health` | `web/main.py`, `web/auth.py`, `web/templates/`, `web/static/` |
| **Senders** | Per-channel delivery, each returns `(bool, str)` for the audit log | inside `notifier.py` |
| **Data** | Schedule + audit trail; release history | `notifications.db` (`notifications`, `logs`) · `version_notes.db` |
| **Versioning** | Seed list + DB, auto-generates `CHANGELOG.md` | `version_manager.py` |

---

## Installation

### Windows 11

**1. Install Python 3.10+**

Download and run the installer from [python.org](https://www.python.org/downloads/windows/).
On the first screen, check **"Add Python to PATH"** before clicking Install.

Verify the install:
```cmd
python --version
```

**2. Install Git**

Download from [git-scm.com](https://git-scm.com/download/win) and run the installer with default settings.

Verify:
```cmd
git --version
```

**3. Clone the repo**
```cmd
git clone https://github.com/trickdaddy24/notifier.git
cd notifier
```

**4. Create and activate a virtual environment**
```cmd
python -m venv .venv
.venv\Scripts\activate
```
You should see `(.venv)` appear at the start of your prompt.

**5. Install dependencies**
```cmd
pip install -r requirements.txt
```

**6. Create your `.env` file**
```cmd
copy .env.example .env
```
Then open `.env` in Notepad or any editor and fill in your credentials (see [Configuration](#configuration) below).

**7. Run the app**
```cmd
python notifier.py
```

> **Desktop notifications on Windows 11** are handled automatically via `plyer` — no extra setup needed.
> **Tkinter** is included in the Python standard library on Windows — no install required.

---

### macOS

**1. Install Python 3.10+**

The recommended way is via [Homebrew](https://brew.sh). If you don't have Homebrew, install it first:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then install Python:
```bash
brew install python
```

Verify:
```bash
python3 --version
```

**2. Install Git**

Git ships with Xcode Command Line Tools. If you don't have it:
```bash
xcode-select --install
```

Or install via Homebrew:
```bash
brew install git
```

**3. Clone the repo**
```bash
git clone https://github.com/trickdaddy24/notifier.git
cd notifier
```

**4. Create and activate a virtual environment**
```bash
python3 -m venv .venv
source .venv/bin/activate
```
You should see `(.venv)` appear at the start of your prompt.

**5. Install dependencies**
```bash
pip install -r requirements.txt
```

**6. Create your `.env` file**
```bash
cp .env.example .env
```
Then open `.env` in any editor and fill in your credentials (see [Configuration](#configuration) below).

**7. Run the app**
```bash
python notifier.py
```

> **Desktop notifications on macOS** require granting terminal notification permissions.
> Go to **System Settings → Notifications** and allow notifications for your terminal app (Terminal or iTerm2).
>
> **Tkinter on macOS** — if you installed Python via Homebrew, tkinter may need to be installed separately:
> ```bash
> brew install python-tk
> ```

---

### Linux (Ubuntu / Debian)

**One-liner install (recommended)**

```bash
bash <(curl -sL https://raw.githubusercontent.com/trickdaddy24/notifier/main/install.sh)
```

The script will:
- Verify Python 3.10+ (and offer install instructions if missing)
- Install `git`, `libnotify-bin`, and `python3-tk` if not present
- Clone the repo to `~/notifier`
- Create a virtual environment and install all dependencies
- Generate a starter `.env` file
- Create a `notifier` launch command available system-wide

Then run:
```bash
notifier
```

> **If the command is not found** after install, reload your shell:
> ```bash
> source ~/.bashrc
> ```

---

**Manual install (any distro)**

Ubuntu / Debian:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv python3-tk git libnotify-bin -y
```

Fedora:
```bash
sudo dnf install python3 python3-pip python3-tkinter git libnotify -y
```

Arch:
```bash
sudo pacman -S python python-pip tk git libnotify
```

```bash
git clone https://github.com/trickdaddy24/notifier.git
cd notifier
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python notifier.py
```

---

## Configuration

Create a `.env` file in the project root. **Never commit this file.**

```env
# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=987654321

# Discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Pushover
PUSHOVER_USER_KEY=your_user_key_here
PUSHOVER_API_TOKEN=your_api_token_here

# Gmail (use an App Password, not your regular password)
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_SENDER=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
EMAIL_RECIPIENT=recipient@email.com

# Timezone (optional — leave blank to use system local time)
# Any IANA tz name: America/New_York, Europe/London, Asia/Tokyo, etc.
TIMEZONE=America/New_York

# Heartbeat interval in hours (0 = disabled)
HEARTBEAT_INTERVAL=6
```

Each service is optional — unconfigured services are silently skipped when sending. You can verify, set credentials, and test each one from the **Notification Services** menu (option 7).

### Telegram setup
1. Message `@BotFather` → `/newbot` → copy the token
2. Message `@userinfobot` to get your chat ID

### Discord setup
1. Server Settings → Integrations → Webhooks → New Webhook → Copy URL

### Pushover setup
1. Create an account at [pushover.net](https://pushover.net)
2. Copy your User Key and create an API Token

### Gmail setup
1. Google Account → Security → 2-Step Verification → App Passwords
2. Generate a password for "Mail" and use it as `EMAIL_PASSWORD`

---

## Usage

```bash
python notifier.py
```

### Main menu

```
╔═══════════════════════════════════════╗
║  📋 NOTIFICATION MENU         v2.5.0 ║
╚═══════════════════════════════════════╝
  1  ➕  Add Notification
  2  📋  View Notifications
  3  📤  Send Due Notifications Now
  4  ✏️   Edit Notification
  5  🗑️   Delete Notification
  6  📅  Events / Countdowns
  ─────────────────────────────────────
  7  📬  Notification Services
  8  📜  View Logs
  9  📤  Export to JSON
 10  📥  Import from JSON
 11  🖥️   Open GUI (Tkinter)
  ─────────────────────────────────────
 12  ⚙️   System  [v2.5.0]
  ─────────────────────────────────────
  0  🚪  Exit
```

### Adding a notification

When adding, you are asked whether to repeat. If yes, choose:

| Type | Behaviour |
|---|---|
| **Daily** | Fires at a specific HH:MM every day; rescheduled automatically after each send |
| **Weekly** | Repeats every 7 days from the initial due time |
| **Biweekly** | Repeats every 14 days from the initial due time |

Due times are accepted as `YYYY-MM-DD HH:MM` or `MM-DD-YYYY HH:MM`.

### Events / Countdowns (option 6)

A **countdown event** is a target date you want to be reminded about repeatedly as
it gets closer — a cruise, a trip, a birthday, a project deadline. Instead of a
single reminder, you get a notification on each **milestone** (days before the
date) plus one on the day itself.

| Field | Notes |
|---|---|
| **Title** | What you're counting down to (e.g. `Carnival Celebration`) |
| **Target date** | `YYYY-MM-DD` or US `m/d/yy` (e.g. `7/12/26`) |
| **Cruise?** | Optional — adds 🚢 nautical phrasing to the messages |
| **Details** | Optional freeform note (ship, cabin, confirmation #) |
| **Milestones** | Comma-separated days-before; default `60,30,14,7,3,1,0` (`0` = the day itself) |
| **Notify at** | Time of day each milestone fires (default `09:00`, in your configured timezone) |

Under the hood, an event **expands into ordinary notifications** — one per future
milestone — so they're delivered by the same scheduler and across the same
channels as everything else. Milestones already in the past are skipped, and
editing an event re-generates its upcoming pings. Events are managed from the web
dashboard too (sidebar → **Events / Countdowns**).

### View Logs (option 8)

Shows the last 100 entries from the `logs` table — one row per channel per notification send attempt, with timestamp, channel name, status (`SUCCESS` / `SKIPPED` / `FAILED`), and the API response.

### Export / Import JSON (options 9 & 10)

- **Export** writes all current notifications to `notifications_export_YYYY-MM-DD_HH-MM-SS.json`
- **Import** reads a JSON file (default: `notifications_import.json`) and bulk-inserts new entries, skipping duplicates

Expected JSON format:
```json
[
  {
    "message": "Team standup",
    "due_time": "2026-04-01 09:00",
    "sent": false,
    "recurrence": "weekly",
    "repeat_time": null
  }
]
```

### System menu (option 12)

```
╔═══════════════════════════════════════╗
║  ⚙️  SYSTEM                   v2.5.0 ║
╚═══════════════════════════════════════╝
  1  📜  View Version History
  2  ➕  Add New Version Release
  3  ✏️   Edit Version Notes
  4  🔄  Check for Updates
  5  🕐  Set Timezone       [America/New_York]
  6  💓  Heartbeat          [every 6h]
  ─────────────────────────────────────
  0  ⬅️   Back to Main Menu
```

| Option | Description |
|---|---|
| View Version History | Full release log stored in `version_notes.db` |
| Add New Version Release | Record a new version with notes; regenerates `CHANGELOG.md` |
| Edit Version Notes | Update notes for an existing release |
| Check for Updates | Fetches latest version from GitHub `CHANGELOG.md`; offers to auto-update via `git` |
| Set Timezone | Set any IANA timezone name; saved to `.env` immediately |
| Heartbeat | Configure how often (in hours) a ping is sent to all services; 0 = disabled |

---

## Project Structure

```
notifier/
├── notifier.py               # Main application (CLI entry point)
├── version_manager.py        # Version tracking & CHANGELOG.md generation
├── requirements.txt
├── install.sh                # One-liner Linux installer
├── .gitignore
├── .env                      # Your secrets (NOT in repo)
├── CHANGELOG.md              # Auto-generated by version_manager
├── notifications.db          # Runtime SQLite DB (excluded from repo)
│                             #   tables: notifications, logs
├── version_notes.db          # Version history DB (excluded from repo)
└── multi_channel_notifier.log  # Rolling activity log (excluded from repo)
```

### Database schema

```sql
-- Scheduled notifications
CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message     TEXT NOT NULL,
    due_time    TEXT NOT NULL,       -- "YYYY-MM-DD HH:MM" (human-readable)
    due_ts      INTEGER NOT NULL,    -- Unix epoch (used for fast queries)
    sent        INTEGER DEFAULT 0,
    recurrence  TEXT DEFAULT NULL,   -- "daily" / "weekly" / "biweekly" / NULL
    repeat_time TEXT DEFAULT NULL    -- "HH:MM" for daily-at-specific-time
);

-- Send-attempt audit trail
CREATE TABLE logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id INTEGER,
    timestamp       TEXT NOT NULL,
    channel         TEXT NOT NULL,
    status          TEXT NOT NULL,   -- SUCCESS / FAILED / SKIPPED
    response        TEXT
);
```

---

## Deployment

The CLI runs anywhere Python does (locally / Windows Task Scheduler / cron). The
**optional web dashboard** ships two deploy paths:

**Docker + Traefik (Saltbox)** — `docker-compose.yml` builds the `notifier-web` service
(FastAPI on `:8000`, `/health` healthcheck). No host port is published; Traefik on the
external `saltbox` network fronts it. State persists via the `./data:/app/data` volume
(`NOTIFIER_DB_PATH=/app/data/notifications.db`); `.env` is mounted read-only.

```bash
# on the host
cp .env.example .env                 # set NOTIFIER_WEB_PASSWORD + channel creds
docker compose up --build -d
# standalone (no Traefik): add a `ports: ["8000:8000"]` mapping per the compose comments
```

**Cloudflare Pages (frontend)** — `wrangler.toml` builds the `web/frontend` bundle for a
Pages deploy (`npx wrangler pages deploy web/frontend/dist --project-name=notifier-web`);
a GitHub Actions workflow (`.github/workflows/deploy-frontend.yml`) automates it.

> Required web env: `NOTIFIER_WEB_PASSWORD` (login), `TIMEZONE`, `NOTIFIER_DB_PATH`, plus
> the notification-channel vars from [Configuration](#configuration).

---

## Backup & Recovery

**All state is two SQLite files** — `notifications.db` (schedule + audit `logs`) and
`version_notes.db` (release history). Everything else regenerates.

```bash
# In-app: CLI "Database backup" (menu) writes a timestamped copy.
# Online-safe snapshot (Docker):
docker exec notifier-web sqlite3 /app/data/notifications.db \
  ".backup '/app/data/notifications_$(date +%F).db'"
# JSON export (portable): use the CLI's JSON export to dump notifications.
```

**Restore:** stop the app, replace `notifications.db` (in `./data` for Docker) with your
backup, restart. Schema is created on first run, so an empty/missing DB just starts fresh;
a JSON export can be re-imported via the CLI.

**Disaster recovery:** re-clone the repo, restore `notifications.db` + `.env`, then run the
CLI or `docker compose up --build -d`.

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned major features.

The most significant upcoming item is **Multi-User Support + Google OAuth** (open registration, private per-user reminders, OAuth implemented inside FastAPI).

---

## Version History

| Version | Date | Highlights |
|---|---|---|
| **2.6.0** | 2026-06-12 | Daily countdown cadence + cruise message pack + stale-tick skip + favicon. |
| 2.5.0 | 2026-05-31 | Countdown events (ported from cruise-notifier) — an event expands into milestone reminders (60/30/14/7/3/1/0 days) via the existing scheduler; web + CLI events UI. |
| 2.4.0 | 2026-05-30 | Unified CLI + web onto one delivery engine (fixed web recurrence, email, Pushover); single `__version__` anchor; pytest engine suite. |
| 2.3.0–2.3.2 | 2026-05-28→29 | Time & Date Sync panel (NTP vs local), version badge in nav, mobile/UI polish. |
| 2.1.0 | 2026-05-19 | Headless CLI (`--daemon/--send-now/--add/...`), timezone fix, channel registry, retry/backoff, smoke tests. |
| 2.0.x | 2026-03 | Daily heartbeat default, About-box fixes, modular refactor groundwork. |

See [CHANGELOG.md](CHANGELOG.md) for the full history (auto-generated by `version_manager.py`).

---

## License

[MIT](LICENSE) © Minus One Labs
