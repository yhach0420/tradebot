"""Executable classification for candidate-specific vs family baseline."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x28_executable_joint.metrics import support_ok
from research.e1_x28b_candidate_reference.classify import (
    abs_directional_positive,
    personalization_pairwise,
    stop_risk_tag,
    yen_only_positive,
)

from . import MIN_COMMON


def classify_executable(
    *,
    is_fallback: bool,
    sel: dict[str, Any],
    entry_delta: Optional[float],
    pers_delta: Optional[float],
    entry_n: int,
    pers_n: int,
    exit_cov: Optional[float],
) -> str:
    if is_fallback:
        return "EXECUTABLE_FALLBACK_NO_PERSONALIZATION_TEST"

    cov_ok = exit_cov is not None and exit_cov >= 0.70
    if not support_ok(sel) or not cov_ok:
        return "EXECUTABLE_SPECIFIC_SUPPORT_INSUFFICIENT"

    entry_ok = entry_n >= MIN_COMMON
    pers_ok = pers_n >= MIN_COMMON
    abs_pos = abs_directional_positive(sel)
    entry_pos = entry_delta is not None and entry_delta > 0 and entry_ok
    pers_pos = pers_delta is not None and pers_delta > 0 and pers_ok
    entry_nonpos = entry_delta is not None and entry_delta <= 0
    pers_nonpos = pers_delta is not None and pers_delta <= 0

    if abs_pos and entry_pos and pers_pos:
        return "EXECUTABLE_SPECIFIC_DIRECTIONAL_JOINT_POSITIVE"
    if abs_pos and entry_pos and (pers_nonpos or (pers_delta is not None and not pers_pos)):
        return "EXECUTABLE_SPECIFIC_ENTRY_EDGE_PERSONALIZATION_NOT_BETTER"
    if pers_pos and (entry_nonpos or not entry_pos):
        return "EXECUTABLE_SPECIFIC_PERSONALIZATION_ONLY"
    if yen_only_positive(sel):
        return "EXECUTABLE_SPECIFIC_YEN_POSITIVE_BPS_NONPOSITIVE"
    if abs_pos:
        return "EXECUTABLE_SPECIFIC_ABSOLUTE_POSITIVE_ONLY"
    return "EXECUTABLE_SPECIFIC_MIXED"


__all__ = [
    "classify_executable",
    "personalization_pairwise",
    "abs_directional_positive",
    "yen_only_positive",
    "stop_risk_tag",
]
