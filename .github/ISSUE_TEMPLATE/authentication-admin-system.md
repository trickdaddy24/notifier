---
name: Phase 3 - Full Authentication & Admin System
about: Request to implement a production-grade authentication, approval, and admin system (Next.js + Auth.js direction)
title: '[Phase 3] Full Authentication, Approval & Admin System'
labels: enhancement, auth, roadmap, phase-3, high-priority, next.js, security, admin-panel
milestone: Phase 3: Authentication & Admin System
assignees: ''
---

## Overview

We want to evolve the Notifier project with a full-featured, production-ready authentication and administration layer. This is a major step beyond the current temporary simple auth in the FastAPI web UI.

This feature is tracked in the [ROADMAP.md](../ROADMAP.md) under **Phase 3**.

## Key Requirements

### Authentication
- Email/Password registration and login with bcrypt-hashed passwords
- Google OAuth sign-in via Auth.js v5 (optional / conditional)
- Email verification on registration (via Gmail SMTP + Nodemailer)
- Configurable Approval Modes (Email Only / Telegram Only / Both)
- Resend verification email (rate-limited)
- Defense-in-depth blocking of unverified users
- JWT sessions with secure httpOnly cookies
- Route protection via middleware

### Walled Garden Approval System
- Pending approval state with waiting page
- Suspended user state with dedicated page
- Auto-approve for admin email via `ADMIN_EMAIL` env var
- Real-time polling on pending page (auto-redirect on approval)
- Full Admin user management table (`/users`)
- Telegram bot with inline Approve/Deny buttons for new registrations
- Telegram commands (`/suspend`, `/unsuspend`, `/status`, `/users`)
- JWT refresh that re-fetches user role/status from DB

### Security
- Rate limiting on login attempts (IP-based, configurable)
- Automatic + manual IP blocking (permanent or timed)
- Visitor fingerprinting / access logging
- Inactivity timeout with auto-logout
- Registration lockdown toggle (open/closed)
- Dedicated Admin Security Dashboard (`/security`)
- Configurable security settings stored in DB
- Clean "Access Denied" page for blocked users

### Monitoring
- Public `/api/health` endpoint
- Admin-only `/status` dashboard with real-time checks
- Service health checks (DB, Google OAuth, Telegram, Email, Git)
- Overall system status (healthy / degraded / unhealthy)

### Registration & Validation
- Password strength meter + complexity rules
- Duplicate email detection
- Confirm password matching

### Dashboard & UI/UX
- Modern sidebar navigation
- Avatar dropdown with theme toggle
- Light + Dark mode (Tailwind v4)
- Toast notifications (Sonner)
- Responsive, polished auth and dashboard experience

### Data Layer
- Prisma + SQLite
- Proper Auth.js adapter schema (User, Account, Session, VerificationToken, etc.)
- Server Actions for auth flows

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

**Suggested Milestone**: `Phase 3: Authentication & Admin System`

Please discuss architecture (Next.js frontend vs full rewrite) before starting implementation.