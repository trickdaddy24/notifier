# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.6.1] - 2026-06-15  *(Latest)*

### Changed

- Heartbeat message now uses a structured format with three sections: **Services** (each notification channel shown as ✅ configured / ⚠️ not configured, plus SQLite DB health), **Network** (remote public IP via ipify + local IP), and **System** (OS, hostname, Python version, timestamp with timezone).

## [v2.6.0] - 2026-06-12

### Added

- Daily countdown cadence for events: a per-event "every day" mode (new `cadence` column, `milestones` remains the default) that sends one countdown notification per day until the event at its send_time, capped at 365 ticks. Daily runs are derived at expansion time, so editing the date or time re-expands every future tick. Cruise events now rotate through a 15-template funny emoji message pack (`days_left % 15`, deterministic) with special "tomorrow" and bon-voyage finale messages. The delivery engine skips stale event ticks after scheduler downtime (one current tick delivered, the rest retired with a `SKIPPED_STALE` audit log). Cadence selectable in the web event modal (radio, hides milestones input) and the CLI Events menu. Site favicon (bell + red badge) added to the web UI.

## [v2.5.0] - 2026-05-31

### Added

- Added countdown events (ported and generalised from cruise-notifier). New events table plus an event_id link on notifications; an event (cruise, trip, birthday, deadline) expands into one milestone notification per future day-offset (default 60,30,14,7,3,1,0) delivered by the existing scheduler with no second delivery path. Shared engine (notifier/db.py): create/get/list/update/delete_event, expand_event, _parse_event_date (ISO + US m/d/yy), milestone math, cruise-flavoured messages; seeds cruise-notifier cruises.json on first run (settings-flag guarded, NOTIFIER_SKIP_EVENT_SEED opt-out). Web: /api/events CRUD, dashboard countdown cards, add/edit modal, sidebar Events view. CLI: Events/Countdowns menu (written, pending interactive test). Added 6 engine tests for expansion, past-milestone skipping, re-expand on edit, delete cleanup, and scheduler delivery.

## [v2.4.0] - 2026-05-30

### Added

- Unified the CLI and web UI onto a single delivery engine — all 4 senders (Telegram, Discord, Pushover, Gmail) with retry/backoff and recurrence now live in notifier/notifications.py, fixing web reminders that never repeated, dead email delivery, and the unregistered Pushover channel. Fixed the python notifier.py entry point (absolute imports) and removed ~330 lines of duplicated logic. Consolidated the version string to a single __version__ anchor in notifier/__init__.py with all fallbacks pointing at it (web version display was permanently stuck on a stale fallback — now fixed). Repaired the smoke test and added a pytest engine suite (recurrence, mark-sent, credentials).

## [v2.3.2] - 2026-05-29

### Changed

- Mobile responsiveness and UI polish improvements across dashboard, top nav, tables, and Time Sync view.

## [v2.3.1] - 2026-05-29

### Fixed

- Removed temporary debug banner. Cleaned up deployment artifacts. Continuing Phase 2.

## [v2.3.0] - 2026-05-28

### Added

- Added Time & Date Sync panel (NTP/Server vs Local PC/browser), version badge in top nav, quick Time Sync button, and backend time_mode APIs.

## [v2.2.0] - 2026-05-28

### Added

- Added Time & Date Sync panel (NTP/Server time vs Local PC/browser), prominent version badge next to logo in top nav, quick-access purple 'Time Sync' button in header, and full backend support including time_mode setting, /api/settings/time, and /api/server-time.

## [v2.1.0] - 2026-05-19

### Added

- Headless + reliability release. Fixed: startup admin alert / GUI title / fallback hardcoded to v2.0.0 (now read live); timezone bug where naive datetime.timestamp() assumed the machine clock instead of TIMEZONE (new _to_ts/_from_ts, all scheduling math routed through them); background scheduler stdout scrambling the interactive menu (new _QUIET/_cprint, jobs run quiet). Added: headless CLI — --daemon, --send-now, --send-id, --snooze/--minutes, --add/--due/--repeat/--at, --list, --version (interactive menu still the no-arg default); UTF-8 stdout/stderr + log guard so emoji/box glyphs no longer crash piped/Task-Scheduler runs; channel registry (CHANNELS) collapsing the 4x duplicated send/verify/menu/credential code into one generic path; bounded retry/backoff on transient send failures via _deliver/_is_transient; pre-update DB+.env backup before git reset --hard; relative due times in the list; HEARTBEAT_ENABLED (legacy HEARTBEAT_INTERVAL still honored); send-by-id / snooze from the Send-Due menu. Added test_notifier_smoke.py (32 checks).

## [v2.0.6] - 2026-03-05

### Other

- Heartbeat on by default — fires once daily at a random time between 00:00 and 12:00 (schedule.every().day.at()); if no notification services are configured the heartbeat is logged to file and DB only without attempting any sends; HEARTBEAT_INTERVAL=0 still disables it; startup banner shows the chosen daily fire time

## [v2.0.5] - 2026-03-05

### Added

- Fixed About box alignment — each row is now exactly 73 visible chars with # on both sides; rewrote _row() to compute visible inner length before adding color codes, eliminating ANSI escape sequence interference with padding calculation

## [v2.0.4] - 2026-03-05

### Added

- Added About screen to System menu option 7 — shows Title, Author(s), Revised date, Description, Version, Entry Point, License, and GitHub URL; Version and Revised date are pulled live from version_notes.db via get_latest_release_info() so they auto-update with every version bump

## [v2.0.3] - 2026-03-05

### Added

- Added monthly recurrence option — _next_month_dt() helper uses stdlib calendar for correct end-of-month clamping (e.g. Jan 31 -> Feb 28), _next_recurrence_ts() handles monthly roll-forward, add/edit menus show option 4 Monthly, Tkinter GUI Combobox includes monthly, import validation accepts monthly

## [v2.0.2] - 2026-03-05

### Added

- Fixed GDBus D-Bus error on headless Linux servers — added DISPLAY/WAYLAND_DISPLAY env check after plyer import; NOTIFICATIONS_AVAILABLE set to False when no graphical display is present, preventing notify-send subprocess from spawning and printing D-Bus errors

## [v2.0.1] - 2026-03-04

### Fixed

- Fixed Pylance type errors — removed unused Back and random imports, moved tkinter imports into launch_tkinter_gui() as lazy import (eliminates tk=None false positives), replaced notification.notify() direct calls with captured _notify callable pattern

## [v2.0.0] - 2026-03-04

### Added

- Major merge release — added logging module with 5MB rotation, db_log audit trail, logs DB table with indexes, due_ts epoch column, recurrence system (daily/weekly/biweekly replacing repeat_time), show_logs() last-100 viewer, export/import JSON, Tkinter GUI (optional), masked() credential display, get_db() WAL context manager, enriched heartbeat with hostname/IP/Python version, send_admin_notification() startup/shutdown alert, send_notifications() epoch-based with ge-1-success logic, 11-option main menu

## [v1.0.43] - 2026-03-03

### Added

- Added daily repeat notifications — repeat_time column (auto-migrates existing DB), _next_daily_time helper, reschedule-on-fire logic, repeat display in view, repeat editing in edit menu. Added heartbeat — send_heartbeat() pings all configured services, HEARTBEAT_INTERVAL env var, System menu option 6 to configure interval, shown in startup banner

## [v1.0.42] - 2026-03-02

### Changed

- Renamed project from plex-notifier to notifier — updated GitHub repo name, git remote URL, install.sh REPO/INSTALL_DIR/launcher, check_for_updates GitHub URL, startup banner, README title and all clone URLs, project structure label

## [v1.0.41] - 2026-03-02

### Added

- Added timezone support — TIMEZONE env var (zoneinfo/tz database), _get_user_tz/_now_in_tz/_tz_label helpers, all due-time inputs and scheduler comparisons now use configured timezone, Set Timezone option in System menu, timezone shown in startup banner and due-time prompt, added tzdata dependency for Windows

## [v1.0.40] - 2026-03-02

### Added

- Full colorama redesign — added _box/_div/_opt/_prompt UI helpers, distinct colors per action type (green=add, cyan=view, blue=edit, red=delete, magenta=services), Style.BRIGHT accents, service-specific box border colors (Telegram=blue, Discord=magenta, Pushover=yellow, Gmail=red), triangle prompt arrow, improved startup banner

## [v1.0.39] - 2026-03-02

### Added

- Fixed version showing v1.0.32 — seed_initial_versions now always INSERT OR IGNORE so new versions are picked up on update. Added Check for Updates option to System menu with auto-update via git

## [v1.0.38] - 2026-03-02

### Changed

- Improved past-date error message to show entered time and current time so users understand why the due time was rejected

## [v1.0.37] - 2026-03-01

### Fixed

- Fixed install.sh update merge conflict — replaced git pull with git fetch origin and git reset --hard origin/main

## [v1.0.36] - 2026-03-01

### Added

- Fixed notification scheduling fires immediately bug — added _parse_due_time helper, normalize due_time to zero-padded format on save, reject past dates, use datetime comparison instead of string comparison in send_notifications

## [v1.0.35] - 2026-03-01

### Added

- Added install.sh one-liner installer for Ubuntu/Linux — auto-installs Python, git, libnotify, venv, deps, generates starter .env, creates notifier system launcher. Updated README Linux section

## [v1.0.34] - 2026-03-01

### Added

- Added interactive Set Credentials option (option 2) to all service menus — Telegram, Discord, Pushover, Gmail. Saves tokens directly to .env via dotenv.set_key, updates running session immediately, Gmail password hidden via getpass

## [v1.0.33] - 2026-03-01

### Fixed

- Fixed CHANGELOG.md version headers to include v prefix (eg. [v1.0.33] instead of [1.0.33])

## [v1.0.32] - 2026-03-01

### Added

- Added version_manager.py integration, System menu (option 7) with version history; renamed entry point to notifier.py; added README, requirements.txt, .gitignore, CHANGELOG.md

## [v1.0.31] - 2025-12-14

### Fixed

- Fixed desktop notification crash when plyer notify is not callable; changed scheduler failure to warning instead of crash

## [v1.0.20] - 2025-10-09

### Added

- Added Pushover integration, unified notification services menu, show_complete_env_example helper, per-service status indicators

## [v1.0.10] - 2025-08-22

### Added

- Added Discord webhook integration and Gmail SMTP support with app-password instructions

## [v1.0.0] - 2025-06-01

### Added

- Initial release — Telegram notifications, SQLite scheduler, background thread, CRUD menu, colorama UI, plyer desktop support
