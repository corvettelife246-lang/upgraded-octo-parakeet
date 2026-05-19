#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AI Multi-Agent Admin — WSL-2 (Ubuntu) Setup Script
# Compatible with Windows 10 x64 Home + WSL-2
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_MIN="3.10"

# ─── 1. System packages ───────────────────────────────────────────────────────
info "Updating apt and installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  python3 python3-pip python3-venv python3-dev \
  build-essential git curl wget ffmpeg \
  portaudio19-dev libsndfile1-dev \
  libgl1-mesa-glx libglib2.0-0 \
  espeak-ng pulseaudio \
  ca-certificates

success "System packages installed."

# ─── 2. Python version check ──────────────────────────────────────────────────
PYTHON=$(command -v python3 || true)
[[ -z "$PYTHON" ]] && die "python3 not found."
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python version: $PY_VER"
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" \
  || die "Python 3.10+ required. Install with: sudo apt install python3.11"

# ─── 3. Virtual environment ───────────────────────────────────────────────────
VENV_DIR="$REPO_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating virtual environment at $VENV_DIR …"
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip wheel setuptools -q
success "Virtual environment ready."

# ─── 4. Python dependencies ───────────────────────────────────────────────────
info "Installing Python dependencies (this may take a few minutes)…"
pip install -r "$REPO_DIR/requirements.txt" -q
success "Python packages installed."

# ─── 5. Whisper model pre-download ───────────────────────────────────────────
MODEL=${WHISPER_MODEL:-base}
info "Pre-downloading Whisper '${MODEL}' model…"
python3 - <<EOF
import whisper, sys
try:
    whisper.load_model("${MODEL}")
    print("Whisper model ready.")
except Exception as e:
    print(f"Warning: {e}", file=sys.stderr)
EOF

# ─── 6. Environment file ──────────────────────────────────────────────────────
ENV_FILE="$REPO_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  info "Creating .env template…"
  cat > "$ENV_FILE" <<'ENVEOF'
# ── Anthropic ──
ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE
DEFAULT_MODEL=claude-opus-4-7
FAST_MODEL=claude-haiku-4-5-20251001

# ── Server ──
HOST=0.0.0.0
PORT=8000
DEBUG=false

# ── Voice ──
WHISPER_MODEL=base
TTS_ENGINE=edge
TTS_VOICE=en-US-AriaNeural

# ── Vision ──
CAMERA_INDEX=0
ENVEOF
  warn "Edit $ENV_FILE and set your ANTHROPIC_API_KEY before starting."
else
  success ".env already exists."
fi

# ─── 7. PulseAudio WSL-2 workaround ──────────────────────────────────────────
if grep -qi microsoft /proc/version 2>/dev/null; then
  info "WSL-2 detected — configuring PulseAudio…"
  PULSE_CONF="$HOME/.config/pulse/client.conf"
  mkdir -p "$(dirname "$PULSE_CONF")"
  cat > "$PULSE_CONF" <<'PEOF'
default-server = tcp:127.0.0.1
autospawn = no
daemon-binary = /bin/true
enable-shm = false
PEOF
  success "PulseAudio WSL-2 config written."
fi

# ─── 8. Systemd user service (optional) ──────────────────────────────────────
SERVICE_FILE="$HOME/.config/systemd/user/ai-admin.service"
if command -v systemctl &>/dev/null; then
  mkdir -p "$(dirname "$SERVICE_FILE")"
  cat > "$SERVICE_FILE" <<SVCEOF
[Unit]
Description=AI Multi-Agent Admin
After=network.target

[Service]
Type=simple
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m uvicorn ui.app:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=default.target
SVCEOF
  systemctl --user daemon-reload 2>/dev/null || true
  info "Systemd service written to $SERVICE_FILE. Enable with: systemctl --user enable ai-admin"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
success "═══════════════════════════════════════════════"
success " Installation complete!"
success "═══════════════════════════════════════════════"
echo ""
echo "  1. Edit .env and set ANTHROPIC_API_KEY"
echo "  2. Start the server:"
echo "       source .venv/bin/activate"
echo "       python main.py"
echo "  3. Open browser: http://localhost:8000"
echo ""
