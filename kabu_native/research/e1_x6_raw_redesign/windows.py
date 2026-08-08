"""Quality-valid windows, analysis_mask_R1 and censor policy (Phase A-R1 §4).

The mask is strategy-independent and frozen BEFORE Phase B. Every evaluation
point must satisfy (in addition to per-symbol NOT_EVALUABLE gates):
- continuous 300s feature lookback inside the window (no gap crossing);
- continuous EXIT observation range of 600s OR to the regular session close;
- no AM/PM boundary crossing; no source conflicts; valid quote.
ENTRY points that cannot observe the full 600s horizon (window truncated) are
pre-excluded as NOT_EVALUABLE_INCOMPLETE_EXIT_HORIZON — decided by window
geometry only, never by trade outcome.
"""
from __future__ import annotations

from typing import Any

from .store import sha256_obj

EXIT_HORIZON_SEC = 600.0
LOOKBACK_SEC = 300.0

CENSOR_POLICY = {
    "not_evaluable_incomplete_exit_horizon": (
        "ENTRY grid t is economically evaluable only if "
        "min(window_valid_end, session_close) - t >= 600s; else the grid is "
        "pre-excluded as NOT_EVALUABLE_INCOMPLETE_EXIT_HORIZON (geometry-only)"
    ),
    "same_mask_for_all_candidates": "all 24 candidates share the identical 600s-horizon mask (EXIT_A and EXIT_B populations identical)",
    "no_zeroing_incomplete": "non-completed trades are never converted to 0 yen",
    "no_silent_censor_drop": "censored trades are never silently removed from PnL",
    "separate_counters": "open/orphan/censored are stored as separate counts",
    "gate_fail_on_leftover": "any accepted ENTRY left non-completed at aggregation => gate FAIL",
}


def build_analysis_mask_r1(
    coverage_days: dict[str, Any],
    known_excluded: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the R1 analysis mask from window spans + historical exclusions."""
    rows: dict[str, Any] = {}
    for day, cov in coverage_days.items():
        for sk in ("AM", "PM"):
            w = dict(cov["windows"][sk])
            included = w["quality_class"] in ("FULL", "TRUNCATED")
            reason = ""
            ex = (known_excluded.get("windows") or {}).get(day, {}).get(sk)
            if ex is not None and not ex.get("included", True):
                included = False
                reason = ex.get("reason", "EXCLUDED_HISTORICAL_MASK")
            if w["quality_class"] == "NO_DATA":
                included = False
                reason = reason or "NO_DATA"
            # EXIT observation may legitimately end at the REGULAR session
            # close (forced close is observable). Only a TRUNCATED capture end
            # cuts the horizon: entries within 600s of a truncated end are
            # pre-excluded (NOT_EVALUABLE_INCOMPLETE_EXIT_HORIZON).
            entry_eval_end = None
            if included and w["valid_end_epoch"] is not None:
                if w["valid_end_epoch"] >= w["expected_end_epoch"] - 1e-9:
                    entry_eval_end = w["expected_end_epoch"]
                else:
                    entry_eval_end = w["valid_end_epoch"] - EXIT_HORIZON_SEC
            rows[f"{day}_{sk}"] = {
                **w,
                "included": included,
                "exclusion_reason": reason,
                "entry_evaluable_until_epoch": entry_eval_end,
            }
    body = {
        "rules": {
            "lookback_sec": LOOKBACK_SEC,
            "exit_horizon_sec": EXIT_HORIZON_SEC,
            "censor_policy": CENSOR_POLICY,
            "requirements": [
                "continuous 300s feature lookback (no gap crossing)",
                "continuous 600s (or to regular session close) EXIT observation",
                "no AM/PM boundary crossing",
                "no source conflicts",
                "valid quote at decision grid",
            ],
        },
        "windows": rows,
    }
    body["analysis_mask_id"] = "MASK_R1_" + sha256_obj(body)[:16]
    return body
