#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"
git pull --ff-only
bash "$REPO_ROOT/scripts/deploy_mac.sh"
