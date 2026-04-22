#!/bin/bash
set -euo pipefail

PLIST_ID="io.hubos.server"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_ID.plist"

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
echo "[HubOS] Stopped."
