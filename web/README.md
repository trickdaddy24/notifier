# Notifier Web UI

This is the new web interface for the Notifier project (work in progress as part of Phase 2).

## Quick Start (Development)

```bash
# From the project root
pip install fastapi uvicorn python-multipart bcrypt itsdangerous

# Set a password (required for login)
export NOTIFIER_WEB_PASSWORD="your-secure-password-here"

# Run the web UI
python -m uvicorn notifier.web.main:app --reload --port 8000
```

Then open http://localhost:8000

## Configuration (.env)

```env
# --- Web UI Authentication ---
NOTIFIER_WEB_PASSWORD=change-this-to-something-strong
# OR (recommended for production)
# NOTIFIER_WEB_PASSWORD_HASH=$2b$12$your-bcrypt-hash-here

# Optional
NOTIFIER_WEB_SECRET_KEY=some-long-random-string
NOTIFIER_WEB_SESSION_MAX_AGE=604800          # 7 days in seconds
```

## Features (Current)

- Beautiful modern login form with Tailwind
- Secure signed cookie sessions
- Support for `Authorization: Bearer <token>` and `?token=...`
- Protected routes with automatic redirect to login
- Simple `/health` and `/api/me` endpoints
- Works great in Docker

## Production Notes

- Set `NOTIFIER_WEB_PASSWORD_HASH` (use `bcrypt` to generate)
- Put the app behind a reverse proxy (Caddy / Nginx / Traefik) with HTTPS
- Consider setting `secure=True` on the session cookie in production

This is the foundation. The real dashboard, services management, logs, etc. will be built on top of this auth system.
