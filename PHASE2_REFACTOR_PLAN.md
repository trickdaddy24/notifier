# Phase 2 Refactor + Web GUI + Docker Plan for "notifier"

**Date**: 2026-05 (planning session)
**Goal**: Execute the high-impact Phase 2 items (proper packaging + breaking the monolith) while adding a modern web GUI that runs cleanly in Docker. Preserve existing CLI behavior and user data as much as possible.

---

## Current State Assessment (from deep exploration)

**Strengths (leverage these)**
- Excellent `CHANNELS` registry (data-driven send/verify/credential UI) at `notifier.py:486`
- High-quality pure helpers already covered by `test_notifier_smoke.py` (`_to_ts`, `_parse_due_time`, recurrence math, `_deliver` + retry logic)
- Solid DB layer with WAL, indexes, and migrations
- `--daemon`, headless CLI, and `run_send_now` paths already exist
- Careful cross-platform and reliability engineering throughout

**Critical Liabilities for Web + Docker + Packaging**
- 1,679-line monolith (`notifier.py`) + 428-line custom `version_manager.py`
- Heavy reliance on module globals + `os.environ` for configuration (fatal for web servers)
- `schedule` library + ad-hoc daemon thread (not suitable for long-running web processes)
- Two SQLite DBs with inconsistent connection patterns
- Import-time side effects (`load_dotenv`, logging setup, Tkinter probe)
- No `pyproject.toml`, no package structure, no proper entry points
- `.env.example` is referenced everywhere but does not exist
- Self-update mechanism (`do_update`) assumes a git clone layout

---

## Recommended Architecture (Chosen After Evaluation)

### 1. Web Framework: FastAPI + HTMX (or minimal Jinja2 + Tailwind)
- Best Docker story, async-ready, excellent Pydantic integration for config.
- Lightweight frontend possible without a heavy JS framework.
- Alternatives considered and rejected: Streamlit/NiceGUI (poor scheduler fit), Flask (less future-proof), Django (overkill).

### 2. Scheduler Strategy
- **Introduce APScheduler** with `SQLiteJobStore` (or in-memory for simplicity initially).
- The web process (Uvicorn) becomes the primary long-running process.
- Old `schedule` + thread approach kept temporarily for pure `--daemon` CLI compatibility, then deprecated.
- Rationale: APScheduler understands persistent jobs, integrates cleanly with FastAPI lifespan, and is the standard in this space.

### 3. Package Structure (Pragmatic Layered Core)
```
notifier/
├── pyproject.toml
├── src/
│   └── notifier/
│       ├── __init__.py
│       ├── core/
│       │   ├── config.py          # Pydantic BaseSettings (single source of truth)
│       │   ├── db.py              # Injectable DB factory (path, engine, etc.)
│       │   ├── models.py          # Dataclasses / TypedDicts for Notification, Log, etc.
│       │   ├── scheduler.py       # Recurrence math + APScheduler integration layer
│       │   ├── delivery.py        # CHANNELS registry, _deliver, _is_transient
│       │   └── channels/
│       │       ├── __init__.py
│       │       ├── telegram.py
│       │       ├── discord.py
│       │       ├── pushover.py
│       │       └── email.py
│       ├── cli/
│       │   └── main.py            # argparse / rich CLI (preserves current UX)
│       ├── web/
│       │   ├── main.py            # FastAPI app + lifespan
│       │   ├── dependencies.py
│       │   ├── routers/
│       │   │   ├── notifications.py
│       │   │   ├── services.py
│       │   │   └── system.py
│       │   ├── templates/         # Jinja2 + HTMX or simple Tailwind
│       │   └── static/
│       └── legacy/                # Temporary compatibility shims
├── tests/
│   └── test_smoke.py              # Must remain green
├── Dockerfile
├── docker-compose.yml
├── .env.example                   # CRITICAL MISSING FILE
├── requirements.txt               # Keep temporarily for backward compat
└── ...
```

**Why this structure?**
- Core has zero knowledge of CLI, web, or environment.
- CLI and Web are thin adapters over the same core services.
- Matches the direction the existing CHANNELS + smoke-tested helpers already point.

### 4. Docker Deployment Model
- **Default**: Single container (`Dockerfile`) running Uvicorn + APScheduler in one process (`--workers 1` or careful lifespan management).
- **Optional**: `docker-compose.yml` with `web` + future dedicated `scheduler-worker` service (using the same image with different CMD).
- Volumes: `notifications.db`, `version_notes.db`, `.env`, logs, backups.
- Healthcheck on the web endpoint + scheduler heartbeat status.

---

## Phased Implementation Plan

### Prerequisites (Do First — Low Risk, High Value)
1. **Add the missing `.env.example`** (currently the #1 onboarding bug).
2. Add proper GitHub topics, description, and create the first real GitHub Release for v2.1.0.
3. Add minimal CI (ruff + smoke test on push/PR).

### Phase 2A — Foundation & Packaging (Shippable Increment)
- Create modern `pyproject.toml` (hatchling or setuptools, with dynamic version or static).
- Define console scripts:
  - `notifier` → new CLI entry (backward compatible behavior)
  - `notifier-web` → launch the web UI
- Set up `src/` layout.
- Extract `NotifierConfig` (Pydantic `BaseSettings`) as the single source of truth. Remove raw `os.getenv` calls from core logic.
- Make `get_db()` take an optional database path/engine (injectable).
- Update smoke tests to work with the new import structure.
- **Goal**: `pip install -e .` works and `notifier --help` behaves almost identically.

### Phase 2B — Core Extraction (Strangler Fig Pattern)
- Move pure helpers + recurrence logic into `notifier/core/scheduler.py`.
- Move CHANNELS + delivery logic into `notifier/core/delivery.py` + `channels/`.
- Keep public names stable where possible so smoke tests need minimal changes.
- Update all internal call sites gradually.
- **Success metric**: The entire old interactive CLI still works when run via the new entry point.

### Phase 2C — Scheduler Modernization
- Introduce APScheduler as a dependency (web extra).
- Create `notifier/core/scheduler.py` that can use either the legacy `schedule` approach or APScheduler.
- For Docker/web path: APScheduler with job definitions derived from the `notifications` table (or store next-run times).
- Keep `--daemon` working during transition.

### Phase 2D — Web GUI (FastAPI + HTMX)
- Minimal but polished web UI:
  - Dashboard: list of notifications with relative due times, status, recurrence.
  - Add / Edit / Delete / Snooze (full parity with current CRUD).
  - Services page: per-channel status, credential forms (reusing the CHANNELS registry data), test send buttons.
  - Logs viewer (paginated).
  - System / Heartbeat / Timezone controls.
  - "Send Due Now" + daemon status.
- Use HTMX for dynamic updates without a heavy frontend framework (keeps Docker image small).
- Authentication: simple single-user password or token (stored in .env) — this is a personal tool.
- Dark mode + clean Tailwind styling (match the existing CLI aesthetic where possible).

### Phase 2E — Docker + Deployment
- `Dockerfile` (multi-stage, slim Python image, non-root user).
- `docker-compose.yml` with proper volume, env, and restart policy.
- Health endpoint (`/health`).
- Documentation: "Run with Docker" section in README (one-liner + compose).
- Update `install.sh` to mention Docker as the preferred "always running" path.

### Phase 2F — Polish, Deprecation & Migration
- Add deprecation warnings for direct `python notifier.py` usage.
- Provide a clear migration guide (`MIGRATION.md`).
- Decide fate of Tkinter GUI (keep for now, mark as legacy, or remove in a later minor version).
- Decide fate of the custom `version_manager` (keep functional, or replace with git tags + conventional commits in a later phase).
- Update self-update logic (the git-based one becomes Docker pull or `pip install --upgrade`).
- Expand test coverage around the core (especially delivery + scheduler).

---

## Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking existing `python notifier.py` + `~/notifier` installs | High (user data loss fear) | Provide compatibility shim + excellent migration guide. Docker path becomes the new "set and forget" recommendation. |
| Two DBs + version_manager coupling | Medium | Keep `version_manager.py` working as-is during transition. Later consider merging or simplifying. |
| Scheduler behavior differences (APScheduler vs schedule) | High (recurrence bugs) | Keep existing smoke tests + add new scheduler-specific tests. Run side-by-side in early Docker releases. |
| Credential / env mutation in web context | High | Centralize all config in `NotifierConfig`. Web forms write to .env via the same `set_key` mechanism but reload config properly. |
| Self-update mechanism breaks after packaging | Medium | Document that Docker users use `docker pull` / compose pull. CLI users use `pip install --upgrade`. Remove or heavily modify the git-based updater. |
| Smoke test breakage during refactor | Medium | Extract core first while keeping public helper names stable. Run tests in CI on every step. |

---

## Technology Choices (Final)

- **Core**: Python 3.10+
- **Web**: FastAPI + Uvicorn + Jinja2 + HTMX + Tailwind (via CDN or local)
- **Scheduler**: APScheduler
- **Config**: Pydantic Settings
- **Packaging**: `pyproject.toml` + hatchling/setuptools
- **Docker base**: `python:3.11-slim` or `python:3.12-slim`
- **Optional nice-to-haves later**: ruff + mypy in CI, pre-commit, rich for enhanced CLI

---

## Success Criteria

1. `pip install -e ".[web]"` + `notifier-web` launches a working web UI.
2. `docker compose up` gives a fully functional always-on notifier with web interface.
3. All existing CLI commands (`notifier`, `notifier --daemon`, `notifier --add ...`) continue to work with no behavior change for existing users.
4. Smoke tests remain green throughout the refactor.
5. A new user can follow "Run with Docker" instructions in < 5 minutes and have a working web GUI + notifications.

---

## Out of Scope for This Phase (Future Work)

- Multi-user / proper auth system
- Replacing the custom version_manager entirely
- Mobile app or external API consumers
- Redis / PostgreSQL backends
- Full test coverage of channels (requires heavy mocking)
- NiceGUI / Streamlit alternative UI (if users request it)

---

## Immediate Next Steps (After Plan Approval)

1. User approves this plan (or requests specific changes to scope/phasing).
2. Create the missing `.env.example` + small CI improvements as a quick win (can be done in parallel).
3. Begin Phase 2A (packaging + config extraction) in a feature branch or worktree.
4. Regular checkpoints after each sub-phase with working demos.

---

**Plan Owner**: Grok (planning) + User (direction)
**Status**: Ready for review and approval via `exit_plan_mode`.

This plan is deliberately pragmatic — it honors the high engineering quality already present while systematically removing the blockers that prevent the project from scaling to a proper packaged + Dockerized web application.