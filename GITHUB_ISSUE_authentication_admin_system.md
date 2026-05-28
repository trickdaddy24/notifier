# GitHub Issue Body: Phase 3 - Full Authentication & Admin System

Copy everything below this line and paste it when creating a new issue on GitHub.

---

**Title:**
[Phase 3] Full Authentication, Approval & Admin System (Next.js + Auth.js)

---

## Overview

We want to evolve the Notifier project with a full-featured, production-ready authentication and administration layer. This is a major step beyond the current temporary simple auth in the FastAPI web UI.

This feature is tracked in the [ROADMAP.md](https://github.com/trickdaddy24/notifier/blob/feat/web-auth-login/ROADMAP.md) under **Phase 3**.

## Key Requirements

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

## Technical Considerations

- This is expected to be built primarily as a **Next.js** frontend (App Router).
- It may run alongside the existing Python backend or eventually replace the current FastAPI web UI.
- Heavy reuse of existing Telegram sending logic (`notifier/notifications.py`).
- Significant increase in data models and security complexity.

## Current State

- Current auth (`web/auth.py`) is temporary and single-user oriented.
- Basic web UI exists (FastAPI + Jinja2).
- Telegram sending already works via the shared notifications module.
- No multi-user support or admin system exists yet.

## Acceptance Criteria

- [ ] Users can register with email + password
- [ ] Google OAuth works when configured
- [ ] Email verification flow is functional
- [ ] Three approval modes are configurable by admin
- [ ] Walled garden (pending/suspended) experience is complete
- [ ] Admin can manage users via `/users`
- [ ] Telegram bot can approve/deny registrations with inline buttons
- [ ] Security features (rate limiting, IP blocking, inactivity timeout) are implemented
- [ ] Admin Security Dashboard exists with configurable settings
- [ ] Monitoring endpoints and status page are live
- [ ] UI is polished with light/dark mode and good UX

## Related

- [ROADMAP.md - Phase 3](https://github.com/trickdaddy24/notifier/blob/feat/web-auth-login/ROADMAP.md#phase-3-full-authentication-approval--admin-system-high-priority-future-direction)
- Current temporary auth: `web/auth.py`
- Notification sending: `notifier/notifications.py`

---

**Priority**: High (Phase 3)

**Suggested Labels**: `enhancement`, `auth`, `roadmap`, `phase-3`, `next.js`

Please discuss architecture (Next.js frontend vs full rewrite) before starting implementation.