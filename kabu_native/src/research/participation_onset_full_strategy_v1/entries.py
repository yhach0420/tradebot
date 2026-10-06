"""Frozen E1_X14 T1 activity-onset. No above-VWAP. No threshold retune. No extra filters."""
from __future__ import annotations

import math
from typing import Any, Optional

from research.dynamic_anchor_p2_0b import MIN_RS_UNIVERSE, VOLUME_PERCENTILE_MIN
from research.dynamic_anchor_p2_0b.contract import t1_raw
from research.participation_onset_full_strategy_v1 import VOLUME_PERCENTILE_THRESHOLD

ENTRY_RULE_TEXT = (
    "X14 10s causal grid AM. CURRENT t1_raw True: feature_status==OK AND relative_status==OK "
    f"AND rs_universe_n>={MIN_RS_UNIVERSE} AND finite(volume_percentile_60s) "
    f"AND volume_percentile_60s >= {VOLUME_PERCENTILE_THRESHOLD}. "
    "PREVIOUS grid must exist and be evaluable with volume_percentile_60s < threshold. "
    "previous missing/not-evaluable => FALSE. ENTRY = FALSE->TRUE onset. "
    "No VWAP/price/RCI/EMA/BB/symbol/spread filter."
)


def activity_evaluable(row: dict[str, Any]) -> bool:
    if row.get("feature_status") != "OK":
        return False
    if row.get("relative_status") != "OK":
        return False
    try:
        n = int(row.get("rs_universe_n") or 0)
    except (TypeError, ValueError):
        return False
    if n < int(MIN_RS_UNIVERSE):
        return False
    v = row.get("volume_percentile_60s")
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(x)


def previous_low(row: dict[str, Any]) -> bool:
    if not activity_evaluable(row):
        return False
    return float(row["volume_percentile_60s"]) < float(VOLUME_PERCENTILE_THRESHOLD)


def onset_rows(grid_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-symbol AM FALSE->TRUE. Previous missing => no fire. No imputation."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in grid_rows:
        if str(r.get("session") or "") != "AM":
            continue
        by.setdefault(str(r["symbol"]), []).append(r)
    out: list[dict[str, Any]] = []
    for _sym, rows in by.items():
        rows = sorted(rows, key=lambda x: float(x["grid_epoch"]))
        prev: Optional[dict[str, Any]] = None
        for r in rows:
            if prev is not None and t1_raw(r) and previous_low(prev):
                rec = dict(r)
                rec["signal_t0"] = float(r["grid_epoch"])
                rec["prev_grid_epoch"] = float(prev["grid_epoch"])
                rec["prev_volume_percentile_60s"] = prev.get("volume_percentile_60s")
                rec["current_raw"] = True
                rec["previous_raw"] = False
                out.append(rec)
            prev = r
    out.sort(key=lambda r: (float(r["grid_epoch"]), str(r["symbol"])))
    return out


def compact_activity_grid(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if str(r.get("session") or "") != "AM":
            continue
        out.append(
            {
                "grid_epoch": float(r["grid_epoch"]),
                "evaluable": bool(activity_evaluable(r)),
                "volume_percentile_60s": r.get("volume_percentile_60s"),
            }
        )
    out.sort(key=lambda x: float(x["grid_epoch"]))
    return out


def already_executed_check() -> dict[str, Any]:
    rows = [
        {
            "study": "E1_X14",
            "identity": "volume_percentile_60s DESIGN q80 component / clustered 10s labels, not Full Strategy",
            "execution": "none (signal audit)",
            "exit": "none",
            "portfolio": "none",
            "exact_duplicate": False,
        },
        {
            "study": "E1_X15_RPFE",
            "identity": "VWAP+rebound+activity combo over RPFE episodes",
            "execution": "not this Ask+Z3/ZP/ZH unit",
            "exit": "none as this strategy",
            "portfolio": "not CAP=5 occupancy Full Causal",
            "exact_duplicate": False,
        },
        {
            "study": "P2-0",
            "identity": "T1 trigger evidence; EXISTING_10MIN_CONFIRMATION_FOUND=false; SELECTED_CONFIRMATION=NONE",
            "execution": "Dynamic trades/PnL not implemented",
            "exit": "none",
            "portfolio": "none",
            "exact_duplicate": False,
        },
        {
            "study": "Simple-Tech / E4_X2_Z3",
            "identity": "1m bar patterns; E4 same-bar VWAP reclaim; passive queue fill",
            "execution": "passive floor(mid) 5s",
            "exit": "Z3 inside bar-pattern family",
            "portfolio": "CAP=5 but different ENTRY",
            "exact_duplicate": False,
        },
        {
            "study": "RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1",
            "identity": "next-bar VWAP reclaim library R1/R2/R3",
            "execution": "Ask1 / Ask1<=mid watch",
            "exit": "Z3 only",
            "portfolio": "CAP=5",
            "exact_duplicate": False,
        },
    ]
    return {
        "duplicate": False,
        "DUPLICATE_FULL_STRATEGY": False,
        "reason": (
            "T1 activity onset is previously researched, but T1 + real Ask1 + Technical EXIT "
            "+ CAP=5 occupancy + slot release was not run as one Full Causal strategy. "
            "P2-0 selected confirmation NONE and did not harvest Dynamic trades."
        ),
        "comparisons": rows,
        "VOLUME_PERCENTILE_MIN": float(VOLUME_PERCENTILE_MIN),
        "VOLUME_PERCENTILE_THRESHOLD": float(VOLUME_PERCENTILE_THRESHOLD),
        "threshold_retuned": False,
        "ABOVE_VWAP_ENTRY_FILTER": False,
    }
