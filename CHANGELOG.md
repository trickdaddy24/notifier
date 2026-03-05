# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.0.0] - 2026-03-04  *(Latest)*

### Added

- File logging — `multi_channel_notifier.log` with automatic 5 MB rotation
- `db_log()` audit trail — every send attempt written to `logs` DB table (channel, status, response)
- `logs` table with `idx_logs_time` index
- `due_ts` epoch column on `notifications` table with `idx_notifications_due` index for fast scheduler queries
- `recurrence` column — "daily" / "weekly" / "biweekly" / NULL; auto-rescheduled after each fire
- `show_logs()` — View Logs menu option showing last 100 entries (menu option 7)
- `export_notifications_to_json()` — exports all notifications to a timestamped JSON file (option 8)
- `import_notifications_from_json()` — bulk-imports from JSON, skips duplicates (option 9)
- `launch_tkinter_gui()` — optional graphical window for viewing, adding, and deleting reminders (option 10)
- `masked()` — safe credential display (shows first 3 and last 3 chars) in service menus
- `get_db()` `@contextmanager` — WAL-mode, thread-safe SQLite connections replacing bare `sqlite3.connect`
- `send_admin_notification()` — optional startup/shutdown Telegram alert via `TELEGRAM_ADMIN_BOT_TOKEN`
- `platform` and `socket` imports — heartbeat message now includes hostname, IP, and Python version
- DB auto-migration on startup — adds `due_ts`, `recurrence`, `repeat_time` columns and backfills epoch values from existing rows
- `versions/` folder — archived `notifier.1.0.32.py` and `notifier.2.0.0.py` prototype

### Changed

- All senders now return `(bool, str)` tuples to enable per-channel `db_log()` on every send
- `send_notifications()` now uses epoch-based DB query (`due_ts <= now_ts`) and only marks sent if at least one channel succeeds
- Recurrence upgraded from daily-only to daily / weekly / biweekly with roll-forward logic
- Heartbeat enriched with system info (hostname, IP, Python version)
- Main menu expanded from 7 to 11 options
- `init(autoreset=False)` kept — colorama resets managed manually per print

## [v1.0.43] - 2026-03-03

### Added

- Daily repeat notifications — `repeat_time` column (auto-migrates existing DB), `_next_daily_time` helper, reschedule-on-fire logic, repeat display in view, repeat editing in edit menu
- Heartbeat — `send_heartbeat()` pings all configured services on a schedule; configure interval via System → option 6 (`HEARTBEAT_INTERVAL` env var); shown in startup banner when active

## [v1.0.42] - 2026-03-02

### Changed

- Renamed project from plex-notifier to notifier — updated GitHub repo name, git remote URL, install.sh REPO/INSTALL_DIR/launcher, check_for_updates GitHub URL, startup banner, README title and all clone URLs, project structure label

## [v1.0.41] - 2026-03-02

### Added

- Added timezone support — TIMEZONE env var (zoneinfo/tz database), `_get_user_tz`/`_now_in_tz`/`_tz_label` helpers, all due-time inputs and scheduler comparisons now use configured timezone, Set Timezone option in System menu, timezone shown in startup banner and due-time prompt, added tzdata dependency for Windows

## [v1.0.40] - 2026-03-02

### Added

- Full colorama redesign — added `_box`/`_div`/`_opt`/`_prompt` UI helpers, distinct colors per action type (green=add, cyan=view, blue=edit, red=delete, magenta=services), Style.BRIGHT accents, service-specific box border colors (Telegram=blue, Discord=magenta, Pushover=yellow, Gmail=red), triangle prompt arrow, improved startup banner

## [v1.0.39] - 2026-03-02

### Fixed

- Fixed version showing v1.0.32 — `seed_initial_versions` now always `INSERT OR IGNORE` so new versions are picked up on update
- Added Check for Updates option to System menu with auto-update via git

## [v1.0.38] - 2026-03-02

### Changed

- Improved past-date error message to show the entered time and current time so users understand why the due time was rejected

## [v1.0.37] - 2026-03-01

### Fixed

- Fixed install.sh update path — replaced `git pull` with `git fetch origin` and `git reset --hard origin/main` to prevent merge conflict when CHANGELOG.md has local changes from version manager

## [v1.0.36] - 2026-03-01

### Fixed

- Fixed notification scheduling logic — added `_parse_due_time()` helper; `add_notification` and `edit_notification` now normalize `due_time` to zero-padded `YYYY-MM-DD HH:MM` format and reject past dates; `send_notifications` uses proper datetime comparison instead of fragile string comparison

## [v1.0.35] - 2026-03-01

### Added

- Added `install.sh` one-liner installer for Ubuntu/Linux — auto-installs Python, git, libnotify, venv, deps, starter `.env`, and notifier launcher. Updated README Linux section with one-liner and manual install instructions

## [v1.0.34] - 2026-03-01

### Added

- Added interactive Set Credentials option (option 2) to all service menus — Telegram, Discord, Pushover, Gmail. Saves tokens directly to `.env` via `dotenv.set_key`, updates running session immediately, Gmail password hidden via `getpass`

## [v1.0.33] - 2026-03-01

### Fixed

- Fixed CHANGELOG.md version headers to include `v` prefix (e.g. `[v1.0.33]` instead of `[1.0.33]`)

## [v1.0.32] - 2026-03-01

### Added

- Added `version_manager.py` integration, System menu (option 7) with version history; renamed entry point to `notifier.py`; added README, requirements.txt, .gitignore, CHANGELOG.md

## [v1.0.31] - 2025-12-14

### Fixed

- Fixed desktop notification crash when plyer `notify` is not callable; changed scheduler failure to warning instead of crash

## [v1.0.20] - 2025-10-09

### Added

- Added Pushover integration, unified notification services menu, `show_complete_env_example` helper, per-service status indicators

## [v1.0.10] - 2025-08-22

### Added

- Added Discord webhook integration and Gmail SMTP support with app-password instructions

## [v1.0.0] - 2025-06-01

### Added

- Initial release — Telegram notifications, SQLite scheduler, background thread, CRUD menu, colorama UI, plyer desktop support


<!-- Generated by version-management tool -->
