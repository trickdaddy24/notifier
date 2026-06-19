"""
notifier/holidays.py — 2026 US holidays: list and batch-add as countdown events.

Holidays are added as countdown *events* (not raw notifications), so the
standard milestone scheduler delivers reminders at 30, 14, 7, 3, 1, and 0
days before each date. Re-running is safe — existing titles are skipped.

Non-interactive CLI (run from repo root):
    python notifier.py --holidays                       # interactive menu
    python notifier.py --holidays list                  # print table with status
    python notifier.py --holidays add-all               # add all upcoming holidays
    python notifier.py --holidays add --holiday-name "Thanksgiving"
    python notifier.py --holidays add --holiday-name "christmas"  # fuzzy match OK

Interactive menu:
    From the main Notifier menu → option 13 "🎉 Holidays"
"""

from __future__ import annotations

import os
from typing import NamedTuple, Optional

from colorama import Fore, Style

# All database interaction goes through the shared db layer.
from notifier.db import (
    create_event,
    list_events,
    days_until,
    DEFAULT_MILESTONES,
    DEFAULT_EVENT_SEND_TIME,
)


# ---------------------------------------------------------------------------
# 2026 Holiday Data
# ---------------------------------------------------------------------------

class Holiday(NamedTuple):
    """One holiday entry: name, ISO date, category, and optional detail note."""
    name: str
    date: str         # YYYY-MM-DD
    category: str     # 'federal' | 'observance'
    details: Optional[str]


# Complete list of 2026 US Federal Holidays + widely-observed dates.
# Federal dates follow the OPM (Office of Personnel Management) schedule.
# When a federal holiday falls on Saturday, it is observed the prior Friday;
# on Sunday, the following Monday.  Both the actual and observed dates are
# noted in `details` where relevant.
HOLIDAYS_2026: list[Holiday] = [

    # ── Federal Holidays (OPM 2026) ──────────────────────────────────────────
    Holiday("New Year's Day",    "2026-01-01", "federal",
            "Thursday — federal holiday"),

    Holiday("MLK Day",           "2026-01-19", "federal",
            "Martin Luther King Jr. Day — 3rd Monday in January"),

    Holiday("Presidents Day",    "2026-02-16", "federal",
            "Washington's Birthday — 3rd Monday in February"),

    Holiday("Memorial Day",      "2026-05-25", "federal",
            "Last Monday in May"),

    Holiday("Juneteenth",        "2026-06-19", "federal",
            "National Independence Day — Friday"),

    Holiday("Independence Day",  "2026-07-04", "federal",
            "Saturday — observed Friday July 3 for federal employees"),

    Holiday("Labor Day",         "2026-09-07", "federal",
            "First Monday in September"),

    Holiday("Columbus Day",      "2026-10-12", "federal",
            "Second Monday in October"),

    Holiday("Veterans Day",      "2026-11-11", "federal",
            "Wednesday — federal holiday"),

    Holiday("Thanksgiving",      "2026-11-26", "federal",
            "Fourth Thursday in November"),

    Holiday("Christmas Day",     "2026-12-25", "federal",
            "Friday — federal holiday"),

    # ── Common Observances (not federal but widely celebrated) ───────────────
    Holiday("Valentine's Day",   "2026-02-14", "observance", None),

    Holiday("St. Patrick's Day", "2026-03-17", "observance", None),

    Holiday("Easter",            "2026-04-05", "observance",
            "Western/Gregorian Easter — April 5, 2026"),

    Holiday("Mother's Day",      "2026-05-10", "observance",
            "Second Sunday in May"),

    Holiday("Father's Day",      "2026-06-21", "observance",
            "Third Sunday in June"),

    Holiday("Halloween",         "2026-10-31", "observance", None),

    Holiday("Christmas Eve",     "2026-12-24", "observance", None),

    Holiday("New Year's Eve",    "2026-12-31", "observance", None),
]

# Quick access by category.
FEDERAL_HOLIDAYS    = [h for h in HOLIDAYS_2026 if h.category == "federal"]
OBSERVANCE_HOLIDAYS = [h for h in HOLIDAYS_2026 if h.category == "observance"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _existing_titles() -> set[str]:
    """Lower-cased titles of all events already in the database."""
    return {ev["title"].lower() for ev in list_events()}


def _find_holiday(query: str) -> Optional[Holiday]:
    """
    Case-insensitive partial match against holiday names.

    "christmas"  → Christmas Day
    "thanks"     → Thanksgiving
    "mlk"        → MLK Day

    Returns the first hit, or None if nothing matches.
    """
    q = query.strip().lower()
    for h in HOLIDAYS_2026:
        if q in h.name.lower():
            return h
    return None


def _days_label(days: Optional[int]) -> str:
    """Short human label: '32 days', 'today', 'yesterday', 'X days ago'."""
    if days is None:
        return "??"
    if days > 1:
        return f"in {days} days"
    if days == 1:
        return "tomorrow"
    if days == 0:
        return "TODAY"
    return f"{abs(days)} days ago"


def _add_one(h: Holiday, send_time: str = DEFAULT_EVENT_SEND_TIME,
             milestones: str = DEFAULT_MILESTONES) -> bool:
    """
    Add a single Holiday as a countdown event.

    Delegates entirely to create_event() — holiday messages are formatted
    by the neutral (non-cruise) path in format_event_message().
    Returns True if the DB insert succeeded.
    """
    event_id = create_event(
        title=h.name,
        target_date=h.date,
        category=None,        # not a cruise — uses the plain "📅 N days until…" template
        details=h.details,
        milestones=milestones,
        send_time=send_time,
    )
    return event_id is not None


# ---------------------------------------------------------------------------
# Minimal UI helpers (colorama — matches notifier.py style without importing it)
# ---------------------------------------------------------------------------

def _hbox(title: str) -> None:
    """Print a colored section header box."""
    c = Fore.YELLOW + Style.BRIGHT
    print(f"\n{c}╔═══════════════════════════════════════╗{Style.RESET_ALL}")
    print(f"{c}║  {title:<37}║{Style.RESET_ALL}")
    print(f"{c}╚═══════════════════════════════════════╝{Style.RESET_ALL}")


def _hdiv() -> None:
    print(f"  {Fore.WHITE}{Style.DIM}{'─' * 39}{Style.RESET_ALL}")


def _hopt(num: str, color: str, emoji: str, label: str) -> None:
    print(f"  {Fore.YELLOW}{Style.BRIGHT}{num}{Style.RESET_ALL}  {color}{emoji}  {label}{Style.RESET_ALL}")


def _hprompt(text: str = "Choose: ") -> str:
    return input(f"\n  {Fore.GREEN}{Style.BRIGHT}▶  {text}{Style.RESET_ALL}").strip()


# ---------------------------------------------------------------------------
# Public display + action functions
# ---------------------------------------------------------------------------

def list_holidays_cli(filter_upcoming: bool = False) -> None:
    """
    Print the full holiday table with status indicators.

    Columns: #  Name  Date  Days  Category  Status
    Status key:  ✅ already added  ·  📅 upcoming  ·  ✓ past (skipped)
    """
    existing = _existing_titles()

    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'═' * 55}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}🎉  2026 HOLIDAYS{Style.RESET_ALL}"
          + (f"  {Fore.WHITE}{Style.DIM}(upcoming only){Style.RESET_ALL}" if filter_upcoming else ""))
    print(f"  {Fore.CYAN}{Style.BRIGHT}{'═' * 55}{Style.RESET_ALL}\n")

    # Federal block
    print(f"  {Fore.YELLOW}{Style.BRIGHT}── Federal Holidays ──────────────────────────{Style.RESET_ALL}")
    _print_holiday_block(FEDERAL_HOLIDAYS, existing, filter_upcoming)

    # Observances block
    print(f"\n  {Fore.YELLOW}{Style.BRIGHT}── Common Observances ────────────────────────{Style.RESET_ALL}")
    _print_holiday_block(OBSERVANCE_HOLIDAYS, existing, filter_upcoming)

    print(f"\n  {Fore.CYAN}{Style.BRIGHT}{'═' * 55}{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}{Style.DIM}✅ = already in your events  "
          f"⏳ = upcoming  ✓ = past{Style.RESET_ALL}\n")


def _print_holiday_block(holidays: list[Holiday], existing: set[str],
                         filter_upcoming: bool) -> None:
    """Print one group (federal or observance) of holidays as a table."""
    for i, h in enumerate(holidays, start=1):
        days = days_until(h.date)
        in_db = h.name.lower() in existing
        past = days is not None and days < 0

        if filter_upcoming and (past or in_db):
            continue

        # Status badge
        if in_db:
            badge = f"{Fore.GREEN}✅ added{Style.RESET_ALL}"
        elif past:
            badge = f"{Fore.WHITE}{Style.DIM}✓ past{Style.RESET_ALL}"
        else:
            badge = f"{Fore.CYAN}⏳ upcoming{Style.RESET_ALL}"

        # Days label (colored by urgency)
        dl = _days_label(days)
        if days is not None and 0 <= days <= 7:
            dl_colored = f"{Fore.RED}{Style.BRIGHT}{dl}{Style.RESET_ALL}"
        elif days is not None and 0 <= days <= 30:
            dl_colored = f"{Fore.YELLOW}{dl}{Style.RESET_ALL}"
        elif days is not None and days > 0:
            dl_colored = f"{Fore.WHITE}{dl}{Style.RESET_ALL}"
        else:
            dl_colored = f"{Fore.WHITE}{Style.DIM}{dl}{Style.RESET_ALL}"

        name_color = Fore.WHITE + Style.DIM if past else Fore.WHITE + Style.BRIGHT
        print(f"  {Fore.WHITE}{Style.DIM}{h.date}{Style.RESET_ALL}  "
              f"{name_color}{h.name:<22}{Style.RESET_ALL}  "
              f"{dl_colored:<28}  {badge}")


def add_holiday_cli(name: str, send_time: str = DEFAULT_EVENT_SEND_TIME,
                    milestones: str = DEFAULT_MILESTONES) -> bool:
    """
    Add one holiday (fuzzy match on name) as a countdown event.

    Safe to call if the holiday already exists — it will print a notice
    and return True without creating a duplicate.
    """
    h = _find_holiday(name)
    if h is None:
        print(f"{Fore.RED}❌ No holiday matching '{name}'. "
              f"Run `--holidays list` to see names.{Style.RESET_ALL}")
        return False

    existing = _existing_titles()
    if h.name.lower() in existing:
        days = days_until(h.date)
        print(f"{Fore.YELLOW}⚠️  '{h.name}' ({h.date}) is already in your events "
              f"[{_days_label(days)}] — skipped.{Style.RESET_ALL}")
        return True

    ok = _add_one(h, send_time=send_time, milestones=milestones)
    if ok:
        days = days_until(h.date)
        if days is not None and days < 0:
            print(f"{Fore.YELLOW}✅ Added '{h.name}' ({h.date}) — "
                  f"{Fore.WHITE}{Style.DIM}already passed; no upcoming milestones.{Style.RESET_ALL}")
        else:
            print(f"{Fore.GREEN}✅ Added '{h.name}' on {h.date} "
                  f"[{_days_label(days)}]{Style.RESET_ALL}")
    else:
        print(f"{Fore.RED}❌ Failed to add '{h.name}'.{Style.RESET_ALL}")
    return ok


def add_all_cli(skip_existing: bool = True, skip_past: bool = False,
                send_time: str = DEFAULT_EVENT_SEND_TIME,
                milestones: str = DEFAULT_MILESTONES) -> tuple[int, int, int]:
    """
    Add every 2026 holiday as a countdown event.

    Args:
        skip_existing: Don't re-add holidays already in the DB (default True).
        skip_past:     Don't add holidays whose date has already passed (default False).
        send_time:     HH:MM daily time for milestone notifications.
        milestones:    Comma-separated day-offsets (e.g. '30,14,7,1,0').

    Returns:
        (added, skipped_existing, skipped_past) counts.
    """
    existing = _existing_titles()
    added = skipped_exist = skipped_past = 0

    for h in HOLIDAYS_2026:
        days = days_until(h.date)
        past = days is not None and days < 0

        if skip_existing and h.name.lower() in existing:
            skipped_exist += 1
            continue
        if skip_past and past:
            skipped_past += 1
            continue

        ok = _add_one(h, send_time=send_time, milestones=milestones)
        if ok:
            label = f"{Fore.WHITE}{Style.DIM}(past — no milestones){Style.RESET_ALL}" if past else \
                    f"{Fore.WHITE}{Style.DIM}[{_days_label(days)}]{Style.RESET_ALL}"
            print(f"  {Fore.GREEN}✅{Style.RESET_ALL} {h.name:<22} {h.date}  {label}")
            added += 1
        else:
            print(f"  {Fore.RED}❌{Style.RESET_ALL} {h.name} — insert failed")

    return added, skipped_exist, skipped_past


# ---------------------------------------------------------------------------
# Interactive menu (option 13 in main menu)
# ---------------------------------------------------------------------------

def holidays_menu() -> None:
    """
    Interactive holidays sub-menu.

    Presents the full holiday list with status badges, then lets the user
    add all upcoming holidays at once, pick individual ones by number,
    or just browse the table.
    """
    while True:
        existing = _existing_titles()
        all_days = [(h, days_until(h.date)) for h in HOLIDAYS_2026]

        # Partition for the menu summary line
        n_upcoming  = sum(1 for h, d in all_days if d is not None and d >= 0
                          and h.name.lower() not in existing)
        n_added     = sum(1 for h, _ in all_days if h.name.lower() in existing)

        _hbox("🎉 2026 HOLIDAYS")
        print(f"  {Fore.WHITE}{Style.DIM}{n_added} already added  •  "
              f"{n_upcoming} upcoming & not yet added{Style.RESET_ALL}\n")

        # Print table
        print(f"  {Fore.YELLOW}{Style.BRIGHT}── Federal ───────────────────────────────────{Style.RESET_ALL}")
        _print_numbered_block(FEDERAL_HOLIDAYS, existing, start=1)

        obs_start = len(FEDERAL_HOLIDAYS) + 1
        print(f"\n  {Fore.YELLOW}{Style.BRIGHT}── Observances ───────────────────────────────{Style.RESET_ALL}")
        _print_numbered_block(OBSERVANCE_HOLIDAYS, existing, start=obs_start)

        _hdiv()
        _hopt("A", Fore.GREEN + Style.BRIGHT, "📅", f"Add all upcoming ({n_upcoming} holidays)")
        _hopt("P", Fore.CYAN,                 "📜", "Add all (including past-dated)")
        _hopt("N", Fore.BLUE + Style.BRIGHT,  "➕", "Add one by number")
        _hopt("0", Fore.RED + Style.DIM,      "⬅️ ", "Back to Main Menu")
        _hdiv()

        choice = _hprompt("Choose: ").upper()

        if choice == "0":
            break

        elif choice == "A":
            print()
            added, skipped_exist, skipped_past = add_all_cli(
                skip_existing=True, skip_past=True
            )
            _print_batch_summary(added, skipped_exist, skipped_past)
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")

        elif choice == "P":
            print()
            added, skipped_exist, skipped_past = add_all_cli(
                skip_existing=True, skip_past=False
            )
            _print_batch_summary(added, skipped_exist, skipped_past)
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")

        elif choice == "N":
            raw = _hprompt(f"Holiday number (1–{len(HOLIDAYS_2026)}): ")
            if not raw.isdigit() or not (1 <= int(raw) <= len(HOLIDAYS_2026)):
                print(f"{Fore.RED}❌ Invalid number.{Style.RESET_ALL}")
            else:
                h = HOLIDAYS_2026[int(raw) - 1]
                add_holiday_cli(h.name)
            input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")

        else:
            # Try interpreting the input as a number directly
            if choice.isdigit() and 1 <= int(choice) <= len(HOLIDAYS_2026):
                h = HOLIDAYS_2026[int(choice) - 1]
                add_holiday_cli(h.name)
                input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}❌ Invalid choice.{Style.RESET_ALL}")


def _print_numbered_block(holidays: list[Holiday], existing: set[str],
                          start: int = 1) -> None:
    """Print a numbered list of holidays for the interactive menu."""
    for i, h in enumerate(holidays, start=start):
        days = days_until(h.date)
        in_db = h.name.lower() in existing
        past  = days is not None and days < 0

        if in_db:
            status = f"{Fore.GREEN}✅{Style.RESET_ALL}"
        elif past:
            status = f"{Fore.WHITE}{Style.DIM}✓{Style.RESET_ALL} "
        else:
            status = f"{Fore.CYAN}⏳{Style.RESET_ALL}"

        dl = _days_label(days)
        if days is not None and 0 <= days <= 7:
            dl_colored = f"{Fore.RED}{Style.BRIGHT}{dl}{Style.RESET_ALL}"
        elif days is not None and 0 <= days <= 30:
            dl_colored = f"{Fore.YELLOW}{dl}{Style.RESET_ALL}"
        else:
            dl_colored = f"{Fore.WHITE}{Style.DIM}{dl}{Style.RESET_ALL}"

        name_dim = Style.DIM if (past and not in_db) else ""
        print(f"  {Fore.YELLOW}{i:>2}{Style.RESET_ALL}  {status}  "
              f"{Fore.WHITE}{name_dim}{h.name:<22}{Style.RESET_ALL}  "
              f"{Fore.WHITE}{Style.DIM}{h.date}{Style.RESET_ALL}  {dl_colored}")


def _print_batch_summary(added: int, skipped_exist: int, skipped_past: int) -> None:
    """Print a summary line after a batch-add operation."""
    print(f"\n  {Fore.GREEN}{Style.BRIGHT}✅ {added} added{Style.RESET_ALL}", end="")
    if skipped_exist:
        print(f"  {Fore.WHITE}{Style.DIM}• {skipped_exist} already existed (skipped){Style.RESET_ALL}", end="")
    if skipped_past:
        print(f"  {Fore.WHITE}{Style.DIM}• {skipped_past} past-dated (skipped){Style.RESET_ALL}", end="")
    print()


# ---------------------------------------------------------------------------
# CLI dispatch (called from notifier.py cli())
# ---------------------------------------------------------------------------

def run_holidays_cli(action: str, name: Optional[str] = None,
                     send_time: str = DEFAULT_EVENT_SEND_TIME,
                     milestones: str = DEFAULT_MILESTONES) -> None:
    """
    Entry point called from the notifier.py `cli()` function.

    Actions:
        menu      — interactive menu (default when --holidays used with no action)
        list      — print full table and exit
        add-all   — add all upcoming holidays (skip existing + past)
        add-past  — add all holidays including past-dated ones
        add       — add one holiday by name (requires --holiday-name)
    """
    # Initialise DB before any query
    from notifier.db import init_db
    init_db(backfill_legacy=False)

    action = (action or "menu").strip().lower()

    if action == "menu":
        holidays_menu()

    elif action == "list":
        list_holidays_cli()

    elif action == "add-all":
        print(f"\n{Fore.CYAN}{Style.BRIGHT}📅 Adding upcoming 2026 holidays...{Style.RESET_ALL}\n")
        added, sk_ex, sk_past = add_all_cli(skip_existing=True, skip_past=True,
                                             send_time=send_time, milestones=milestones)
        _print_batch_summary(added, sk_ex, sk_past)

    elif action == "add-past":
        print(f"\n{Fore.CYAN}{Style.BRIGHT}📅 Adding all 2026 holidays (incl. past)...{Style.RESET_ALL}\n")
        added, sk_ex, sk_past = add_all_cli(skip_existing=True, skip_past=False,
                                             send_time=send_time, milestones=milestones)
        _print_batch_summary(added, sk_ex, sk_past)

    elif action == "add":
        if not name:
            print(f"{Fore.RED}❌ --holidays add requires --holiday-name NAME.{Style.RESET_ALL}")
            print(f"   Example: python notifier.py --holidays add "
                  f"--holiday-name \"Thanksgiving\"")
            return
        add_holiday_cli(name, send_time=send_time, milestones=milestones)

    else:
        print(f"{Fore.RED}❌ Unknown action '{action}'. "
              f"Use: list | add-all | add-past | add{Style.RESET_ALL}")
        print(f"   Run `python notifier.py --holidays list` to see available holidays.")
