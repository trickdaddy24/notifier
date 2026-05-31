# -*- coding: utf-8 -*-
"""
Smoke test for the shared engine — no pytest dependency, no network, no input().

Run:  python test_notifier_smoke.py

Exercises the pure logic that the CLI and web UI both depend on: date parsing,
recurrence rollover, timezone->epoch conversion, retry classification, and the
channel registry. These now live in the package (notifier.db / notifier.notifications),
so we import them from there rather than from the legacy top-level script.
"""
import os
import sys
import time as _t
from datetime import datetime
from zoneinfo import ZoneInfo

# Force a known timezone and an isolated DB BEFORE importing the package so the
# env-driven helpers see them.
os.environ["TIMEZONE"] = "America/Chicago"
os.environ.setdefault("NOTIFIER_DB_PATH", os.path.join(os.path.dirname(__file__), ".smoke_test.db"))

from notifier import db as D            # noqa: E402
from notifier import notifications as N  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# ── Date parsing ───────────────────────────────────────────────────────────────
check("parse YYYY-MM-DD HH:MM",
      D._parse_due_time("2026-05-20 14:00") == datetime(2026, 5, 20, 14, 0))
check("parse MM-DD-YYYY HH:MM",
      D._parse_due_time("05-20-2026 14:00") == datetime(2026, 5, 20, 14, 0))
check("parse garbage -> None",
      D._parse_due_time("not a date") is None)

# ── Month rollover (clamps to last valid day) ──────────────────────────────────
check("Jan 31 -> Feb 28 (2026 non-leap)",
      D._next_month_dt(datetime(2026, 1, 31)) == datetime(2026, 2, 28))
check("Feb 28 -> Mar 28",
      D._next_month_dt(datetime(2026, 2, 28)) == datetime(2026, 3, 28))
check("Dec -> Jan next year",
      D._next_month_dt(datetime(2026, 12, 15)) == datetime(2027, 1, 15))

# ── Recurrence rolls forward past 'now' ────────────────────────────────────────
_now = int(_t.time())
_wk = D._next_recurrence_ts(_now - 10 * 604800, "weekly")
check("weekly recurrence is in the future", _wk is not None and _wk > _now, f"got {_wk}")
_bw = D._next_recurrence_ts(_now - 10 * 1209600, "biweekly")
check("biweekly recurrence is in the future", _bw is not None and _bw > _now)
_mo = D._next_recurrence_ts(_now - 10 * 2_592_000, "monthly")
check("monthly recurrence is in the future", _mo is not None and _mo > _now)
check("unknown recurrence -> None", D._next_recurrence_ts(_now, "yearly") is None)

# ── Timezone -> epoch (the Bug #2 contract) ────────────────────────────────────
TZ = ZoneInfo("America/Chicago")
naive = datetime(2026, 7, 1, 9, 0)            # 09:00 in Chicago (summer => -05:00)
expected_ts = int(naive.replace(tzinfo=TZ).timestamp())
check("_to_ts localizes to TIMEZONE", D._to_ts(naive) == expected_ts,
      f"_to_ts={D._to_ts(naive)} expected={expected_ts}")

nd = D._next_daily_time("09:00")
check("_next_daily_time returns a datetime", nd is not None)
check("daily epoch matches _to_ts of its own walltime",
      D._to_ts(nd) == int(nd.replace(tzinfo=TZ).timestamp()))

# ── Channel registry ───────────────────────────────────────────────────────────
names = {c["name"] for c in N.CHANNELS}
check("registry has the 4 core channels",
      {"telegram", "discord", "pushover", "email"}.issubset(names), f"got {names}")
for c in N.CHANNELS:
    check(f"channel '{c['name']}' has a send callable", callable(c["send"]))

# ── Retry / backoff (_is_transient + _deliver) ────────────────────────────────
check("missing-config is NOT transient", N._is_transient("Missing TELEGRAM_BOT_TOKEN") is False)
check("HTTP 500 is transient",          N._is_transient("HTTP 500 - boom") is True)
check("HTTP 429 is transient",          N._is_transient("HTTP 429 - slow down") is True)
check("HTTP 401 is NOT transient",      N._is_transient("HTTP 401 - bad token") is False)
check("timeout is transient",           N._is_transient("Connection timed out") is True)
check("empty is NOT transient",         N._is_transient("") is False)

calls = {"n": 0}
def _flaky(_msg):
    calls["n"] += 1
    return (True, "HTTP 200") if calls["n"] >= 3 else (False, "HTTP 503 - down")
ok, resp = N._deliver({"name": "fake", "send": _flaky}, "hi", retries=3, backoff=0)
check("transient failure retried then succeeds", ok and calls["n"] == 3,
      f"ok={ok} calls={calls['n']}")

calls2 = {"n": 0}
def _perm(_msg):
    calls2["n"] += 1
    return (False, "HTTP 401 - bad token")
ok2, _ = N._deliver({"name": "fake", "send": _perm}, "hi", retries=3, backoff=0)
check("permanent failure not retried", (not ok2) and calls2["n"] == 1,
      f"ok={ok2} calls={calls2['n']}")

# ── Relative due labels ────────────────────────────────────────────────────────
now = int(_t.time())
check("future ~2h -> 'in 2h'", D._relative_due(now + 2 * 3600 + 30).startswith("in 2h"))
check("past -> 'overdue'",     D._relative_due(now - 5000).startswith("overdue"))
check("within a minute -> 'due now'", D._relative_due(now + 10) == "due now")
check("zero ts -> ''",         D._relative_due(0) == "")

# Clean up the isolated smoke DB if we created it.
try:
    os.remove(os.environ["NOTIFIER_DB_PATH"])
except OSError:
    pass

print(f"\n  {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
