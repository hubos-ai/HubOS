#!/bin/bash
set -euo pipefail

PLIST_ID="io.hubos.server"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_ID.plist"
PORT="${HUBOS_PORT:-8088}"
LOG_DIR="$HOME/Library/Logs/HubOS"

echo "== HubOS Service Status =="
echo "Label: $PLIST_ID"
echo "Plist: $PLIST_PATH"
echo

if [ -f "$PLIST_PATH" ]; then
  echo "[OK] LaunchAgent exists"
else
  echo "[WARN] LaunchAgent not found"
fi

echo
echo "== launchctl =="
launchctl list | grep "$PLIST_ID" || echo "[WARN] Service not loaded in launchctl"

echo
echo "== Port Check =="
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
else
  echo "[WARN] Nothing is listening on port $PORT"
fi

echo
echo "== HTTP Check =="
if curl -fsS "http://127.0.0.1:${PORT}/api/version" 2>/dev/null; then
  echo
else
  echo "[WARN] HTTP health check failed"
fi

echo
echo "== Recent Logs =="
tail -n 20 "$LOG_DIR/hubos.out.log" 2>/dev/null || true
tail -n 20 "$LOG_DIR/hubos.err.log" 2>/dev/null || true
