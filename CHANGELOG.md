# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.0.5] - 2026-03-05  *(Latest)*

### Fixed

- Fixed About box alignment — each row is now exactly 73 visible chars with `#` flush on both sides; rewrote `_row()` to calculate padding from the plain-text inner length before applying colorama ANSI codes, eliminating escape-sequence interference with string padding

## [v2.0.4] - 2026-03-05

### Added

- Added About screen to System menu option 7 — displays Title, Author(s), Revised date, Description, Version, Entry Point, License, and GitHub URL in script-header format; Version and Revised date are pulled live from `version_notes.db` via `get_latest_release_info()` so they auto-update with every version bump; `get_latest_release_info()` helper added to `version_manager.py`

## [v2.0.3] - 2026-03-05

### Added

- Added monthly recurrence option — `_next_month_dt()` helper uses stdlib `calendar` for correct end-of-month clamping (e.g. Jan 31 → Feb 28/29); `_next_recurrence_ts()` handles monthly roll-forward; add and edit menus show option **4 Monthly**; Tkinter GUI Combobox includes monthly; JSON import validation accepts monthly

## [v2.0.2] - 2026-03-05

### Fixed

- Fixed GDBus D-Bus error on headless Linux servers — added `DISPLAY`/`WAYLAND_DISPLAY` environment variable check after plyer import; `NOTIFICATIONS_AVAILABLE` is set to `False` when no graphical display is detected, preventing `notify-send` from spawning and printing D-Bus errors to the console

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

- Improved past-date error message to show the entered time and current time so users understand why the due time was rejected

## [v1.0.37] - 2026-03-01

### Fixed

- Fixed install.sh update path — replaced git pull with git fetch origin and git reset --hard origin/main to prevent merge conflict when CHANGELOG.md has local changes from version manager

## [v1.0.36] - 2026-03-01

### Added

- Fixed notification scheduling logic — added _parse_due_time() helper; add_notification and edit_notification now normalize due_time to zero-padded YYYY-MM-DD HH:MM format and reject past dates; send_notifications uses proper datetime comparison instead of fragile string comparison

## [v1.0.35] - 2026-03-01

### Added

- Added install.sh one-liner installer for Ubuntu/Linux — auto-installs Python, git, libnotify, venv, deps, starter .env, and plex-notifier launcher. Updated README Linux section with one-liner and manual install instructions

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

### Other

- Initial release — Telegram notifications, SQLite scheduler, background thread, CRUD menu, colorama UI, plyer desktop support


<!-- Generated by version-management tool -->
