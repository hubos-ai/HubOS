"""Second-pass rebrand: identifier-embedded legacy names.

The first pass (``hubos_rebrand.py``) uses word-boundary regex, which
correctly leaves snake_case / CamelCase identifiers alone when the
legacy token sits *inside* the identifier (e.g. ``enable_solo_hub_wechat_direct``,
``CoPaw_QA_Agent_0.1beta1``).  Those are the last holdouts.  This pass
targets each case with an explicit literal substitution, so we stay in
control of what gets changed.

Also renames a handful of on-disk directories (currently just the skill
``copaw_source_index``) and fixes the PowerShell build helper that the
first pass missed because ``.ps1`` wasn't in the text-extension allow
list.

Run from repo root:

    python3 scripts/hubos_rebrand_pass2.py            # dry-run
    python3 scripts/hubos_rebrand_pass2.py --apply    # commit
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Pure-text substitutions (literal, applied in order).
# ---------------------------------------------------------------------------

LITERAL_SUBS: list[tuple[str, str]] = [
    # Attribute / env-flag names — `solo_hub` embedded inside identifier.
    # `runtime` is the functional role played by what used to be called
    # solo_hub now that it's an in-process subpackage.
    # NOTE: list longest prefix first so that ENABLE_OPENWORK_... is
    # substituted before the shorter OPENWORK_... rule fires (otherwise
    # the tail would be rewritten twice with unstable results).
    ("ENABLE_OPENWORK_CHANNEL_TO_SOLO_HUB", "ENABLE_OPENWORK_CHANNEL_TO_RUNTIME"),
    ("OPENWORK_CHANNEL_TO_SOLO_HUB",        "OPENWORK_CHANNEL_TO_RUNTIME"),
    ("SOLO_HUB_WECHAT_DIRECT",              "RUNTIME_WECHAT_DIRECT"),
    ("enable_openwork_channel_to_solo_hub", "enable_openwork_channel_to_runtime"),
    ("enable_solo_hub_wechat_direct",       "enable_runtime_wechat_direct"),
    ("use_solo_hub_wechat_direct",          "use_runtime_wechat_direct"),
    ("openwork_channel_to_solo_hub",        "openwork_channel_to_runtime"),
    ("solo_hub_task_id",                    "runtime_task_id"),
    ("test_solo_hub_wechat_direct",         "test_runtime_wechat_direct"),
    # Internal sentinel user id used by the host-agent runner.
    ("\"_solo_hub\"",                       "\"_hubos_internal\""),
    # Prometheus metric prefix and channel-name strings. These live
    # inside string literals, so word boundaries don't help; we do it
    # as a substring substitution which is safe because `solo_hub_` as
    # a substring only appears as a namespace prefix anywhere in the
    # codebase at this point.
    ("solo_hub_",                           "hubos_core_"),

    # Agent / class / skill names.
    ("CoPawAgent",              "HubOSAgent"),
    ("CoPaw_QA_Agent_0.1beta1", "HubOS_QA_Agent_0.1beta1"),
    ("copaw_source_index",      "hubos_source_index"),

    # CLI process-detection helpers + related user-facing warning.
    ("_matches_copaw_cli_command", "_matches_hubos_cli_command"),
    ("_is_copaw_service_command",  "_is_hubos_service_command"),
    ("_is_copaw_wrapper_process",  "_is_hubos_wrapper_process"),
    ("RUNNING COPAW SERVICE",      "RUNNING HUBOS SERVICE"),

    # Telemetry field + local variable.
    ("copaw_version",              "hubos_version"),
    ("copaw_ver",                  "hubos_ver"),

    # PowerShell / shell path stragglers that the first pass missed
    # because their file extension wasn't in scope.
    (r"src\copaw\console",         r"src\hubos\console"),  # windows path
    ("src/copaw/console",          "src/hubos/console"),   # unix path
]


# Extensions to scan — the first pass already handled common sources;
# this pass additionally reaches .ps1.
TEXT_EXTS = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".json", ".jsonc", ".md", ".mdx", ".rst",
    ".yml", ".yaml", ".toml", ".cfg", ".ini",
    ".less", ".css", ".scss", ".html",
    ".sh", ".env", ".ps1", ".bat",
}

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "node_modules", "dist", "build", ".next",
    ".mypy_cache", ".ruff_cache", ".tox",
}

SKIP_FILES = {
    "scripts/hubos_rebrand.py",
    "scripts/hubos_rebrand_pass2.py",
    "scripts/rewrite_orchestration_imports.py",
    "scripts/rewrite_test_patch_strings.py",
    "scripts/select_solo_hub_tests.py",
}


# Directory renames — pairs of (old, new) relative to repo root.  Also
# detects a stale path (existing old, missing new) and renames it.
DIR_RENAMES: list[tuple[str, str]] = [
    ("src/hubos/agents/skills/copaw_source_index",
     "src/hubos/agents/skills/hubos_source_index"),
    # Align the test tree with the new production layout so that
    # tests/core/ maps onto src/hubos/core/.
    ("tests/solo_hub", "tests/core"),
]

FILE_RENAMES: list[tuple[str, str]] = [
    # Historical planning doc that was authored during the host-app
    # adaptation and still carries the legacy brand in its filename.
    ("docs/HUBOS_COPAW_UI_ADAPTATION_PLAN.md",
     "docs/HUBOS_UI_ADAPTATION_PLAN.md"),
]


def _iter_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if str(rel) in SKIP_FILES:
            continue
        if p.suffix and p.suffix.lower() not in TEXT_EXTS:
            if p.name not in ("Dockerfile", "dockerfile", ".env", ".gitignore"):
                continue
        yield p


def _rewrite(text: str) -> tuple[str, int]:
    hits = 0
    for old, new in LITERAL_SUBS:
        if old in text:
            n = text.count(old)
            text = text.replace(old, new)
            hits += n
    return text, hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--root", default=str(REPO))
    ns = ap.parse_args()

    root = Path(ns.root).resolve()
    scanned = 0
    changed_files = 0
    total_hits = 0

    for f in _iter_files(root):
        scanned += 1
        try:
            original = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new, hits = _rewrite(original)
        if hits == 0:
            continue
        changed_files += 1
        total_hits += hits
        rel = f.relative_to(root)
        print(f"  {rel} (x{hits})")
        if ns.apply:
            f.write_text(new, encoding="utf-8")

    # Directory renames.
    renamed_dirs: list[tuple[Path, Path]] = []
    for rel_old, rel_new in DIR_RENAMES:
        old = root / rel_old
        new = root / rel_new
        if old.is_dir() and not new.exists():
            print(f"  DIR: {rel_old} → {rel_new}")
            renamed_dirs.append((old, new))
            if ns.apply:
                shutil.move(str(old), str(new))

    # File renames.
    renamed_files: list[tuple[Path, Path]] = []
    for rel_old, rel_new in FILE_RENAMES:
        old = root / rel_old
        new = root / rel_new
        if old.is_file() and not new.exists():
            print(f"  FILE: {rel_old} → {rel_new}")
            renamed_files.append((old, new))
            if ns.apply:
                shutil.move(str(old), str(new))

    mode = "APPLIED" if ns.apply else "dry-run"
    print()
    print(
        f"{mode}: {changed_files} file(s), {total_hits} literal subs, "
        f"{len(renamed_dirs)} dir(s), {len(renamed_files)} file renames, "
        f"{scanned} scanned"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
