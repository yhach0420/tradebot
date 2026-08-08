"""Joint Strategy Package registry (Plan 2.0+) — research-only.

ENTRY+EXIT evaluated as one JointStrategyPackage. Do not open economics
until Plan Version >= 2.0 is locked and P1 precommit is complete.

Shadow / Paper / Live / Runtime are NOT started from this module.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from research.e1_x6_provisional.constants import CANDIDATE_CAP, PREDICTOR_FEATURES
from research.e1_x6_provisional.util import sha256_obj
from small_paper.e1_x5_forward_shadow import (
    GIVEBACK,
    MAX_HOLD_SEC,
    STOP_BPS,
    TARGET_BPS,
    TRAIL_ARM_BPS,
)


# EXIT families pre-registered (as-of features only; no future return / MFE / MAE)
EXIT_FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "exit_family_id": "X5_FROZEN",
        "invalidation": "SCORE_COLLAPSE_BELOW_ENTRY_GAP",
        "initial_stop_bps": STOP_BPS,
        "target_bps": TARGET_BPS,
        "trailing": {"arm_bps": TRAIL_ARM_BPS, "giveback": GIVEBACK},
        "no_progress": {"enabled": True, "source": "e1_x5_forward_shadow"},
        "max_hold_sec": MAX_HOLD_SEC,
        "session_end": "WINDOW_CENSORED",
        "reentry": "FORBIDDEN_SAME_EPISODE",
        "execution": "entry_ask / exit_bid",
        "lot": 100,
        "cost_bps_once": 5.0,
        "cap": 5,
        "pyramiding": False,
    },
    {
        "exit_family_id": "X5_TIGHTER_STOP",
        "invalidation": "SCORE_COLLAPSE_BELOW_ENTRY_GAP",
        "initial_stop_bps": float(STOP_BPS) * 0.75,
        "target_bps": TARGET_BPS,
        "trailing": {"arm_bps": TRAIL_ARM_BPS, "giveback": GIVEBACK},
        "no_progress": {"enabled": True, "source": "e1_x5_forward_shadow"},
        "max_hold_sec": MAX_HOLD_SEC,
        "session_end": "WINDOW_CENSORED",
        "reentry": "FORBIDDEN_SAME_EPISODE",
        "execution": "entry_ask / exit_bid",
        "lot": 100,
        "cost_bps_once": 5.0,
        "cap": 5,
        "pyramiding": False,
    },
    {
        "exit_family_id": "X5_WIDER_TARGET",
        "invalidation": "SCORE_COLLAPSE_BELOW_ENTRY_GAP",
        "initial_stop_bps": STOP_BPS,
        "target_bps": float(TARGET_BPS) * 1.25,
        "trailing": {"arm_bps": TRAIL_ARM_BPS, "giveback": GIVEBACK},
        "no_progress": {"enabled": True, "source": "e1_x5_forward_shadow"},
        "max_hold_sec": MAX_HOLD_SEC,
        "session_end": "WINDOW_CENSORED",
        "reentry": "FORBIDDEN_SAME_EPISODE",
        "execution": "entry_ask / exit_bid",
        "lot": 100,
        "cost_bps_once": 5.0,
        "cap": 5,
        "pyramiding": False,
    },
    {
        "exit_family_id": "X5_SHORTER_HOLD",
        "invalidation": "SCORE_COLLAPSE_BELOW_ENTRY_GAP",
        "initial_stop_bps": STOP_BPS,
        "target_bps": TARGET_BPS,
        "trailing": {"arm_bps": TRAIL_ARM_BPS, "giveback": GIVEBACK},
        "no_progress": {"enabled": True, "source": "e1_x5_forward_shadow"},
        "max_hold_sec": int(MAX_HOLD_SEC * 0.5),
        "session_end": "WINDOW_CENSORED",
        "reentry": "FORBIDDEN_SAME_EPISODE",
        "execution": "entry_ask / exit_bid",
        "lot": 100,
        "cost_bps_once": 5.0,
        "cap": 5,
        "pyramiding": False,
    },
)

JOINT_STRATEGY_CAP = CANDIDATE_CAP
JOINT_BUILD_RANK_FORMULA = (
    "1) all build days pnl>0 count desc; 2) worst_day_pnl desc; "
    "3) rolling/LODO min pnl desc; 4) day concentration asc; "
    "5) max_dd asc (less negative); 6) pf desc; 7) simplicity (fewer params) asc; "
    "8) strategy_id lex"
)

JOINT_FIXED_SPEC_GATES = [
    "all_9_days_pnl_gt_0",
    "same_entry_exit_spec_all_days",
    "worst_day_net_pnl_gt_0",
    "each_day_trades_ge_3",
    "period_trades_ge_30",
    "ex722_pnl_gt_0_and_pf_gt_1",
    "rolling_origin_confirm_5_of_5_positive",
    "refit_lodo_held_out_9_of_9_positive",
    "no_family_direction_flip_across_folds",
    "max_day_contribution_le_30pct",
    "top1_trade_excluded_pnl_gt_0",
    "top1_symbol_excluded_pnl_gt_0",
    "pf_ge_1_10",
    "base_compare_dd_and_stop_not_worse",
    "invalid_source_count_0",
    "ab_determinism_exact",
    "report_xlsx_independent_recompute_match",
]


def strategy_id(entry: Mapping[str, Any], exit_family: Mapping[str, Any]) -> str:
    eid = str(entry.get("candidate_id") or entry.get("entry_id") or "")
    xid = str(exit_family.get("exit_family_id") or "")
    return f"JS|{eid}|{xid}"


def build_joint_strategy_registry(
    entry_candidates: Sequence[Mapping[str, Any]],
    *,
    exit_families: Sequence[Mapping[str, Any]] = EXIT_FAMILIES,
    cap: int = JOINT_STRATEGY_CAP,
) -> list[dict[str, Any]]:
    """Deterministic enumerate ENTRY×EXIT then cap. No economics opened here."""
    packages: list[dict[str, Any]] = []
    for entry in entry_candidates:
        for xf in exit_families:
            sid = strategy_id(entry, xf)
            packages.append(
                {
                    "strategy_id": sid,
                    "entry_candidate_id": entry.get("candidate_id"),
                    "entry_family": entry.get("family"),
                    "entry_features": entry.get("features"),
                    "entry_direction": entry.get("direction"),
                    "entry_thresholds": entry.get("thresholds"),
                    "entry_hypothesis": {
                        "horizon_sec_assumed": 300,
                        "predictors": list(PREDICTOR_FEATURES),
                    },
                    "exit_family_id": xf.get("exit_family_id"),
                    "exit_spec": dict(xf),
                    "lot": 100,
                    "cost_bps_once": 5.0,
                    "cap": 5,
                    "pyramiding": False,
                    "reentry": "FORBIDDEN_SAME_EPISODE",
                    "session_end": "WINDOW_CENSORED",
                    "execution": "entry_ask / exit_bid",
                }
            )
    packages.sort(key=lambda p: str(p["strategy_id"]))
    capped = packages[:cap]
    for i, p in enumerate(capped):
        p["enumerate_rank"] = i
    return capped


def joint_registry_sha(registry: Sequence[Mapping[str, Any]]) -> str:
    return sha256_obj(list(registry))


def selected_joint_spec_sha(package: Mapping[str, Any]) -> str:
    """Separate namespace from full registry SHA."""
    return sha256_obj(
        {
            "strategy_id": package.get("strategy_id"),
            "entry_candidate_id": package.get("entry_candidate_id"),
            "exit_family_id": package.get("exit_family_id"),
            "exit_spec": package.get("exit_spec"),
        }
    )
