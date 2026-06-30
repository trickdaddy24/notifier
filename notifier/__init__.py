"""
Notifier package.

Exposes shared database and notification delivery layers.
"""

# ── Single source of truth for the version string ─────────────────────────────
# At runtime the live version comes from version_notes.db (via version_manager).
# NOTE: the Docker image does NOT ship version_manager.py or version_notes.db, so
# in the deployed web container `get_app_version()` always falls back to THIS
# constant — making it the de-facto source of truth for the version badge. Always
# bump it on every release (it is the only literal that affects the live UI).
__version__ = "2.6.6"

from .db import get_db, init_db, DB_PATH  # noqa: F401,E402
from .notifications import send_notifications, send_heartbeat, CHANNELS, set_quiet_mode  # noqa: F401,E402
