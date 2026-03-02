"""
version_manager.py — Semantic version tracking with auto-generated CHANGELOG.md.

Source: https://github.com/trickdaddy24/version-management-system (v0.2.0)
Integrated into the Notification App project.
"""

import sqlite3
from datetime import datetime
import logging
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).parent
DATABASE_NAME = BASE_DIR / 'version_notes.db'
CHANGELOG_FILE = BASE_DIR / 'CHANGELOG.md'
LOG_FILE = BASE_DIR / 'version_management.log'


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    """Initialises logging to a file in the same directory as this script."""
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logging.info("Logging initialised.")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_NAME)
    try:
        yield conn
    finally:
        conn.close()


def setup_database():
    """Creates the 'releases' table if it does not already exist."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS releases (
                id              TEXT PRIMARY KEY,
                version_number  TEXT NOT NULL UNIQUE,
                notes           TEXT,
                timestamp       TEXT
            )
        ''')
        conn.commit()
    logging.info("Database initialised with 'releases' table.")
    seed_initial_versions()


# ---------------------------------------------------------------------------
# Seed data — pre-populates history on first run
# ---------------------------------------------------------------------------

SEED_VERSIONS = [
    ("001", "1.0.0",
     "Initial release — Telegram notifications, SQLite scheduler, background thread, CRUD menu, colorama UI, plyer desktop support",
     "2025-06-01 12:00:00"),
    ("002", "1.0.10",
     "Added Discord webhook integration and Gmail SMTP support with app-password instructions",
     "2025-08-22 09:00:00"),
    ("003", "1.0.20",
     "Added Pushover integration, unified notification services menu, show_complete_env_example helper, per-service status indicators",
     "2025-10-09 14:00:00"),
    ("004", "1.0.31",
     "Fixed desktop notification crash when plyer notify is not callable; changed scheduler failure to warning instead of crash",
     "2025-12-14 11:00:00"),
    ("005", "1.0.32",
     "Added version_manager.py integration, System menu (option 7) with version history; renamed entry point to notifier.py; added README, requirements.txt, .gitignore, CHANGELOG.md",
     "2026-03-01 20:38:00"),
]


def seed_initial_versions():
    """Populate the DB with version history on first run (skips if already seeded)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM releases")
        if cursor.fetchone()[0] > 0:
            return
        cursor.executemany(
            "INSERT OR IGNORE INTO releases (id, version_number, notes, timestamp) "
            "VALUES (?, ?, ?, ?)",
            SEED_VERSIONS
        )
        conn.commit()
    logging.info("Seeded %d initial versions into the database.", len(SEED_VERSIONS))
    update_changelog()


def get_current_version() -> str:
    """Return the latest version string from the DB, or '1.0.32' as fallback."""
    latest = get_latest_version_data()
    return latest[1] if latest else "1.0.32"


def get_latest_version_data():
    """Returns (id, version_number) for the most recent release, or None."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, version_number FROM releases ORDER BY id DESC LIMIT 1"
        )
        latest = cursor.fetchone()
    logging.info("Fetched latest version data: %s", latest)
    return latest


def parse_version(version_str: str) -> tuple[int, int, int]:
    """Parse 'X.Y.Z' into a tuple of three ints."""
    parts = version_str.split('.')
    if len(parts) != 3:
        raise ValueError(
            f"Invalid version format: '{version_str}' — expected X.Y.Z"
        )
    logging.info("Parsed version %s into %s", version_str, parts)
    return int(parts[0]), int(parts[1]), int(parts[2])


# ---------------------------------------------------------------------------
# Version generation
# ---------------------------------------------------------------------------

def generate_next_version(latest_version_data):
    """
    Prompt the user for a bump type and return (new_id, new_version).
    Returns (None, None) if the user cancels.
    """
    if latest_version_data is None:
        new_id = "001"
        new_version = "0.0.1"
        logging.info(
            "No previous version found. Using initial ID: %s, Version: %s",
            new_id, new_version
        )
    else:
        latest_id, latest_version = latest_version_data
        next_id_int = int(latest_id) + 1
        new_id = f"{next_id_int:03d}"

        major, minor, patch = parse_version(latest_version)

        print(f"\nCurrent version: {latest_version}")
        print("Select increment type:")
        print("1) Major (reset minor & patch)")
        print("2) Minor (reset patch)")
        print("3) Patch")
        print("Enter) Auto-increment patch")
        print("c/C) Cancel")

        choice = input("\nEnter choice (1/2/3/Enter/c): ").strip().lower()

        if choice in ('c', 'cancel'):
            logging.info("Version increment cancelled by user.")
            return None, None
        elif choice == '1':
            new_version = f"{major + 1}.0.0"
        elif choice == '2':
            new_version = f"{major}.{minor + 1}.0"
        elif choice in ('3', ''):
            new_version = f"{major}.{minor}.{patch + 1}"
        else:
            print("Invalid choice. Defaulting to patch increment.")
            new_version = f"{major}.{minor}.{patch + 1}"

        logging.info(
            "Generated new version: %s from choice: %s", new_version, choice
        )

    return new_id, new_version


# ---------------------------------------------------------------------------
# Changelog generation
# ---------------------------------------------------------------------------

def update_changelog():
    """Regenerate CHANGELOG.md in Keep-a-Changelog Markdown format."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, version_number, notes, timestamp "
            "FROM releases ORDER BY id DESC"
        )
        releases = cursor.fetchall()

    try:
        with open(CHANGELOG_FILE, 'w', encoding='utf-8') as f:
            f.write("# Changelog\n\n")
            f.write(
                "All notable changes to this project will be documented "
                "in this file.\n\n"
            )
            f.write(
                "The format is based on "
                "[Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
            )
            f.write(
                "and this project adheres to "
                "[Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n\n"
            )

            if not releases:
                f.write("> No releases have been recorded yet.\n")
                logging.info("CHANGELOG.md generated — no releases yet.")
                return

            for i, (rel_id, version, notes, ts) in enumerate(releases):
                date_str = ts[:10] if ts else 'unknown'
                latest_tag = "  *(Latest)*" if i == 0 else ""
                f.write(f"## [{version}] - {date_str}{latest_tag}\n\n")

                if not notes or not notes.strip():
                    f.write("No release notes provided.\n\n")
                    continue

                lines = [l.strip() for l in notes.splitlines() if l.strip()]
                categorized: dict[str, list[str]] = {
                    'Added': [], 'Changed': [], 'Fixed': [], 'Other': []
                }

                for line in lines:
                    lower = line.lower()
                    if any(w in lower for w in ['add', 'new', 'implement', 'feat', 'create']):
                        categorized['Added'].append(line)
                    elif any(w in lower for w in ['fix', 'bug', 'correct', 'resolve', 'hotfix']):
                        categorized['Fixed'].append(line)
                    elif any(w in lower for w in ['change', 'update', 'refactor', 'improv', 'modify']):
                        categorized['Changed'].append(line)
                    else:
                        categorized['Other'].append(line)

                for category, items in categorized.items():
                    if items:
                        f.write(f"### {category}\n\n")
                        for item in items:
                            text = item[0].upper() + item[1:] if item else item
                            f.write(f"- {text}\n")
                        f.write("\n")

                if all(len(lst) == 0 for lst in categorized.values()) and lines:
                    f.write("### Notes\n\n")
                    for line in lines:
                        f.write(f"- {line}\n")
                    f.write("\n")

            f.write("\n<!-- Generated by version-management tool -->\n")

        print(f"[SUCCESS] Updated {CHANGELOG_FILE} with {len(releases)} release(s).")
        logging.info(
            "Successfully updated CHANGELOG.md with %d releases.", len(releases)
        )

    except Exception as e:
        print(f"[ERROR] Could not write {CHANGELOG_FILE}: {e}")
        logging.error("Failed to update CHANGELOG.md: %s", e)


# ---------------------------------------------------------------------------
# Core operations (called from notifier system menu or standalone)
# ---------------------------------------------------------------------------

def add_version_notes():
    """Prompt the user to record a new version release."""
    latest_data = get_latest_version_data()
    version_id, version_number = generate_next_version(latest_data)

    if version_id is None:
        print("\n[INFO] Operation cancelled.")
        logging.info("Version addition cancelled.")
        return

    print("\n--- Add New Release ---")
    print(f"Generated Version ID:     {version_id}")
    print(f"Generated Version Number: {version_number}")

    notes = ""
    while not notes:
        notes = input("Enter release notes (required): ").strip()

    timestamp = datetime.now().isoformat(sep=' ', timespec='seconds')

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO releases (id, version_number, notes, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (version_id, version_number, notes, timestamp)
            )
            conn.commit()
            print(
                f"\n[SUCCESS] Version {version_number} "
                f"(ID: {version_id}) added to history."
            )
            logging.info(
                "Added version %s (ID: %s) to database.", version_number, version_id
            )
            update_changelog()
        except sqlite3.IntegrityError as e:
            print(
                f"\n[ERROR] Version {version_number} already exists. ({e})"
            )
            logging.error(
                "Failed to add version %s: %s", version_number, e
            )


def view_version_history():
    """Print all recorded releases in reverse chronological order."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, version_number, notes, timestamp "
            "FROM releases ORDER BY id DESC"
        )
        releases = cursor.fetchall()

    print("\n--- Full Version History ---")
    if not releases:
        print("No versions recorded yet.")
        logging.info("Viewed version history: no entries found.")
        return

    for version_id, version_number, notes, timestamp in releases:
        print(f"\nID: {version_id} | Version: {version_number}")
        print(f"  Notes:    {notes}")
        print(f"  Released: {timestamp}")
    print("----------------------------")
    logging.info(
        "Viewed version history: %d entries displayed.", len(releases)
    )


def edit_notes():
    """Update the release notes for an existing version."""
    version = input(
        "\nEnter the version number to edit (e.g., 1.0.32): "
    ).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT notes FROM releases WHERE version_number = ?", (version,)
        )
        result = cursor.fetchone()

        if result is None:
            print(f"[ERROR] Version {version} not found.")
            logging.warning(
                "Attempted to edit notes for non-existent version: %s", version
            )
            return

        print(f"\nCurrent Notes for {version}: {result[0]}")
        new_notes = input("Enter the new, updated notes: ").strip()

        if new_notes:
            timestamp = datetime.now().isoformat(sep=' ', timespec='seconds')
            cursor.execute(
                "UPDATE releases SET notes = ?, timestamp = ? "
                "WHERE version_number = ?",
                (new_notes, timestamp, version)
            )
            conn.commit()
            print(f"[SUCCESS] Notes for version {version} updated.")
            logging.info("Updated notes for version %s.", version)
            update_changelog()
        else:
            print("[INFO] Edit cancelled — notes unchanged.")
            logging.info("Edit notes cancelled for version %s.", version)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def display_menu():
    print("\n--- Version Management System ---")
    print("1. Add New Version Notes (Auto-Increment)")
    print("2. View All Version History")
    print("3. Edit Existing Version Notes")
    print("0. Exit")
    return input("Enter your choice (0-3): ")


def main():
    setup_logging()
    setup_database()

    while True:
        choice = display_menu()

        if choice == '1':
            add_version_notes()
        elif choice == '2':
            view_version_history()
        elif choice == '3':
            edit_notes()
        elif choice == '0':
            print("\nExiting Version Manager. 👋")
            logging.info("Application exited.")
            break
        else:
            print("[ERROR] Invalid choice. Please try again.")
            logging.warning("Invalid menu choice: %s", choice)


if __name__ == "__main__":
    main()
