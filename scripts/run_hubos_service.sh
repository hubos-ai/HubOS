#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv-deploy}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/HubOS}"
WORK_DIR="${HUBOS_WORKING_DIR:-$HOME/.hubos}"
SECRET_DIR="${HUBOS_SECRET_DIR:-$HOME/.hubos.secret}"
PORT="${HUBOS_PORT:-8088}"
HOST="${HUBOS_HOST:-0.0.0.0}"
LOG_MAX_BYTES="${HUBOS_LOG_MAX_BYTES:-52428800}"
LOG_BACKUP_COUNT="${HUBOS_LOG_BACKUP_COUNT:-3}"

mkdir -p "$LOG_DIR" "$WORK_DIR" "$SECRET_DIR"

rotate_log() {
  local file="$1"
  [ -f "$file" ] || return 0

  local size
  size="$(wc -c < "$file" 2>/dev/null || echo 0)"
  [ "$size" -gt "$LOG_MAX_BYTES" ] || return 0

  local i prev
  i="$LOG_BACKUP_COUNT"
  while [ "$i" -gt 1 ]; do
    prev=$((i - 1))
    [ -f "$file.$prev" ] && mv -f "$file.$prev" "$file.$i"
    i="$prev"
  done

  # launchd opens stdout/stderr before this script runs. Tail+truncate keeps
  # the original inode valid for the already-open descriptor without copying a
  # huge historical log in full.
  tail -c "$LOG_MAX_BYTES" "$file" > "$file.1" 2>/dev/null || true
  : > "$file"
}

rotate_log "$LOG_DIR/hubos.out.log"
rotate_log "$LOG_DIR/hubos.err.log"

list_listeners() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true
}

wait_for_port_release() {
  local listeners=""
  local waited=0

  while true; do
    listeners="$(list_listeners)"
    [ -z "$listeners" ] && return 0

    if [ "$waited" -eq 0 ]; then
      echo "[hubos] Port $PORT already in use by PID(s): ${listeners//$'\n'/, }. Waiting for the previous instance to exit..." >&2
    elif [ $((waited % 10)) -eq 0 ]; then
      echo "[hubos] Still waiting for port $PORT to be released..." >&2
    fi

    sleep 1
    waited=$((waited + 1))
  done
}

source "$VENV_DIR/bin/activate"
cd "$REPO_ROOT"

export HUBOS_WORKING_DIR="$WORK_DIR"
export HUBOS_SECRET_DIR="$SECRET_DIR"
export ENABLE_WORK_EXPERIENCE_LAYER="${ENABLE_WORK_EXPERIENCE_LAYER:-true}"
export PYTHONUNBUFFERED=1

wait_for_port_release

exec hubos app --host "$HOST" --port "$PORT"
