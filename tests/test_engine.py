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
    # Keep the DB pristine — don't auto-seed the sample cruise events.
    monkeypatch.setenv("NOTIFIER_SKIP_EVENT_SEED", "1")

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


# ── Countdown events ───────────────────────────────────────────────────────────

def _future_date(days):
    # "Today" in the engine's pinned zone (the fixture sets TIMEZONE to
    # America/Chicago) — host-local date.today() can differ near midnight.
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/Chicago")).date()
    return (today + timedelta(days=days)).strftime("%Y-%m-%d")


def _pending_for_event(db, event_id):
    with db.get_db() as conn:
        c = conn.cursor()
        return c.execute(
            "SELECT message, due_ts FROM notifications WHERE event_id = ? AND sent = 0 ORDER BY due_ts",
            (event_id,),
        ).fetchall()


def test_create_event_expands_future_milestones(engine):
    db, _ = engine
    # 100 days out with these offsets -> all four are still in the future.
    eid = db.create_event("Cruise", _future_date(100), category="cruise",
                          milestones="60,30,7,0", send_time="09:00")
    assert eid is not None
    rows = _pending_for_event(db, eid)
    assert len(rows) == 4, "every future milestone should become a notification"
    # Earliest pending ping should be the 60-days-before one.
    assert "60 days until Cruise" in rows[0]["message"]


def test_event_skips_past_milestones(engine):
    db, _ = engine
    # 5 days out: the 60/30/14/7 milestones are already in the past, only 3,1,0 remain.
    eid = db.create_event("Trip", _future_date(5), milestones="60,30,14,7,3,1,0")
    rows = _pending_for_event(db, eid)
    assert len(rows) == 3, f"only 3,1,0 should remain, got {len(rows)}"


def test_event_entirely_in_past_creates_nothing(engine):
    db, _ = engine
    eid = db.create_event("Old cruise", _future_date(-10), milestones="7,1,0")
    assert eid is not None  # event row still created (kept for history/view)
    assert _pending_for_event(db, eid) == []


def test_update_event_reexpands(engine):
    db, _ = engine
    eid = db.create_event("Move", _future_date(5), milestones="60,30,14,7,3,1,0")
    assert len(_pending_for_event(db, eid)) == 3
    # Push it far out -> all seven milestones now fit in the future.
    assert db.update_event(eid, target_date=_future_date(100))
    assert len(_pending_for_event(db, eid)) == 7


def test_delete_event_clears_pending_notifications(engine):
    db, _ = engine
    eid = db.create_event("Launch", _future_date(50), milestones="30,7,0")
    assert len(_pending_for_event(db, eid)) == 3
    assert db.delete_event(eid)
    assert _pending_for_event(db, eid) == []
    assert db.get_event(eid) is None


def test_event_milestone_delivers_through_scheduler(engine):
    db, N = engine
    calls = _use_fake_channel(N)
    eid = db.create_event("Birthday", _future_date(50), milestones="30,7,0")
    # Force the earliest milestone's due_ts into the past so it's due now.
    rows = _pending_for_event(db, eid)
    nid = None
    with db.get_db() as conn:
        c = conn.cursor()
        first = c.execute(
            "SELECT id FROM notifications WHERE event_id = ? ORDER BY due_ts LIMIT 1", (eid,)
        ).fetchone()
        nid = first["id"]
        c.execute("UPDATE notifications SET due_ts = ? WHERE id = ?",
                  (int(time.time()) - 100, nid))
        conn.commit()

    N.send_notifications()
    assert calls, "a due milestone should be delivered by send_notifications"
    with db.get_db() as conn:
        c = conn.cursor()
        sent = c.execute("SELECT sent FROM notifications WHERE id = ?", (nid,)).fetchone()["sent"]
    assert sent == 1


def test_parse_event_date_accepts_us_and_iso(engine):
    db, _ = engine
    from datetime import date
    assert db._parse_event_date("2026-07-12") == date(2026, 7, 12)
    assert db._parse_event_date("7/12/26") == date(2026, 7, 12)
    assert db._parse_event_date("07/12/2026") == date(2026, 7, 12)
    assert db._parse_event_date("garbage") is None


def test_day_of_message_is_celebratory(engine):
    db, _ = engine
    msg = db.format_event_message("Sail Away", 0, "2026-07-12", category="cruise")
    assert "Today is the day" in msg and "🚢" in msg


# ── Daily cadence ──────────────────────────────────────────────────────────────

def test_cadence_roundtrip_and_default(engine):
    db, _ = engine
    m = db.create_event("A", _future_date(30))
    d = db.create_event("B", _future_date(30), cadence="daily")
    bad = db.create_event("C", _future_date(30), cadence="weird")
    assert db.get_event(m)["cadence"] == "milestones"
    assert db.get_event(d)["cadence"] == "daily"
    assert db.get_event(bad)["cadence"] == "milestones"  # unknown -> default
    # update_event can switch cadence and preserves it when untouched
    assert db.update_event(d, cadence="milestones")
    assert db.get_event(d)["cadence"] == "milestones"
    assert db.update_event(m, title="A2")
    assert db.get_event(m)["cadence"] == "milestones"


def test_daily_cadence_expands_one_per_day(engine):
    db, _ = engine
    eid = db.create_event("Cruise", _future_date(10), cadence="daily", send_time="23:59")
    rows = _pending_for_event(db, eid)
    assert len(rows) == 11  # offsets 10..0 inclusive


def test_daily_cadence_event_today_gets_finale_only(engine):
    db, _ = engine
    eid = db.create_event("Now", _future_date(0), cadence="daily", send_time="23:59")
    assert len(_pending_for_event(db, eid)) == 1  # just the day-0 tick


def test_daily_cadence_capped_at_365(engine):
    db, _ = engine
    eid = db.create_event("Far", _future_date(500), cadence="daily", send_time="23:59")
    assert len(_pending_for_event(db, eid)) == 366  # offsets 365..0


def test_daily_cadence_reexpands_on_edit(engine):
    db, _ = engine
    eid = db.create_event("Trip", _future_date(5), cadence="daily", send_time="23:59")
    assert len(_pending_for_event(db, eid)) == 6
    assert db.update_event(eid, target_date=_future_date(8))
    assert len(_pending_for_event(db, eid)) == 9


# ── Cruise message pack ────────────────────────────────────────────────────────

def test_cruise_pack_has_15_templates(engine):
    db, _ = engine
    assert len(db.CRUISE_PACK) == 15
    for tpl in db.CRUISE_PACK:
        assert "{days} days until {title}" in tpl


def test_cruise_pack_rotation_deterministic(engine):
    db, _ = engine
    a = db.format_event_message("Cruise", 17, "2026-07-12", category="cruise")
    b = db.format_event_message("Cruise", 17, "2026-07-12", category="cruise")
    adjacent = db.format_event_message("Cruise", 16, "2026-07-12", category="cruise")
    assert a == b  # same day -> same message
    assert a != adjacent  # consecutive days differ
    assert a.startswith(db.CRUISE_PACK[17 % 15].format(days=17, title="Cruise"))
    assert "17 days until Cruise" in a


def test_cruise_tomorrow_and_finale_specials(engine):
    db, _ = engine
    one = db.format_event_message("Cruise", 1, "2026-07-12", category="cruise")
    zero = db.format_event_message("Cruise", 0, "2026-07-12", category="cruise")
    assert "TOMORROW" in one
    assert "Today is the day" in zero and "🚢" in zero


def test_cruise_message_appends_details_and_noncruise_unchanged(engine):
    db, _ = engine
    msg = db.format_event_message("Cruise", 5, "2026-07-12",
                                  category="cruise", details="Deck 9, cabin 9242")
    assert msg.endswith("\nDeck 9, cabin 9242")
    plain = db.format_event_message("Dentist", 5, "2026-07-12")
    assert plain == "📅 5 days until Dentist on 2026-07-12!"
