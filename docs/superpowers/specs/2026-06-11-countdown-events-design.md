# Daily Countdown Cadence for Events — Design Spec

**Date:** 2026-06-11 (rev 2 — rewritten against the real v2.5.0 architecture)
**Project:** Notifier
**Status:** Approved design, pending implementation plan

> Rev 1 of this spec assumed the pre-v2.4.0 single-file architecture and
> designed countdown events from scratch. Countdown events already shipped in
> v2.5.0 (`eca5c19`) as **milestone-based** notifications. This revision keeps
> the agreed user-facing behavior (daily ticker, funny cruise messages) and
> re-bases the design onto the existing events engine.

## Summary

The v2.5.0 events engine sends countdown pings only at fixed milestones
(default `60,30,14,7,3,1,0` days before the target). This adds a second
**cadence**: *daily* — one notification every day until the event, at the
event's `send_time`. It also upgrades the cruise flavour from two stock
suffixes to a rotating pack of 15 funny emoji-art messages.

## Existing architecture (what we build on)

- `events` table (`notifier/db.py`): `title`, `target_date`, `target_ts`,
  `category` (e.g. `cruise`), `details`, `milestones` (CSV of day-offsets),
  `send_time` (HH:MM), `active`.
- `expand_event(event_id)` materialises one row in `notifications` per
  **future** milestone, linked via `notifications.event_id`. The existing
  scheduler delivers them — there is no second delivery path.
- **Edit re-expands**: updating an event deletes its pending milestone rows
  and regenerates them. This is what already guarantees "change the time/date
  once → every future tick follows".
- `format_event_message()` renders the per-milestone text; `category ==
  'cruise'` adds a nautical suffix.
- Web: `/api/events` CRUD + dashboard countdown cards + add/edit modal.
  CLI: Events/Countdowns menu.

## Requirements (as agreed with Kendall)

1. **Daily countdown ticker** — opt-in per event; one notification per day
   until the event date, at the event's `send_time`.
2. **Event day** — final "🎉 today!" message, then the event completes
   naturally (no pending rows left; stays in history).
3. **Edit propagates** — changing `send_time`/`target_date` updates all
   future ticks. (Already provided by re-expansion; daily cadence must go
   through the same path.)
4. **Auto messages, cruise pack** — 15 funny cruise-themed emoji-art messages
   rotating day to day (no real images; senders stay text-only). Non-cruise
   events keep the neutral calendar style.

## Design

### 1. `cadence` column

Add `cadence TEXT NOT NULL DEFAULT 'milestones'` to `events`
(values: `milestones` | `daily`), via the existing column-migration pattern.

### 2. Expansion

In `expand_event()`, compute the milestone set per cadence:

- `milestones` → `_parse_milestones(event.milestones)` (unchanged).
- `daily` → `range(days_left, -1, -1)` computed **at expansion time**, capped
  at 365 offsets (sanity guard for far-future events). The stored
  `milestones` CSV is ignored for daily events.

Because daily events derive their set at expansion time, the existing
edit-re-expand flow automatically produces the correct new daily run when the
date or time changes — requirement 3 falls out with no extra code.

### 3. Message pack

In `notifier/db.py` next to `format_event_message()`:

- `CRUISE_PACK`: exactly 15 emoji-art templates with `{days}`, `{title}`,
  `{date}` placeholders, e.g.
  `"🚢💨 {days} days until {title}! The ship is warming up its horn... ⚓😤"`.
- Selection: `CRUISE_PACK[days_left % 15]` for `days_left > 1` — deterministic
  (same day → same message; consecutive days never repeat).
- `days_left == 1` → special "tomorrow" cruise message;
  `days_left == 0` → finale (`"🎉🚢 TODAY'S THE DAY — {title}! Bon voyage! 🍹🌊"`).
- Applies to `category == 'cruise'` regardless of cadence (milestone cruise
  events get the pack too). Non-cruise events keep the current neutral
  format. `details` continues to append on its own line.

### 4. Stale-tick skip (scheduler downtime)

If the scheduler is down for N days, a daily event has N overdue rows. On
delivery, for event-linked notifications (`event_id IS NOT NULL`) that are
due, send only the **most current** row per event (lowest `days_left`, i.e.
latest `due_ts`); mark older overdue siblings `sent=1` with a
`SKIPPED_STALE` log entry instead of delivering them. This also fixes the
same (milder) backlog behavior for milestone events.

### 5. Web UI

Add/edit event modal: a cadence radio —
**Milestones** (default; shows the existing milestones CSV input) /
**Every day** (hides the CSV input). `/api/events` create/update accept and
return `cadence` (validated to the two values). Dashboard cards unchanged
(days_left display already cadence-agnostic).

### 6. CLI

Events/Countdowns menu add + edit flows gain the same cadence choice
(`1` Milestones / `2` Every day), defaulting to milestones.

### 7. Export / Import & seed

Event export/import (where applicable) carries `cadence`; invalid values fall
back to `milestones`. The cruise-notifier seed events stay milestone-based.

## Error handling

- Unknown `cadence` value in DB → treat as `milestones`.
- Daily event created with target today → expansion yields just the day-0
  finale row.
- Past target date → existing validation/expansion behavior (no future rows).

## Testing (extend `tests/` engine suite)

- Daily expansion: target 10 days out → 11 rows (10..0), all future-dated at
  `send_time`.
- 365-day cap honored for far-future daily events.
- Edit re-expand: change `target_date`/`send_time` on a daily event → pending
  rows regenerated with the new schedule.
- Rotation: `days_left % 15` deterministic; days 17 and 2 share a template,
  adjacent days differ; day 1 and day 0 specials.
- Stale-skip: 3 overdue daily rows → exactly 1 delivered, 2 logged
  `SKIPPED_STALE`.
- Cadence round-trip through `/api/events` create → get.

## Out of scope

- Images/photos in notifications (senders stay text-only)
- Count-up after the event (anniversaries)
- Additional themed packs beyond cruise (easy to add later)
- Changing the default cadence (milestones remains the default)
