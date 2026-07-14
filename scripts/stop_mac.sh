#!/bin/bash
set -euo pipefail

PLIST_ID="io.hubos.server"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_ID.plist"
PORT="${HUBOS_PORT:-8088}"

list_port_listeners() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true
}

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
PIDS="$(list_port_listeners)"
if [ -n "$PIDS" ]; then
  echo "[HubOS] Stopping listener(s) on port $PORT: ${PIDS//$'\n'/, }"
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    kill -TERM "$pid" >/dev/null 2>&1 || true
  done <<< "$PIDS"
fi
echo "[HubOS] Stopped."
