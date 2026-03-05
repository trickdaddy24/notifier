# Notifier

A Python CLI tool for scheduling and delivering reminders across multiple notification platforms — Telegram, Discord, Pushover, and Gmail — with a SQLite-backed scheduler, audit logging, JSON import/export, an optional Tkinter GUI, and an integrated version management system.

## Features

- **Multi-platform delivery** — send reminders to Telegram, Discord, Pushover, and Gmail simultaneously
- **SQLite scheduler** — store notifications with due times; a background thread fires them automatically every minute
- **Recurrence** — repeat notifications daily (at a specific time), weekly, or biweekly; auto-rescheduled after each fire
- **Audit log** — every send attempt is recorded to a `logs` table (channel, status, response) via `db_log()`
- **File logging** — all activity written to `multi_channel_notifier.log` with automatic 5 MB rotation
- **JSON import / export** — back up or bulk-load notifications from a JSON file
- **Tkinter GUI** — optional graphical window for viewing, adding, and deleting reminders (menu option 10)
- **Desktop notifications** — optional Windows/macOS/Linux system toasts via `plyer`
- **Full CRUD** — add, view, edit, and delete scheduled notifications
- **Service health checks** — verify, set credentials, test, and show setup instructions for each integration
- **Timezone support** — configure any IANA timezone; all due-time inputs and scheduler comparisons use it
- **Configurable heartbeat** — periodic ping to all services at a set interval (hours)
- **Version management** — built-in release tracker with auto-generated `CHANGELOG.md` (System menu)

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

Each service is optional — unconfigured services are silently skipped when sending. You can verify, set credentials, and test each one from the **Notification Services** menu (option 6).

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
║  📋 NOTIFICATION MENU         v2.0.0 ║
╚═══════════════════════════════════════╝
  1  ➕  Add Notification
  2  📋  View Notifications
  3  📤  Send Due Notifications Now
  4  ✏️   Edit Notification
  5  🗑️   Delete Notification
  ─────────────────────────────────────
  6  📬  Notification Services
  7  📜  View Logs
  8  📤  Export to JSON
  9  📥  Import from JSON
 10  🖥️   Open GUI (Tkinter)
  ─────────────────────────────────────
 11  ⚙️   System  [v2.0.0]
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

### View Logs (option 7)

Shows the last 100 entries from the `logs` table — one row per channel per notification send attempt, with timestamp, channel name, status (`SUCCESS` / `SKIPPED` / `FAILED`), and the API response.

### Export / Import JSON (options 8 & 9)

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

### System menu (option 11)

```
╔═══════════════════════════════════════╗
║  ⚙️  SYSTEM                   v2.0.0 ║
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
├── notifier.py               # Main application (v2.0.0)
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

## License

MIT
