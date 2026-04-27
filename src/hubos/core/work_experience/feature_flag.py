# -*- coding: utf-8 -*-
"""Feature flag access for the Work Experience Layer."""

from hubos.core.infra.feature_flags import get_feature_flags


def is_work_experience_enabled() -> bool:
    """Return True if the Work Experience Layer is enabled."""
    return get_feature_flags().enable_work_experience_layer


def require_work_experience() -> None:
    """Raise RuntimeError if Work Experience Layer is disabled."""
    if not is_work_experience_enabled():
        raise RuntimeError(
            "Work Experience Layer is disabled. "
            "Set ENABLE_WORK_EXPERIENCE_LAYER=true to enable it.",
        )
