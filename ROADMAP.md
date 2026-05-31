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

---

## Phase 3: Full Authentication, Approval & Admin System (High Priority Future Direction)

**Status**: Not started — recorded for later implementation (user request, June 2026).

**Note**: This represents a significant evolution from the current temporary Python/FastAPI + Jinja2 web UI. It would likely involve building a modern frontend (most likely **Next.js 15+** with the App Router) while potentially keeping the existing Python backend for the core reminder scheduling and notification delivery logic.

### Authentication

- **Email/Password Auth** — register and sign in with bcrypt-hashed passwords
- **Google OAuth** — one-click sign in with Google via Auth.js v5 (conditional — only loads when configured)
- **Email Verification** — verification email sent on registration via Gmail SMTP (Nodemailer), token expires in 24 hours
- **Configurable Approval Mode** — admin chooses one of three modes from the Security dashboard:
  - **Email Only** (default) — email verification required, auto-approved after verifying
  - **Telegram Only** — no email verification, Telegram approve/deny immediately on registration
  - **Both** — email verification first, then Telegram approve/deny after verification
- **Resend Verification** — login page shows "Resend Verification Email" for unverified users (rate-limited to 1 per minute)
- **Defense-in-depth** — both `loginWithCredentials()` and `authorize()` block unverified users when mode requires it
- **JWT Sessions** — stateless auth with secure httpOnly cookies
- **Route Protection** — edge middleware redirects unauthenticated users to login

### Walled Garden Approval System

- **Pending Approval** — new users land on a waiting page after registration until admin approves
- **Suspended State** — admin can suspend users who then see a dedicated "Account Suspended" page
- **Auto-approve Admin** — `ADMIN_EMAIL` env var auto-approves that email as admin on registration
- **Real-time Status** — pending page polls every 3 seconds and auto-redirects on approval
- **Admin Panel** (`/users`) — full user management table with approve, suspend, unsuspend, delete, and add user actions
- **Telegram Bot** — admin receives inline Approve/Deny buttons for new registrations
- **Telegram Commands** — `/suspend`, `/unsuspend`, `/status`, `/users` via webhook
- **JWT Re-fetch** — role/status re-fetched from DB on every token refresh so admin changes take effect immediately

### Security

- **Rate Limiting** — tracks failed login attempts per IP; auto-blocks after configurable threshold (default: 5 attempts → 15 min block)
- **IP Blocking** — automatic blocks from rate limiting + manual block/unblock from admin panel (permanent or timed)
- **Visitor Fingerprinting** — logs IP, user agent, timestamp, and page for every authenticated visit
- **Inactivity Timeout** — client-side auto-logout after configurable idle period (default: 30 minutes)
- **Registration Lockdown** — admin toggle to open/close new user registration
- **Admin Security Dashboard** (`/security`) — tabbed interface for settings, blocked IPs, visitor logs, and login attempt history
- **Configurable Settings** — `SiteSettings` table stores all security thresholds, editable from admin panel
- **Access Denied Page** — blocked IPs see a clean error page with no access to any routes

### Monitoring

- **API Health Check** (`/api/health`) — public JSON endpoint for uptime monitors and load balancers
- **System Status Dashboard** (`/status`) — admin-only page with real-time service health, version, uptime, DB latency, and auto-refresh every 30s
- **Service Checks** — database connectivity (with latency), Google OAuth config, Telegram bot config, Email SMTP config (with "Send Test" button), git remote availability
- **Overall Status** — healthy / degraded / unhealthy based on database + optional service states

### Registration & Validation

- **Password Strength Meter** — 5-bar visual indicator (Weak / Fair / Good / Strong / Very Strong)
- **Password Complexity** — enforces 8+ characters and at least 2 character types
- **Duplicate Email Detection** — user-friendly error on existing accounts
- **Confirm Password** — match validation before submission

### Dashboard

- **Sidebar Navigation** — Dashboard, Projects, Analytics, Settings with active route highlighting
- **Avatar Dropdown** — initials circle (or Google profile image) with theme toggle, settings link, sign out
- **Stat Cards** — 4 placeholder metric cards with icons
- **Settings Page** — read-only profile display, change password & connected accounts placeholders
- **Projects / Analytics** — placeholder pages ready for content

### UI/UX

- **Light + Dark Mode** — full theme support with class-based toggle (Tailwind v4)
- **Toast Notifications** — Sonner-powered success/error toasts
- **Loading Spinners** — animated Loader2 icons on form buttons
- **Password Visibility** — eye/eye-off toggles on all password fields
- **Form Animations** — fade-in/slide-up on auth pages
- **Responsive Typography** — Geist Sans/Mono fonts with custom scrollbar

### Data Layer

- **Prisma + SQLite** — local database, zero external dependencies
- **Auth.js Adapter Schema** — User, Account, Session, VerificationToken models
- **Server Actions** — register, login, OAuth operations

#### Technical Considerations

- This would likely be built as a **separate Next.js frontend** (or full-stack replacement) that communicates with the existing Python backend (or a new unified backend).
- The current FastAPI web UI (`web/`) would be deprecated or kept only for the core reminder scheduling engine.
- Deep integration with Telegram bot (already partially present) for the approval workflow.
- Significant increase in complexity around user states, approvals, and security.

#### Related Current Code

- `web/auth.py` (temporary simple auth — to be replaced)
- `notifier/notifications.py` (contains Telegram sending logic that would be reused)

---

**Last updated**: June 2026 (by user request)
