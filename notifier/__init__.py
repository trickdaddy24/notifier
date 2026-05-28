"""
Notifier package.

Currently exposes the shared database layer for use by both
the CLI and the web UI.
"""

from .db import get_db, init_db, DB_PATH  # noqa: F401
