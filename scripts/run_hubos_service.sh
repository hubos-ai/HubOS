#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv-deploy}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/HubOS}"
WORK_DIR="${HUBOS_WORKING_DIR:-$HOME/.hubos}"
SECRET_DIR="${HUBOS_SECRET_DIR:-$HOME/.hubos.secret}"
PORT="${HUBOS_PORT:-8088}"

mkdir -p "$LOG_DIR" "$WORK_DIR" "$SECRET_DIR"

source "$VENV_DIR/bin/activate"
cd "$REPO_ROOT"

export HUBOS_WORKING_DIR="$WORK_DIR"
export HUBOS_SECRET_DIR="$SECRET_DIR"
export ENABLE_WORK_EXPERIENCE_LAYER="${ENABLE_WORK_EXPERIENCE_LAYER:-true}"
export PYTHONUNBUFFERED=1

exec hubos app --host 0.0.0.0 --port "$PORT"
