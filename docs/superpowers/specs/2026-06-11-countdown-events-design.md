# Countdown Events — Design Spec

**Date:** 2026-06-11
**Project:** Notifier
**Status:** Approved design, pending implementation plan

## Summary

Add a new notification type to Notifier: a **countdown event**. The user names an
event with a date (e.g., "Cruise — 2026-08-15") and Notifier sends one notification
per day counting down to it ("⏳ 12 days until Cruise"), at a per-event daily time.
On the event day it sends a finale message and auto-completes. The cruise theme
rotates through 15 funny emoji-art messages so consecutive days never repeat.

## Requirements (as agreed)

1. **Daily countdown ticker** — one notification per day until the event date.
2. **Event day** — send a "🎉 Today!" finale, then mark the event complete.
   It stays visible in history; no further notifications.
3. **Per-event daily time** — each event has its own send time (HH:MM).
   Editing the time on the event changes **all future ticks**, because the
   event is a single row (not pre-generated per-day rows).
4. **Auto-generated messages** — the user only names the event. Messages come
   from a theme pack:
   - **Cruise** theme: 15 funny cruise messages with emoji art (no images —
     emoji/ASCII only, so the existing text-only senders are untouched).
   - **Generic** theme: plain "⏳ {days} days until {name} ({date})".
5. **No pictures** — explicitly out of scope. All senders remain text-only.

## Data model

Extend the existing `notifications` table via the `_ensure_columns` migration
list already in `init_db()` (same pattern used for `recurrence`/`repeat_time`):

| Column | Type | Meaning |
|---|---|---|
| `event_date` | `TEXT` | Event day, `YYYY-MM-DD` (user TZ) |
| `theme` | `TEXT` | `cruise` or `generic` |

A countdown event is **one row**:

- `recurrence = 'countdown'` (new recurrence value)
- `message` = event name (e.g., "Cruise")
- `repeat_time` = daily tick time, `HH:MM`
- `due_ts` / `due_time` = the **next** tick (kept current as ticks fire)
- `sent = 1` only after the finale fires (event complete)

## Creation UX

In `add_notification()`, the Repeat-type menu gains option **5 — 🎉 Countdown
to event**. Prompt sequence:

1. Event name (the `message` field; reuses existing non-empty/length checks)
2. Event date — `YYYY-MM-DD`, must be strictly in the future (user TZ)
3. Daily time — `HH:MM`, Enter defaults to `09:00`
4. Theme — `1` 🚢 Cruise / `2` ⏳ Generic

First tick = next occurrence of the daily time (reuse `_next_daily_time()`).
Confirmation line shows: event name, date, days remaining, daily time, theme.

## Send behavior (`send_notifications`)

Countdown rows differ from existing recurrences in one key way: existing
recurrences **insert a new row** per occurrence; countdown rows **update in
place**. This is what makes requirement 3 work — one row per event means a
single-field edit applies to every future tick.

Per tick, when a countdown row is due:

1. Compute `days_remaining = event_date − today` in the user TZ **at send
   time** (always accurate even if the machine slept or the scheduler ran late).
2. `days_remaining > 0` → send the themed countdown message, then advance the
   same row's `due_ts`/`due_time` to tomorrow at `repeat_time` (`sent` stays 0).
3. `days_remaining == 0` → send the finale message, set `sent = 1`. Done.
4. `days_remaining < 0` (scheduler was down past the event) → send the finale
   once, set `sent = 1`. Never spam a backlog of missed days: a late scheduler
   delivers **one** tick with the current count, then resumes the daily cadence.

Delivery uses the existing `_deliver()` fan-out (Telegram, Discord, Pushover,
Gmail) and `db_log()` audit logging, unchanged.

## Message packs

In-file constants in `notifier.py` (no new files/modules):

- `CRUISE_PACK`: list of exactly 15 strings, each with emoji art, using
  `{days}`, `{name}`, `{date}` placeholders. Example:
  `"🚢💨 {days} days until {name}! The ship is warming up its horn... ⚓😤"`
- `GENERIC_PACK`: single template `"⏳ {days} days until {name} ({date})"`.
- `FINALE`: per-theme finale, e.g.
  `"🎉🚢 TODAY'S THE DAY — {name} is HERE! Bon voyage! 🍹🌊"` and a generic
  `"🎉 Today: {name}!"`.

Selection is deterministic: `CRUISE_PACK[days_remaining % 15]`. Consecutive
days never repeat, and re-running the sender on the same day picks the same
message. `{date}` renders as a friendly short date (e.g., "Aug 15").

## View / Edit / Snooze

- **View** (`view_notifications`): countdown rows render as
  `🚢 Countdown: 12 days until Cruise (2026-08-15) · daily at 09:00`
  (🚢 for cruise theme, ⏳ for generic). Completed events show the existing
  sent/✅ status.
- **Edit** (`edit_notification`): countdown branch lets the user change event
  name, event date (future-validated), and daily time. Changing the daily time
  also recomputes the next `due_ts`. Single-row update → all future ticks follow.
- **Snooze**: existing behavior shifts only the next tick (`due_ts`); the daily
  cadence resumes from the following day because the post-send advance always
  targets tomorrow at `repeat_time`.

## Export / Import

- Export JSON gains `event_date` and `theme` per row.
- Import validates countdown rows: `recurrence == 'countdown'` requires a
  parseable future `event_date` and a theme in `('cruise', 'generic')`;
  invalid rows are skipped with the existing warning pattern.

## Error handling

- Past event date at creation/edit → reject with the existing red-error style.
- Invalid date/time formats → existing validation messages.
- `theme` NULL on a countdown row (hand-edited DB) → fall back to generic pack.
- Delivery failures → existing retry/transient handling in `_deliver()`; the
  row only advances/completes when at least one channel succeeds (same rule
  as current sends).

## Testing (`test_notifier_smoke.py`)

- Create a countdown event → row has correct `recurrence`, `event_date`,
  `theme`, first `due_ts`.
- Days-remaining math at TZ boundaries (date math in user TZ, not UTC).
- Deterministic rotation: same day → same message; adjacent days differ.
- In-place reschedule: after a tick, same row id, `due_ts` advanced one day.
- Finale: `days_remaining == 0` → finale sent, `sent = 1`, no reschedule.
- Late scheduler: multiple missed days → exactly one send.
- Edit propagation: change `repeat_time` → next `due_ts` reflects it.
- Import validation: bad date / bad theme rows skipped.

## Out of scope

- Images/photos in notifications (senders stay text-only)
- Count-up after the event (anniversaries)
- Additional theme packs beyond cruise + generic (easy to add later)
- GUI (Tkinter) support for creating countdown events — view-only is fine
