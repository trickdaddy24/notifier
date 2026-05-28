# Notifier Roadmap

This document tracks planned major features and architectural work for the Notifier project.

---

## Current Focus (Phase 2)

- Basic web UI with authentication (single shared password + optional username display)
- Add / view / delete reminders through the web interface
- Docker deployment on Saltbox (Traefik)
- Shared SQLite database between CLI and web

The current authentication system (`web/auth.py`) is **temporary and intentionally simple**. It is a single-password gate with signed cookies. It is **not** designed for multiple users.

---

## Major Planned Work

### Multi-User Support + Google OAuth (High Priority Future Work)

**Status**: Not started — recorded for later implementation.

#### Requirements

- **Open registration**: Any user should be able to create an account using their Google account (Gmail OAuth).
- **OAuth inside FastAPI**: Google OAuth flow must be implemented directly in the FastAPI application (using a library such as `authlib` or the official Google libraries), **not** handled exclusively at the Traefik/Authelia layer.
- **Private per user**: All reminders must be scoped to the individual user.
  - Each user can only see, create, edit, and delete their own reminders.
  - No cross-user visibility by default.
- **Multiple accounts**: Full support for multiple independent user accounts.
- **Migration path**: Existing reminders created under the simple single-user auth must be either:
  - Attached to a default/owner account during migration, or
  - Left as-is with a clear upgrade path.

#### Non-Goals (for the initial implementation)

- Email + password registration (Google OAuth is the primary login method for now)
- Admin panel for user management (can be added later)
- Team/shared reminder lists (future enhancement)

#### Technical Considerations

- Database changes:
  - New `users` table (id, email, google_sub, name, picture, created_at, last_login, etc.)
  - Add `user_id` foreign key to the `notifications` table (with migration for existing data)
- Replace or significantly extend the current `web/auth.py` system.
- Proper session / OAuth token handling.
- Google OAuth consent screen configuration + required scopes.
- Rate limiting and abuse protection for open registration.
- Logout + session invalidation for OAuth accounts.

#### Related Files (Current State)

- `web/auth.py` — current temporary single-password auth
- `web/main.py` — login routes and dashboard
- `web/templates/login.html` and `dashboard.html`
- Database logic in `notifier.py` (`notifications` table)

---

## Other Future Ideas (Lower Priority)

- Replace `schedule` library with APScheduler (see `PHASE2_REFACTOR_PLAN.md`)
- Proper Python packaging (`pyproject.toml`, src layout)
- Modular core (separate `notifier/core/`)
- Richer dashboard (channels status, delivery logs, bulk actions)
- Mobile-friendly improvements / PWA
- Email notifications for upcoming reminders
- Webhook / API access per user with scoped tokens

---

## How to Contribute

If you're interested in working on the **Multi-User + Google OAuth** feature, please open an issue referencing this roadmap item and we can coordinate the design.

---

**Last updated**: May 2026 (by user request during web UI development)
