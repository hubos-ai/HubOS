#!/usr/bin/env python3
"""Archive and compact all HubOS JSON sessions outside the request path."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from hubos.core.memory.session_migration import migrate_all_sessions


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--working-dir",
        type=Path,
        default=Path.home() / ".hubos",
    )
    parser.add_argument("--skip-session-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-age-hours", type=float, default=2.0)
    parser.add_argument("--min-keep", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    migration_root = args.working_dir / "migrations" / "session-memory-v1"
    results = migrate_all_sessions(
        workspaces_root=args.working_dir / "workspaces",
        backup_root=migration_root / "backups",
        manifest_path=migration_root / f"manifest-{stamp}.jsonl",
        skip_session_ids=set(args.skip_session_id),
        dry_run=args.dry_run,
        max_age_hours=args.max_age_hours,
        min_keep=args.min_keep,
    )
    statuses = Counter(result.status for result in results)
    summary = {
        "sessions": len(results),
        "statuses": dict(statuses),
        "messages": sum(result.messages for result in results),
        "ledger_appended": sum(result.ledger_appended for result in results),
        "compacted": sum(result.compacted for result in results),
        "tool_payloads_compacted": sum(
            result.tool_payloads_compacted for result in results
        ),
        "errors": [
            {"path": result.path, "reason": result.reason}
            for result in results
            if result.status == "error"
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if statuses.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
