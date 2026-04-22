"""One-shot rebrand: copaw / solo_hub → hubos / hubos.core.

Mechanical text rewrite across the repo. Run from repo root:

    python3 scripts/hubos_rebrand.py              # dry-run, shows diffs
    python3 scripts/hubos_rebrand.py --apply      # writes changes in place

Rules are ordered — MORE SPECIFIC first. This matters a lot: if we
rewrote "solo_hub" first, "solo_hub.core" would become "hubos.core.core"
instead of "hubos.core.orchestrator".

Scope: source + tests + scripts + docs. Frontend is *mostly* rewritten
here too (string-level replacements), but the brand-visible UI strings
(page titles, i18n copy) are also touched; verify the diff looks OK.

What is IN scope:
  • Python imports and attribute access
  • Environment variable names
  • Config paths ~/.copaw → ~/.hubos
  • pypi / package names
  • Frontend strings "CoPaw" → "HubOS", ".copaw" → ".hubos" in tsx/ts/less
  • Docker image references copaw → hubos
  • Markdown prose

What is OUT of scope (handled elsewhere):
  • pyproject.toml rewrite (too structured for regex — see P5)
  • CLI entrypoint script regeneration — pyproject scripts section
  • Filesystem rename — already done in P2
  • Config directory *runtime* migration — handled by constant.py shim

Files we skip:
  • binary files, images
  • node_modules, .git, .venv, .pytest_cache, egg-info
  • This script itself (it contains the forbidden words as rule keys)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[1]

# File extensions we rewrite.
TEXT_EXTS = {
    ".py", ".pyi",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".json", ".jsonc",
    ".md", ".mdx", ".rst",
    ".yml", ".yaml", ".toml", ".cfg", ".ini",
    ".less", ".css", ".scss", ".html",
    ".sh", ".env",
    ".dockerfile", ".Dockerfile",
}

# Directories to skip entirely.
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "node_modules", "dist", "build", ".next",
    ".mypy_cache", ".ruff_cache", ".tox",
}

# Files to skip (absolute or relative to repo root).
SKIP_FILES = {
    "scripts/hubos_rebrand.py",  # self
    "scripts/rewrite_orchestration_imports.py",  # legacy rewrite scripts
    "scripts/rewrite_test_patch_strings.py",
    "scripts/select_solo_hub_tests.py",
}


# ---------------------------------------------------------------------------
# Rewrite rules — ORDER MATTERS.
#
# Each rule is (pattern, replacement, description). Patterns are compiled
# as regex; use \b boundaries where you want "whole word" matching.
# ---------------------------------------------------------------------------


def _rules() -> list[tuple[re.Pattern[str], str, str]]:
    r: list[tuple[str, str, str]] = []

    # (A) solo_hub subpackage renames — MUST come before the bare `solo_hub`
    # rule so that e.g. `solo_hub.core` doesn't collapse to `hubos.core.core`.
    r.append((r"\bsolo_hub\.core\b", "hubos.core.orchestrator",
              "solo_hub.core → hubos.core.orchestrator"))

    # (B) Generic solo_hub → hubos.core (at word boundaries, so
    # `solo_hub_something` stays intact).
    r.append((r"\bsolo_hub\b", "hubos.core",
              "solo_hub → hubos.core"))

    # (C) copaw package → hubos. Careful: must not eat `copaw-data` in a
    # volume name that's been manually left, but here we actively WANT to
    # rewrite those, so a plain word-boundary substitution is fine.
    # Case-sensitive: we match lowercase `copaw` here and brand `CoPaw`
    # separately below.
    r.append((r"\bcopaw\b", "hubos",
              "copaw → hubos"))

    # (D) Brand string "CoPaw" → "HubOS" (UI, docstrings, marketing).
    r.append((r"\bCoPaw\b", "HubOS",
              "CoPaw → HubOS"))

    # (E) Env var prefix COPAW_ → HUBOS_  (uppercase only).
    r.append((r"\bCOPAW_", "HUBOS_",
              "COPAW_* env prefix → HUBOS_*"))

    # (F) Config directory ~/.copaw/ → ~/.hubos/  (already covered by C
    # because the leading `~/.` is just context, but keep an explicit
    # rule documented for clarity on any leftover literal strings.)
    # -- no-op, covered by (C).

    return [(re.compile(p), repl, desc) for (p, repl, desc) in r]


RULES = _rules()


# ---------------------------------------------------------------------------
# File walker.
# ---------------------------------------------------------------------------


def _iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        # Skip based on any path component matching SKIP_DIRS.
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if str(rel) in SKIP_FILES:
            continue
        # Skip unknown binary-ish extensions.
        if p.suffix and p.suffix.lower() not in TEXT_EXTS:
            # Also accept well-known extensionless config files.
            if p.name not in ("Dockerfile", "dockerfile", ".env", ".gitignore"):
                continue
        yield p


def _rewrite_one(text: str) -> tuple[str, list[str]]:
    """Apply all rules in order. Returns (new_text, applied-rule-descs)."""
    applied: list[str] = []
    for pat, repl, desc in RULES:
        new, n = pat.subn(repl, text)
        if n > 0:
            applied.append(f"{desc} (x{n})")
            text = new
    return text, applied


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default: dry-run).")
    ap.add_argument("--root", default=str(REPO),
                    help="repo root (default: script parent).")
    ns = ap.parse_args()

    root = Path(ns.root).resolve()
    changed = 0
    scanned = 0

    for f in _iter_files(root):
        scanned += 1
        try:
            original = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new, applied = _rewrite_one(original)
        if new == original:
            continue
        changed += 1
        rel = f.relative_to(root)
        print(f"  {rel}")
        for a in applied:
            print(f"      {a}")
        if ns.apply:
            f.write_text(new, encoding="utf-8")

    mode = "APPLIED" if ns.apply else "dry-run"
    print()
    print(f"{mode}: {changed} file(s) modified, {scanned} scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
