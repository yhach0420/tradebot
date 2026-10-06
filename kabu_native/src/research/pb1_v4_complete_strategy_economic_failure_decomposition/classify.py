"""A priori path classes. Continuous MFE/MAE/realized remain source of truth."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_complete_strategy_economic_failure_decomposition import (
    CAPTURE_HALF,
    EXPAND_BPS,
    IMMEDIATE_MIN,
    SMALL_EDGE_BPS,
)


def classify_entry_path(row: dict[str, Any]) -> str:
    mfe = float(row.get("MFE_bps") or 0.0)
    mae = float(row.get("MAE_bps") or 0.0)
    realized = row.get("realized_gross_bps")
    rz = float(realized) if realized is not None else 0.0
    t_mae = int(row.get("time_to_MAE") or 10**9)
    capture = row.get("MFE_capture_ratio")
    if mae <= -float(SMALL_EDGE_BPS) and mfe < float(SMALL_EDGE_BPS) and t_mae <= int(IMMEDIATE_MIN):
        return "A_IMMEDIATE_WRONG_DIRECTION"
    if mfe >= float(EXPAND_BPS) and rz <= 0:
        return "C_FAVORABLE_THEN_FULL_GIVEBACK"
    if rz > 0 and mfe > 0 and capture is not None and float(capture) >= float(CAPTURE_HALF):
        return "D_FAVORABLE_AND_EXIT_CAPTURED"
    if mfe >= float(EXPAND_BPS) and rz > 0 and (capture is None or float(capture) < float(CAPTURE_HALF)):
        return "E_LATE_REVERSAL_AFTER_VALID_MOVE"
    if mfe < float(EXPAND_BPS):
        return "B_SMALL_EDGE_NEVER_EXPANDED"
    return "B_SMALL_EDGE_NEVER_EXPANDED"


def classify_asf(row: dict[str, Any], *, first_struct_t: str | None, delay_min: int | None) -> str:
    mfe = float(row.get("MFE_before_THESIS_LOST") or row.get("MFE_bps") or 0.0)
    mae = float(row.get("MAE_before_THESIS_LOST") or row.get("MAE_bps") or 0.0)
    t_mae = int(row.get("time_to_MAE") or 10**9)
    if mae <= -float(SMALL_EDGE_BPS) and mfe < float(SMALL_EDGE_BPS) and t_mae <= int(IMMEDIATE_MIN):
        return "ENTRY_ALREADY_WRONG"
    if mfe >= float(EXPAND_BPS) and delay_min is not None and int(delay_min) >= 10:
        return "VALID_ENTRY_BUT_DEATH_LATE"
    if first_struct_t and str(first_struct_t)[:5] == str(row.get("THESIS_LOST_AT") or "")[:5]:
        return "STRUCTURAL_FAILURE_TIMELY"
    if mfe < float(EXPAND_BPS) and mae > -2.0 * float(EXPAND_BPS):
        return "VALID_ENTRY_NO_FOLLOWTHROUGH"
    if mfe >= float(EXPAND_BPS) and float(row.get("realized_gross_bps") or 0) <= 0:
        return "VALID_ENTRY_BUT_DEATH_LATE"
    return "AMBIGUOUS"
