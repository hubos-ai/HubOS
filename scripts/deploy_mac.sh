#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv-deploy"
RUN_SCRIPT="$REPO_ROOT/scripts/run_hubos_service.sh"
PLIST_ID="io.hubos.server"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_ID.plist"
LOG_DIR="$HOME/Library/Logs/HubOS"
WORK_DIR="${HUBOS_WORKING_DIR:-$HOME/.hubos}"
SECRET_DIR="${HUBOS_SECRET_DIR:-$HOME/.hubos.secret}"
PORT="${HUBOS_PORT:-8088}"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR" "$WORK_DIR" "$SECRET_DIR"

find_python() {
  local candidates=(
    "${PYTHON_BIN:-}"
    "$(command -v python3 2>/dev/null || true)"
    "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3"
    "/opt/homebrew/bin/python3.11"
    "/usr/local/bin/python3.11"
    "/opt/homebrew/bin/python3.12"
    "/usr/local/bin/python3.12"
  )
  local py
  for py in "${candidates[@]}"; do
    [ -n "$py" ] || continue
    [ -x "$py" ] || continue
    if "$py" - <<'PY' >/dev/null 2>&1
import sys
sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)
PY
    then
      echo "$py"
      return 0
    fi
  done
  return 1
}

ensure_brew_pkg() {
  local cmd="$1"
  local pkg="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    if ! command -v brew >/dev/null 2>&1; then
      echo "[HubOS] Missing $cmd and Homebrew is not installed." >&2
      exit 1
    fi
    echo "[HubOS] Installing $pkg via Homebrew..."
    brew install "$pkg"
  fi
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  ensure_brew_pkg brew brew
  ensure_brew_pkg python3 python@3.11
  PYTHON="$(find_python || true)"
fi

if [ -z "$PYTHON" ]; then
  echo "[HubOS] Python 3.10-3.13 not found. Please install python@3.11." >&2
  exit 1
fi

ensure_brew_pkg npm node

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

cd "$REPO_ROOT/console"
npm install
npm run build

cd "$REPO_ROOT"
python -m pip install --no-cache-dir .

export HUBOS_WORKING_DIR="$WORK_DIR"
export HUBOS_SECRET_DIR="$SECRET_DIR"
export ENABLE_WORK_EXPERIENCE_LAYER=true
export PYTHONUNBUFFERED=1

if [ ! -f "$WORK_DIR/HEARTBEAT.md" ]; then
  hubos init --defaults --accept-security
fi

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_ID}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${RUN_SCRIPT}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>${REPO_ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>VENV_DIR</key>
    <string>${VENV_DIR}</string>
    <key>LOG_DIR</key>
    <string>${LOG_DIR}</string>
    <key>HUBOS_PORT</key>
    <string>${PORT}</string>
    <key>HUBOS_WORKING_DIR</key>
    <string>${WORK_DIR}</string>
    <key>HUBOS_SECRET_DIR</key>
    <string>${SECRET_DIR}</string>
    <key>ENABLE_WORK_EXPERIENCE_LAYER</key>
    <string>true</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/hubos.out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/hubos.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/${PLIST_ID}" || true

echo "[HubOS] Deploy complete."
echo "[HubOS] Local: http://localhost:${PORT}"
echo "[HubOS] LAN:   http://$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo YOUR_MAC_IP):${PORT}"
echo "[HubOS] Logs:  ${LOG_DIR}"
