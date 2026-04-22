#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v git >/dev/null 2>&1; then
  echo "[HubOS] git is required." >&2
  exit 1
fi

if ! git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1; then
  echo "[HubOS] This directory is not a git clone with an origin remote." >&2
  exit 1
fi

ORIGIN_URL="$(git -C "$REPO_ROOT" remote get-url origin)"
if [[ "$ORIGIN_URL" == https://github.com/* ]] && command -v gh >/dev/null 2>&1; then
  if ! gh auth status >/dev/null 2>&1; then
    echo "[HubOS] GitHub CLI is not authenticated. Running gh auth login..."
    gh auth login
  fi
fi

exec bash "$REPO_ROOT/scripts/deploy_mac.sh"
