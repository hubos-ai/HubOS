from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Tests should never mutate a developer's real legacy home during import.
os.environ.setdefault("HUBOS_SKIP_LEGACY_MIGRATION", "1")
