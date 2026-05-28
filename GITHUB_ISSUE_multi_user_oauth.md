# GitHub Issue Template: Multi-User Support + Google OAuth

Copy everything below the line and paste it when creating a new issue on GitHub.

---

**Title suggestion:**
`Multi-user accounts with Google OAuth (open registration, private per-user data)`

---

## Description

We currently have a very basic single-password authentication system in the web UI (`web/auth.py`). This is a temporary solution for personal use.

We want to evolve Notifier into a proper multi-user application with the following requirements:

### Core Requirements

- **Google OAuth login** implemented **inside** the FastAPI application (not handled only at the Traefik/Authelia proxy level).
- **Open registration** — anyone with a Google account can sign up.
- **Private per user** — Reminders must be strictly scoped to the logged-in user. Users should only see and manage their own reminders.
- **Multiple accounts** supported from day one.

### Non-Goals (MVP)

- Traditional email + password registration (Google OAuth is primary for now)
- Admin user management UI
- Shared/team reminder lists

### Technical Scope

- Add a `users` table (store Google `sub`, email, name, profile picture, timestamps, etc.)
- Add `user_id` column to the existing `notifications` table
- Migrate existing data (attach current reminders to a default/owner user during upgrade)
- Replace or heavily refactor `web/auth.py`
- Implement proper Google OAuth2 flow (authorization code + token exchange, refresh handling)
- Update all reminder-related endpoints and queries to respect `user_id`
- Update the dashboard to only show the current user's reminders
- Handle logout and session invalidation correctly for OAuth users

### Current Auth State (to be replaced)

- Located in `web/auth.py`
- Uses `itsdangerous` signed cookies + bcrypt against a single `NOTIFIER_WEB_PASSWORD`
- No user table, everything is effectively "admin" or freeform username display only
- See `web/templates/login.html` and `web/main.py` for current login flow

### Related Documents

- [ROADMAP.md](ROADMAP.md) — see the "Multi-User Support + Google OAuth" section
- [PHASE2_REFACTOR_PLAN.md](PHASE2_REFACTOR_PLAN.md)

### Questions for Design Phase

- Should we support multiple OAuth providers later (GitHub, Microsoft, etc.)?
- Do we want to allow the original simple password login as a "backdoor" for the instance owner?
- How should we handle the first user (who becomes owner)?
- Any preference on OAuth library (`authlib`, `google-auth-oauthlib`, or manual)?

---

**Labels suggestion:** `enhancement`, `web`, `auth`, `roadmap`

**Milestone:** Phase 3 (Post Web UI stabilization)

---

## Acceptance Criteria (when work begins)

- [ ] User can register/login using Google account via OAuth flow inside FastAPI
- [ ] New users can sign up without prior invitation (open registration)
- [ ] Each user only sees their own reminders
- [ ] Database schema updated with `users` table + `user_id` on notifications
- [ ] Existing single-user data has a clear migration path
- [ ] Current simple password auth is either removed or clearly marked as deprecated/owner-only

---

*This issue was created from the project roadmap during web UI development (May 2026).*
