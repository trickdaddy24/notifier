"""
Notifier package.

Exposes shared database and notification delivery layers.
"""

# ── Single source of truth for the version string ─────────────────────────────
# At runtime the live version comes from version_notes.db (via version_manager).
# This constant is the one static anchor every other module falls back to when
# that DB is unavailable, and the value the docs/labels reflect. Keep it in sync
# with the latest release on each bump (it is the only literal to update).
__version__ = "2.6.1"

from .db import get_db, init_db, DB_PATH  # noqa: F401,E402
from .notifications import send_notifications, send_heartbeat, CHANNELS, set_quiet_mode  # noqa: F401,E402
