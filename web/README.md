# Notifier Web UI

**Status**: Early but functional. Web UI + Docker is now the **primary focus** of the project.

The web interface is the recommended way to use Notifier day-to-day. A strong CLI remains available for power users and scripting.

## Quick Start

### Option A: Docker (Recommended)

```bash
# 1. Copy the example env file
cp .env.example .env

# 2. Edit .env and set a password
NOTIFIER_WEB_PASSWORD=your-strong-password-here

# 3. Start with Docker Compose
docker compose up --build
```

Then open **http://localhost:8000**

### Option B: Local (PowerShell / Windows)

```powershell
cd F:\grok\notifier

# Activate venv
.\.venv\Scripts\Activate.ps1

# Set password
$env:NOTIFIER_WEB_PASSWORD = "your-strong-password-here"

# Run (correct command)
python -m uvicorn --app-dir . web.main:app --reload --port 8000
```

Open **http://localhost:8000**

## Configuration

Copy `.env.example` → `.env` and fill in at minimum:

```env
NOTIFIER_WEB_PASSWORD=your-strong-password-here
```

Optional but recommended:
- `NOTIFIER_WEB_SECRET_KEY`
- `NOTIFIER_WEB_SESSION_MAX_AGE`

## Current Features

- Secure login (cookie sessions + Bearer token support)
- Protected routes
- Clean dark UI (Tailwind via CDN)
- Health endpoint (`/health`)
- Works in Docker

## Next Milestones (Web Focus)

- Notification CRUD in the web UI
- Channel/credential management from the browser
- Logs viewer
- Full Docker Compose examples with reverse proxy

---

**Note**: The old Tkinter GUI is now considered legacy. The future is the web interface + Docker.
