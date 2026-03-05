#!/usr/bin/env bash
# install.sh — Ubuntu / Linux installer for Notifier
set -e

REPO="https://github.com/trickdaddy24/notifier.git"
INSTALL_DIR="$HOME/notifier"

echo ""
echo "🔔 Notifier — Installer"
echo "=============================="
echo ""

OS_TYPE=$(uname -s)

if [[ "$OS_TYPE" != "Linux" ]]; then
    echo "❌ This installer targets Ubuntu / Linux only."
    echo "   For macOS and Windows 11 see the README."
    exit 1
fi

echo "🐧 Detected Linux"
echo ""

# ── Python 3.10+ ───────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "📦 python3 not found — installing..."
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-pip python3-venv
fi

PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
PY_VER="$PY_MAJOR.$PY_MINOR"

if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ]]; then
    echo "❌ Python 3.10+ required. Found: Python $PY_VER"
    echo ""
    echo "   Upgrade with:"
    echo "   sudo apt install python3.10 python3.10-venv python3.10-pip"
    exit 1
fi
echo "✅ Python $PY_VER found"

# ── git ────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    echo "📦 git not found — installing..."
    sudo apt-get update -qq
    sudo apt-get install -y git
fi
echo "✅ git found"

# ── libnotify (desktop notifications) ─────────────────────────
if ! command -v notify-send &>/dev/null; then
    echo "📦 Installing libnotify-bin for desktop notifications..."
    sudo apt-get install -y libnotify-bin
fi
echo "✅ libnotify found"

# ── python3-tk (Tkinter GUI — optional) ───────────────────────
if ! python3 -c "import tkinter" &>/dev/null 2>&1; then
    echo "📦 Installing python3-tk for optional Tkinter GUI..."
    sudo apt-get install -y python3-tk
fi
echo "✅ tkinter found"

# ── Clone or update ────────────────────────────────────────────
echo ""
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "🔄 Existing install found — updating $INSTALL_DIR..."
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" reset --hard origin/main
else
    echo "📦 Cloning notifier to $INSTALL_DIR..."
    git clone "$REPO" "$INSTALL_DIR"
fi
echo "✅ Repo ready at $INSTALL_DIR"

# ── Virtual environment ────────────────────────────────────────
echo ""
echo "📦 Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"
echo "✅ Virtual environment ready"

# ── Dependencies ───────────────────────────────────────────────
echo "📦 Installing dependencies..."
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
echo "✅ Dependencies installed"

# ── Starter .env ───────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo ""
    echo "📄 Creating starter .env file..."
    cat > "$INSTALL_DIR/.env" << 'ENVFILE'
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Discord
DISCORD_WEBHOOK_URL=

# Pushover
PUSHOVER_USER_KEY=
PUSHOVER_API_TOKEN=

# Gmail
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_SENDER=
EMAIL_PASSWORD=
EMAIL_RECIPIENT=

# Timezone (optional — leave blank to use server local time)
# Examples: America/New_York  America/Chicago  America/Los_Angeles  Europe/London
TIMEZONE=

# Heartbeat interval in hours (0 = disabled)
HEARTBEAT_INTERVAL=0
ENVFILE
    echo "✅ .env created at $INSTALL_DIR/.env"
    echo "   Tip: You can set credentials from inside the app (option 6 → service → option 2)"
else
    echo "✅ Existing .env found — not overwritten."
fi

# ── Launcher script ────────────────────────────────────────────
LAUNCHER="$HOME/.local/bin/notifier"
mkdir -p "$HOME/.local/bin"
cat > "$LAUNCHER" << LAUNCHER_EOF
#!/usr/bin/env bash
source "$INSTALL_DIR/.venv/bin/activate"
python "$INSTALL_DIR/notifier.py"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
echo "✅ Launcher created: $LAUNCHER"

# ── PATH ───────────────────────────────────────────────────────
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── Done ───────────────────────────────────────────────────────
echo ""
echo "✅ Installation complete!"
echo ""
echo "Run the app:"
echo "  notifier"
echo ""
echo "Or directly:"
echo "  cd $INSTALL_DIR && source .venv/bin/activate && python notifier.py"
echo ""
echo "First-time setup:"
echo "  1. Run: notifier"
echo "  2. Go to option 6  — Notification Services → pick a service → option 2 (Set Credentials)"
echo "  3. Go to option 11 — System → option 5 (Set Timezone) if needed"
echo "  4. Go to option 11 — System → option 6 (Heartbeat) to enable periodic pings"
echo "  5. Go to option 10 — Open GUI (Tkinter) for a graphical view of your reminders"
echo ""
echo "⚠️  If 'notifier' command is not found after install, run:"
echo "   source ~/.bashrc"
echo ""
