# -*- coding: utf-8 -*-
"""Lightweight helper to read the current UI language from settings.

Persisted in ``WORKING_DIR/settings.json`` alongside the FastAPI settings
router.  Provides ``get_ui_language()`` (returns ``"en"`` / ``"zh"`` / …)
and the convenience predicate ``is_zh()``.
"""
from __future__ import annotations

import json
from functools import lru_cache

from ..constant import WORKING_DIR

_SETTINGS_FILE = WORKING_DIR / "settings.json"


@lru_cache(maxsize=1)
def _read_language() -> str:
    """Read language from settings.json (cached until cleared)."""
    try:
        if _SETTINGS_FILE.is_file():
            data = json.loads(_SETTINGS_FILE.read_text("utf-8"))
            return data.get("language", "en")
    except (json.JSONDecodeError, OSError):
        pass
    return "en"


def get_ui_language() -> str:
    """Return the current UI language code (default ``"en"``).

    Result is cached; call :func:`clear_language_cache` after
    ``PUT /settings/language`` if you need the fresh value in the
    same process.
    """
    return _read_language()


def is_zh() -> bool:
    """True when the UI language is simplified Chinese."""
    return get_ui_language() == "zh"


def clear_language_cache() -> None:
    """Invalidate the cached language (e.g. after a settings update)."""
    _read_language.cache_clear()
