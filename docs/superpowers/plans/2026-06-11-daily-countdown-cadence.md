# Daily Countdown Cadence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-event `daily` cadence (one countdown notification every day until the event) plus a 15-message funny cruise emoji pack, per the approved spec at `docs/superpowers/specs/2026-06-11-countdown-events-design.md`.

**Architecture:** Events already expand into ordinary `notifications` rows via `expand_event()` (`notifier/db.py`), and editing an event re-expands them. We add a `cadence` column (`milestones` default | `daily`); daily events derive their day-offsets at expansion time (`range(days_left..0)`, capped at 365), so edit-propagation comes free from the existing re-expand. Cruise messages get a rotating 15-template pack selected by `days_left % 15`. The delivery engine gains a stale-tick skip so scheduler downtime sends one current tick per event, not a backlog.

**Tech Stack:** Python 3 / SQLite (`notifier/db.py`, `notifier/notifications.py`), FastAPI (`web/main.py`), Jinja2 + vanilla JS (`web/templates/dashboard.html`), pytest (`tests/test_engine.py`).

**Conventions:**
- Run tests from the repo root: `G:\kvcd\VSCODE - Main\Plex Stuff\Notifier`
- Test command: `python -m pytest tests -q` (16 tests pass before this plan starts)
- The `engine` fixture in `tests/test_engine.py` reloads `notifier.db` + `notifier.notifications` against a temp DB — use `db.<fn>` / `N.<fn>` accessors, never import at module top.
- New event tests use `send_time="23:59"` so "today's" tick is still in the future when tests run (avoids time-of-day flakes).
- Spec notes that need no code: the cruise-notifier seed events stay milestone-based (`create_event` default covers it), and there is no separate event export/import path in the codebase — nothing to extend.

---

### Task 1: `cadence` column + constants + create/update plumbing

**Files:**
- Modify: `notifier/db.py` (events CREATE TABLE ~line 133; migrations ~line 154; constants near `DEFAULT_MILESTONES` ~line 414; `create_event` ~line 506; `update_event` ~line 575)
- Test: `tests/test_engine.py` (append at end)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_cadence_roundtrip_and_default -q`
Expected: FAIL — `TypeError: create_event() got an unexpected keyword argument 'cadence'`

- [ ] **Step 3: Implement**

In `notifier/db.py`:

(a) Add to the `events` CREATE TABLE (after the `send_time` line, before `created_ts`):

```python
                cadence      TEXT NOT NULL DEFAULT 'milestones',  -- 'milestones' | 'daily'
```

(b) After the existing notifications-column migration loop (~line 165), add an events migration loop:

```python
        # Migrations for older events tables
        for col, definition in [
            ("cadence", "TEXT NOT NULL DEFAULT 'milestones'"),
        ]:
            try:
                c.execute(f"ALTER TABLE events ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # Column already exists
```

(c) Next to `DEFAULT_MILESTONES = "60,30,14,7,3,1,0"` add:

```python
CADENCE_MILESTONES = "milestones"
CADENCE_DAILY = "daily"
CADENCES = (CADENCE_MILESTONES, CADENCE_DAILY)
DAILY_CADENCE_CAP = 365  # max daily ticks expanded for far-future events


def _normalize_cadence(value) -> str:
    """Coerce any stored/user value to a valid cadence ('milestones' default)."""
    v = (value or "").strip().lower()
    return v if v in CADENCES else CADENCE_MILESTONES
```

(d) `create_event`: add `cadence: Optional[str] = None` to the signature (after `send_time`), then inside, after `send_time = (...)`:

```python
    cadence = _normalize_cadence(cadence)
```

and extend the INSERT:

```python
        c.execute(
            "INSERT INTO events (title, target_date, target_ts, category, details,"
            " milestones, send_time, cadence, created_ts, active)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (title.strip(), canonical_date, target_ts, category or None,
             details or None, milestones_csv, send_time, cadence, now_ts),
        )
```

(e) `update_event`: in the field-merging block add:

```python
    cadence = _normalize_cadence(fields.get("cadence", existing.get("cadence")))
```

and extend the UPDATE:

```python
        c.execute(
            "UPDATE events SET title=?, target_date=?, target_ts=?, category=?,"
            " details=?, milestones=?, send_time=?, cadence=?, active=? WHERE id=?",
            (title.strip(), canonical_date, target_ts, category or None,
             details or None, milestones_csv, send_time, cadence, active, event_id),
        )
```

(Also update `update_event`'s docstring "Accepts any of:" list to include `cadence`.)

- [ ] **Step 4: Run tests to verify all pass**

Run: `python -m pytest tests -q`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add notifier/db.py tests/test_engine.py
git commit -m "feat(events): cadence column (milestones|daily) with create/update plumbing"
```

---

### Task 2: Daily expansion in `expand_event`

**Files:**
- Modify: `notifier/db.py` (`expand_event` ~line 633; new helper above it)
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine.py -q -k daily_cadence`
Expected: 4 FAILED — daily events currently expand the default milestones CSV (7 rows for far-future, etc.)

- [ ] **Step 3: Implement**

In `notifier/db.py`, directly above `expand_event`, add:

```python
def _event_offsets(event: dict) -> list[int]:
    """Day-offsets to expand for an event, derived per its cadence.

    Daily events compute their range at expansion time (capped at
    DAILY_CADENCE_CAP), so re-expansion after an edit always yields the
    correct new run. Milestone events use their stored CSV.
    """
    if _normalize_cadence(event.get("cadence")) == CADENCE_DAILY:
        days_left = days_until(event["target_date"])
        if days_left is None or days_left < 0:
            return []
        return list(range(min(days_left, DAILY_CADENCE_CAP), -1, -1))
    return _parse_milestones(event["milestones"])
```

In `expand_event`, replace:

```python
    offsets = _parse_milestones(event["milestones"])
```

with:

```python
    offsets = _event_offsets(event)
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `python -m pytest tests -q`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add notifier/db.py tests/test_engine.py
git commit -m "feat(events): daily cadence expands one tick per day, capped at 365"
```

---

### Task 3: Cruise message pack (15 rotating templates + specials)

**Files:**
- Modify: `notifier/db.py` (constants above `format_event_message` ~line 480; the function's cruise branch)
- Test: `tests/test_engine.py` (append)

Existing message tests stay green by design: every pack template contains the literal phrase `{days} days until {title}` (keeps `test_create_event_expands_future_milestones`), and the finale contains `Today is the day` + `🚢` (keeps `test_day_of_message_is_celebratory`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine.py -q -k cruise`
Expected: FAIL — `AttributeError: module 'notifier.db' has no attribute 'CRUISE_PACK'`

- [ ] **Step 3: Implement**

In `notifier/db.py`, above `format_event_message`, add:

```python
# Rotating cruise countdown flavour. Every template keeps the literal phrase
# "{days} days until {title}" so countdown info survives the jokes; selection
# is days_left % 15 (deterministic, consecutive days never repeat).
CRUISE_PACK = [
    "🚢💨 {days} days until {title}! The ship is warming up its horn... ⚓😤",
    "🍹⏳ {days} days until {title}! The umbrella drinks are already chilling. 🧊",
    "🌊🛳️ {days} days until {title}! The ocean said it's saving you a spot. 🤙",
    "🧳😱 {days} days until {title}! Started packing yet? (We both know.) 👀",
    "☀️🕶️ {days} days until {title}! Sunscreen: buy two. Trust us. 🧴",
    "🦞🍤 {days} days until {title}! The buffet has NO idea what's coming. 😈",
    "⚓😎 {days} days until {title}! Start practicing your captain wave. 👋",
    "🎰🛳️ {days} days until {title}! Sea air is basically free money. 🤑",
    "🐬✨ {days} days until {title}! The dolphins have been notified. 📣",
    "🛏️🌊 {days} days until {title}! Soon your hardest choice is pool or nap. 🏊",
    "📅🚢 {days} days until {title}! Your out-of-office is begging to be written. 📨",
    "🍕🍦 {days} days until {title}! Calories don't count in international waters. 🌐",
    "🌅🥂 {days} days until {title}! Sunset deck toasts incoming. 🌇",
    "🧭🗺️ {days} days until {title}! Adventure is plotting a course to you. 🚀",
    "🎶🛳️ {days} days until {title}! Cue the boarding-day playlist. 🎧",
]
CRUISE_TOMORROW = "😱🚢 TOMORROW is the day — {title}! Set every alarm. All of them. ⏰⏰⏰"
CRUISE_FINALE = "🎉🚢 Today is the day — {title}! Bon voyage! 🍹🌊"


def _friendly_date(target_date: str) -> str:
    """'2026-08-15' -> 'Aug 15' (falls back to the raw string)."""
    d = _parse_event_date(target_date)
    return f"{d.strftime('%b')} {d.day}" if d else target_date
```

Replace the body of `format_event_message` (keep the signature and docstring; update the docstring's second paragraph to mention the rotating pack):

```python
    cruise = (category or "").lower() == "cruise"

    if cruise:
        if days_left > 1:
            msg = (CRUISE_PACK[days_left % len(CRUISE_PACK)]
                   .format(days=days_left, title=title)
                   + f" ({_friendly_date(target_date)})")
        elif days_left == 1:
            msg = CRUISE_TOMORROW.format(title=title)
        else:
            msg = CRUISE_FINALE.format(title=title)
    else:
        if days_left > 1:
            body = f"{days_left} days until {title} on {target_date}"
        elif days_left == 1:
            body = f"Tomorrow is the day — {title} on {target_date}"
        else:
            body = f"Today is the day — {title} on {target_date}"
        msg = f"📅 {body}!"

    if details:
        msg += f"\n{details}"
    return msg
```

- [ ] **Step 4: Run tests to verify all pass (including the two pre-existing message tests)**

Run: `python -m pytest tests -q`
Expected: 25 passed

- [ ] **Step 5: Commit**

```bash
git add notifier/db.py tests/test_engine.py
git commit -m "feat(events): 15-template rotating cruise message pack with tomorrow/finale specials"
```

---

### Task 4: Stale-tick skip in the delivery engine

**Files:**
- Modify: `notifier/notifications.py` (`send_notifications` ~line 368)
- Test: `tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine.py`:

```python
def test_stale_event_ticks_skipped_after_downtime(engine):
    db, N = engine
    calls = _use_fake_channel(N)
    eid = db.create_event("Cruise", _future_date(10), category="cruise",
                          cadence="daily", send_time="23:59")
    # Simulate 3 days of scheduler downtime: backdate the 3 earliest ticks.
    now = int(time.time())
    with db.get_db() as conn:
        c = conn.cursor()
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM notifications WHERE event_id = ? ORDER BY due_ts LIMIT 3",
            (eid,),
        ).fetchall()]
        for i, nid in enumerate(ids):
            c.execute("UPDATE notifications SET due_ts = ? WHERE id = ?",
                      (now - (3 - i) * 86400, nid))
        conn.commit()

    N.send_notifications()

    assert len(calls) == 1, "only the most current tick should be delivered"
    with db.get_db() as conn:
        c = conn.cursor()
        stale = c.execute(
            "SELECT COUNT(*) FROM logs WHERE status = 'SKIPPED_STALE'"
        ).fetchone()[0]
        unsent = c.execute(
            "SELECT COUNT(*) FROM notifications WHERE id IN (?, ?, ?) AND sent = 0",
            tuple(ids),
        ).fetchone()[0]
    assert stale == 2
    assert unsent == 0, "all three overdue ticks must be retired"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py::test_stale_event_ticks_skipped_after_downtime -q`
Expected: FAIL — `len(calls)` is 3 (every overdue tick currently delivers)

- [ ] **Step 3: Implement**

In `notifier/notifications.py` `send_notifications`:

(a) Add `event_id` to **both** SELECT column lists:

```python
                "SELECT id, message, due_ts, recurrence, repeat_time, event_id"
```

(one in the `only_id` branch, one in the scheduled branch — same change).

(b) After `pending = c.fetchall()` and the early-return block (i.e. right after the `logger.info(...)` line), insert:

```python
        # Stale-tick skip: if downtime left several ticks of the same event
        # overdue (daily cadence especially), deliver only the most current
        # one and retire the rest quietly — never spam a backlog.
        if only_id is None:
            latest_by_event = {}
            for row in pending:
                ev_id = row["event_id"]
                if ev_id is not None:
                    best = latest_by_event.get(ev_id)
                    if best is None or row["due_ts"] > best["due_ts"]:
                        latest_by_event[ev_id] = row
            stale_ids = {
                row["id"] for row in pending
                if row["event_id"] is not None
                and row["id"] != latest_by_event[row["event_id"]]["id"]
            }
            if stale_ids:
                for sid in stale_ids:
                    c.execute("UPDATE notifications SET sent = 1 WHERE id = ?", (sid,))
                    db_log(sid, "system", "SKIPPED_STALE",
                           "Outdated countdown tick superseded by a newer one")
                conn.commit()
                logger.info("Skipped %d stale event tick(s)", len(stale_ids))
                pending = [row for row in pending if row["id"] not in stale_ids]
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `python -m pytest tests -q`
Expected: 26 passed

- [ ] **Step 5: Commit**

```bash
git add notifier/notifications.py tests/test_engine.py
git commit -m "feat(engine): skip stale event ticks after scheduler downtime (SKIPPED_STALE)"
```

---

### Task 5: Web API — accept and return `cadence`

**Files:**
- Modify: `web/main.py` (`api_create_event` ~line 774, `api_update_event` ~line 809; import line ~63/73)

No web test harness exists; engine behavior is covered by Task 1's round-trip test. Verification is the manual check in Task 7.

- [ ] **Step 1: Implement**

(a) In **both** `web/main.py` import blocks that pull from `notifier.db` (there are two — ~line 63 and ~line 73), add `CADENCES` to the imported names.

(b) `api_create_event`: add a form param after `send_time`:

```python
    cadence: str = Form(""),
```

validate it after the date check:

```python
    if cadence and cadence not in CADENCES:
        return JSONResponse({"error": "Invalid cadence"}, status_code=400)
```

and pass it through to `create_event`:

```python
            cadence=(cadence or None),
```

(c) `api_update_event`: same three additions — `cadence: str = Form("")` param, the same validation block, and `cadence=(cadence or None)` in the `update_event(...)` call.

- [ ] **Step 2: Sanity-run the engine suite (web imports notifier.db at module load)**

Run: `python -c "import web.main" && python -m pytest tests -q`
Expected: import succeeds, 26 passed

- [ ] **Step 3: Commit**

```bash
git add web/main.py
git commit -m "feat(web): /api/events accepts cadence (milestones|daily)"
```

---

### Task 6: Web UI — cadence radio in the event modal

**Files:**
- Modify: `web/templates/dashboard.html` (modal form ~line 552-604; JS `showAddEventModal`/`editEvent` ~line 716-754)

- [ ] **Step 1: Add the radio + wrap the milestones field**

In the event form, directly **above** the existing Milestones `<div>` (~line 587), insert:

```html
                <div>
                    <label class="block text-sm text-zinc-400 mb-1.5">Cadence</label>
                    <div class="flex gap-5 text-sm">
                        <label class="inline-flex items-center gap-2">
                            <input type="radio" name="cadence" value="milestones" checked
                                   onchange="onCadenceChange()" class="accent-sky-600 w-4 h-4">
                            Milestones
                        </label>
                        <label class="inline-flex items-center gap-2">
                            <input type="radio" name="cadence" value="daily"
                                   onchange="onCadenceChange()" class="accent-sky-600 w-4 h-4">
                            Every day 📆
                        </label>
                    </div>
                </div>
```

Then add `id="milestones-field"` to the existing Milestones wrapper div:

```html
                <div id="milestones-field">
                    <label class="block text-sm text-zinc-400 mb-1.5">Milestones <span class="text-zinc-600">(days before, comma-separated)</span></label>
```

- [ ] **Step 2: Wire the JS**

In the `// === Countdown Events ===` script section, add:

```javascript
        function setCadence(value) {
            const radio = document.querySelector(`input[name="cadence"][value="${value}"]`);
            if (radio) radio.checked = true;
            onCadenceChange();
        }

        function onCadenceChange() {
            const daily = document.querySelector('input[name="cadence"]:checked')?.value === 'daily';
            document.getElementById('milestones-field').classList.toggle('hidden', daily);
        }
```

In `showAddEventModal()`, after the `event-time` reset line, add:

```javascript
            setCadence('milestones');
```

In `editEvent(id)`, after the `event-milestones` prefill line, add:

```javascript
            setCadence(ev.cadence || 'milestones');
```

(`submitEvent` needs no change — the checked radio rides along in `FormData` automatically.)

- [ ] **Step 3: Commit**

```bash
git add web/templates/dashboard.html
git commit -m "feat(web): cadence radio in event modal — Every day hides milestones input"
```

---

### Task 7: Manual web verification

**Files:** none (verification only)

- [ ] **Step 1: Run the web app against a scratch DB**

```bash
NOTIFIER_DB_PATH=/tmp/notifier-cadence-test.db NOTIFIER_SKIP_EVENT_SEED=1 \
  python -m uvicorn web.main:app --port 8765
```

(On Windows PowerShell: `$env:NOTIFIER_DB_PATH="$env:TEMP\notifier-cadence-test.db"; $env:NOTIFIER_SKIP_EVENT_SEED="1"; python -m uvicorn web.main:app --port 8765`)

- [ ] **Step 2: Verify in the browser (use the webapp-testing skill if headless)**

1. Log in, open Add Countdown → cadence radio shows, Milestones field visible by default.
2. Select "Every day 📆" → Milestones input hides.
3. Create a cruise event ~10 days out with daily cadence → card appears with ~11 upcoming pings.
4. Edit it → radio prefills to "Every day"; change the date → pending count changes accordingly.
5. Confirm the new favicon shows in the browser tab (already wired in `5dc2d05`).

- [ ] **Step 3: Stop the server, delete the scratch DB**

---

### Task 8: CLI — cadence in Events menu (add, edit, list)

**Files:**
- Modify: `notifier.py` (events list ~line 802; `_add_event_interactive` ~line 823; `_edit_event_interactive` ~line 870; the `from notifier.db import ...` block ~line 80 — add `CADENCE_DAILY`, `CADENCE_MILESTONES`)

- [ ] **Step 1: Implement list display**

In the events list loop (~line 802), replace:

```python
                print(f"     {Fore.WHITE}{Style.DIM}milestones: {ev['milestones']}  "
                      f"at {ev['send_time']}  •  {pending} upcoming ping(s){Style.RESET_ALL}")
```

with:

```python
                schedule = ("every day 📆" if (ev.get("cadence") or "") == CADENCE_DAILY
                            else f"milestones: {ev['milestones']}")
                print(f"     {Fore.WHITE}{Style.DIM}{schedule}  "
                      f"at {ev['send_time']}  •  {pending} upcoming ping(s){Style.RESET_ALL}")
```

- [ ] **Step 2: Implement add flow**

In `_add_event_interactive`, replace the milestones prompt block (the `ms_raw = ...` through `milestones = DEFAULT_MILESTONES` lines) with:

```python
    daily = _prompt("Notify every day until the event? (y/N): ").lower() == "y"
    cadence = CADENCE_DAILY if daily else CADENCE_MILESTONES
    milestones = DEFAULT_MILESTONES
    if not daily:
        ms_raw = input(f"  {Fore.YELLOW}▶  Milestones in days-before (Enter for '{DEFAULT_MILESTONES}'): {Style.RESET_ALL}").strip()
        milestones = ms_raw or DEFAULT_MILESTONES
        if not _parse_milestones(milestones):
            print(f"{Fore.RED}❌ No valid milestones. Using default.{Style.RESET_ALL}")
            milestones = DEFAULT_MILESTONES
```

and pass it through in the `create_event` call:

```python
    event_id = create_event(title, date_raw, category=category, details=details,
                            milestones=milestones, send_time=send_time, cadence=cadence)
```

- [ ] **Step 3: Implement edit flow**

In `_edit_event_interactive`, after the milestones prompt line, add:

```python
    current_cadence = ev.get("cadence") or CADENCE_MILESTONES
    cad_raw = input(f"  {Fore.YELLOW}▶  Cadence (milestones/daily) [{current_cadence}]: {Style.RESET_ALL}").strip().lower()
    cadence = cad_raw if cad_raw in (CADENCE_MILESTONES, CADENCE_DAILY) else current_cadence
```

and extend the `update_event` call:

```python
    ok = update_event(int(eid), title=title, target_date=date_raw, details=details,
                      milestones=ms_raw, send_time=time_raw, cadence=cadence)
```

- [ ] **Step 4: Verify CLI manually + run suites**

```bash
NOTIFIER_DB_PATH=/tmp/cli-test.db NOTIFIER_SKIP_EVENT_SEED=1 python notifier.py --list
python -m pytest tests -q && python test_notifier_smoke.py
```

Expected: `--list` runs clean (proves imports), 26 passed, smoke test green. Then open the interactive Events menu once and add a daily event end-to-end.

- [ ] **Step 5: Commit**

```bash
git add notifier.py
git commit -m "feat(cli): daily cadence in Events menu add/edit/list"
```

---

### Task 9: Version bump to 2.6.0 + changelog

Workflow rule: every push with code changes requires a version bump.

**Files:**
- Modify: `notifier/__init__.py:12` (`__version__`)
- Modify: `VERSION` (repo root)
- Modify: `CHANGELOG.md` (new section at top)
- Modify: `version_manager.py` (`SEED_VERSIONS` list ~line 68 — append a 2.6.0 entry; check `get_current_version()` fallback string and bump it if hardcoded)
- Modify: `README.md` (Version History table — add a 2.6.0 row; update any version badge)

- [ ] **Step 1: Bump the anchors**

`notifier/__init__.py`: `__version__ = "2.6.0"` · `VERSION`: `2.6.0`

- [ ] **Step 2: CHANGELOG.md — add at top (move the `*(Latest)*` marker here)**

```markdown
## [v2.6.0] - 2026-06-11  *(Latest)*

### Added

- Daily countdown cadence for events: a per-event "every day" mode (new `cadence` column, `milestones` remains the default) that sends one countdown notification per day until the event at its send_time, capped at 365 ticks. Daily runs are derived at expansion time, so editing the date or time re-expands every future tick (existing re-expand path). Cruise events now rotate through a 15-template funny emoji message pack (`days_left % 15`, deterministic) with special "tomorrow" and bon-voyage finale messages. The delivery engine skips stale event ticks after scheduler downtime (one current tick delivered, the rest retired with a `SKIPPED_STALE` audit log). Cadence selectable in the web event modal (radio, hides milestones input) and the CLI Events menu. Site favicon (bell + red badge) added to the web UI.
```

- [ ] **Step 3: version_manager.py + README**

Append a `("2.6.0", "<same summary as changelog, one line>")`-shaped entry to `SEED_VERSIONS` matching the existing tuple format in the file (inspect neighbors and copy their shape exactly), bump any hardcoded fallback in `get_current_version()`, and add the 2.6.0 row to README's Version History table.

- [ ] **Step 4: Full verification**

Run: `python -m pytest tests -q && python test_notifier_smoke.py && python notifier.py --version`
Expected: 26 passed, smoke green, version prints 2.6.0

- [ ] **Step 5: Commit**

```bash
git add notifier/__init__.py VERSION CHANGELOG.md version_manager.py README.md
git commit -m "chore: bump to v2.6.0 — daily countdown cadence + cruise message pack"
```

---

### Task 10: Push (requires Kendall's go-ahead)

- [ ] **Step 1: Confirm with Kendall before pushing** — local `main` also carries the earlier spec/favicon commits (`a5e5b19`, `4cd76de`, `5dc2d05`).
- [ ] **Step 2: `git push origin main`**
- [ ] **Step 3: Deploy** — the site runs Dockerized at notifier.minus-one-labs.com; rebuild/redeploy per the project's deployment method so the new cadence UI and favicon go live.
