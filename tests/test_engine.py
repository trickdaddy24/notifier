# -*- coding: utf-8 -*-
"""
Pytest coverage for the shared delivery engine (notifier.notifications).

These tests target the behaviour that was previously broken in the web path:
recurrence rollover, the "≥1 channel success marks sent" rule, and credential
resolution (database first, environment second). They run fully offline against
an isolated temp DB and a fake in-memory channel — no network, no real creds.
"""
import importlib
import time

import pytest


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """Fresh engine + isolated DB per test.

    The DB path is read at import time in notifier.db, so we set the env var and
    reload both modules to bind a clean database file.
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("NOTIFIER_DB_PATH", str(db_file))
    monkeypatch.setenv("TIMEZONE", "America/Chicago")

    import notifier.db as db
    importlib.reload(db)
    import notifier.notifications as N
    importlib.reload(N)

    db.init_db()
    N.set_quiet_mode(True)
    return db, N


def _insert(db, message, due_ts, recurrence=None, repeat_time=None, sent=0):
    with db.get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO notifications (message, due_time, due_ts, sent, recurrence, repeat_time)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (message, "x", due_ts, sent, recurrence, repeat_time),
        )
        conn.commit()
        return c.lastrowid


def _use_fake_channel(N, result=(True, "HTTP 200")):
    """Replace the registry with a single fake channel; return its call log."""
    calls = []

    def fake_send(msg, **kwargs):
        calls.append(msg)
        return result

    N.CHANNELS[:] = [{"name": "fake", "label": "Fake", "emoji": "x",
                      "send": fake_send, "fields": [{"key": "FAKE_X"}]}]
    return calls


def _rows(db):
    with db.get_db() as conn:
        c = conn.cursor()
        return [dict(r) for r in c.execute(
            "SELECT id, message, sent, due_ts, recurrence FROM notifications ORDER BY id"
        ).fetchall()]


# ── Recurrence rollover (the headline web bug) ─────────────────────────────────

def test_weekly_recurrence_creates_next_occurrence(engine):
    db, N = engine
    _use_fake_channel(N)
    _insert(db, "weekly", int(time.time()) - 100, recurrence="weekly")

    N.send_notifications()

    rows = _rows(db)
    assert len(rows) == 2, "a new occurrence should have been inserted"
    sent = [r for r in rows if r["sent"] == 1]
    pending = [r for r in rows if r["sent"] == 0]
    assert len(sent) == 1 and len(pending) == 1
    assert pending[0]["due_ts"] > int(time.time()), "next occurrence must be in the future"
    assert pending[0]["recurrence"] == "weekly"


def test_daily_recurrence_uses_repeat_time(engine):
    db, N = engine
    _use_fake_channel(N)
    _insert(db, "daily", int(time.time()) - 100, recurrence="daily", repeat_time="09:00")

    N.send_notifications()

    pending = [r for r in _rows(db) if r["sent"] == 0]
    assert len(pending) == 1
    nxt = db._from_ts(pending[0]["due_ts"])
    assert (nxt.hour, nxt.minute) == (9, 0)


def test_one_time_reminder_is_not_recreated(engine):
    db, N = engine
    _use_fake_channel(N)
    _insert(db, "once", int(time.time()) - 100, recurrence=None)

    N.send_notifications()

    rows = _rows(db)
    assert len(rows) == 1 and rows[0]["sent"] == 1


# ── ≥1-success mark-sent rule ──────────────────────────────────────────────────

def test_not_marked_sent_when_all_channels_fail(engine):
    db, N = engine
    _use_fake_channel(N, result=(False, "HTTP 500 - down"))
    nid = _insert(db, "boom", int(time.time()) - 100, recurrence="weekly")

    N.send_notifications()

    rows = _rows(db)
    assert len(rows) == 1, "no next occurrence when nothing was delivered"
    assert rows[0]["id"] == nid and rows[0]["sent"] == 0


def test_only_id_force_sends_regardless_of_due(engine):
    db, N = engine
    calls = _use_fake_channel(N)
    # Not due yet (future) and already sent — only_id must still deliver it.
    nid = _insert(db, "forced", int(time.time()) + 9999, sent=1)

    N.send_notifications(only_id=nid)

    assert calls, "only_id should force a delivery attempt"


def test_audit_log_written_per_channel(engine):
    db, N = engine
    _use_fake_channel(N)
    nid = _insert(db, "logged", int(time.time()) - 100)

    N.send_notifications()

    with db.get_db() as conn:
        c = conn.cursor()
        logs = c.execute(
            "SELECT channel, status FROM logs WHERE notification_id = ?", (nid,)
        ).fetchall()
    assert any(row["channel"] == "fake" and row["status"] == "SUCCESS" for row in logs)


# ── Credential resolution: DB first, then environment ──────────────────────────

def test_credential_prefers_database_over_env(engine, monkeypatch):
    db, N = engine
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
    # No DB value yet → env wins.
    assert N._get_credential("telegram", "bot_token") == "from-env"
    # Set a DB value → DB wins.
    N.set_channel_credential("telegram", "bot_token", "from-db")
    assert N._get_credential("telegram", "bot_token") == "from-db"


def test_channel_configured_reflects_credentials(engine, monkeypatch):
    db, N = engine
    telegram = next(c for c in N.CHANNELS if c["name"] == "telegram")
    assert N._channel_configured(telegram) is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    assert N._channel_configured(telegram) is True
