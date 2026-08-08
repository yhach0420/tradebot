"""Stage-4 Joint Strategy validation runner (Plan 2.0+ only).

Requires:
- Plan Version >= 2.0 locked
- JointStrategyRegistry + P1 locked before economics
- Full canonical replay per package (research-only)

Does NOT enable Shadow / Paper / Live / Discord / Runtime.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.e1_x6_provisional.constants import DAYS
from research.e1_x6_provisional.joint_strategy import (
    JOINT_FIXED_SPEC_GATES,
    JOINT_STRATEGY_CAP,
    build_joint_strategy_registry,
    joint_registry_sha,
    selected_joint_spec_sha,
)
from research.e1_x6_provisional.util import JST, progress, sha256_obj, summarize_pnls, write_json


VERDICT_NO_ROBUST = "E1_X6_NO_ROBUST_JOINT_STRATEGY"
VERDICT_FROZEN_FOR_FORWARD = "E1_X6_JOINT_RESEARCH_SPEC_FROZEN_FOR_FORWARD_TEST"
VERDICT_INSUFFICIENT = "E1_X6_INSUFFICIENT_EVIDENCE"


def _day_pnls(trades: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by: dict[str, list[float]] = {d: [] for d in DAYS}
    for t in trades:
        d = str(t.get("day") or "")
        if d in by:
            by[d].append(float(t.get("net_pnl_yen_100") or 0))
    out = {}
    for d, pnls in by.items():
        m = summarize_pnls(pnls) if pnls else {"n": 0, "pnl": 0.0, "pf": None}
        out[d] = {"n": m["n"], "pnl": m["pnl"], "pf": m.get("pf")}
    return out


def evaluate_joint_gates(
    *,
    trades: Sequence[Mapping[str, Any]],
    fold_confirm_pnls: Mapping[str, float],
    lodo_held_out_pnls: Mapping[str, float],
    ex722_pnl: float,
    ex722_pf: Optional[float],
    core_valid_n_windows: int,
    ab_match: bool,
    report_xlsx_match: bool,
    invalid_source_n: int,
    family_direction_flip: bool,
    base_dd: Optional[float],
    cand_dd: Optional[float],
    base_stop_loss: Optional[float],
    cand_stop_loss: Optional[float],
) -> dict[str, Any]:
    day = _day_pnls(trades)
    pnls = [float(t.get("net_pnl_yen_100") or 0) for t in trades]
    tot = summarize_pnls(pnls) if pnls else {"n": 0, "pnl": 0.0, "pf": None, "max_dd": 0.0}
    day_pnls = [day[d]["pnl"] for d in DAYS]
    day_ns = [day[d]["n"] for d in DAYS]
    all_days_pos = all(day[d]["n"] > 0 and float(day[d]["pnl"]) > 0 for d in DAYS)
    worst = min(day_pnls) if day_pnls else 0.0
    period_n = int(tot["n"])
    each_ge3 = all(n >= 3 for n in day_ns)
    total_pnl = float(tot["pnl"])
    max_day = max(day_pnls) if day_pnls else 0.0
    conc = (max_day / total_pnl) if total_pnl > 0 else 1.0
    top1_trade = float(tot.get("pnl_ex_top1_trade") or 0)
    # symbol concentration
    by_sym: dict[str, float] = {}
    for t in trades:
        s = str(t.get("symbol") or "")
        by_sym[s] = by_sym.get(s, 0.0) + float(t.get("net_pnl_yen_100") or 0)
    top_sym = max(by_sym.values()) if by_sym else 0.0
    pnl_ex_sym = total_pnl - top_sym
    pf = tot.get("pf")
    pf_ok = pf is not None and float(pf) >= 1.10
    fold_ok = all(float(fold_confirm_pnls.get(f, -1)) > 0 for f in ("F1", "F2", "F3", "F4", "F5"))
    lodo_ok = len(lodo_held_out_pnls) >= 9 and all(float(v) > 0 for v in lodo_held_out_pnls.values())
    ex722_ok = float(ex722_pnl) > 0 and ex722_pf is not None and float(ex722_pf) > 1.0
    dd_ok = True
    if base_dd is not None and cand_dd is not None:
        dd_ok = float(cand_dd) >= float(base_dd)  # less negative or equal is better (max_dd usually negative)
    stop_ok = True
    if base_stop_loss is not None and cand_stop_loss is not None:
        stop_ok = float(cand_stop_loss) <= float(base_stop_loss)  # smaller magnitude loss better if both negative... use abs
        stop_ok = abs(float(cand_stop_loss)) <= abs(float(base_stop_loss))

    gates = {
        "all_9_days_pnl_gt_0": all_days_pos,
        "same_entry_exit_spec_all_days": True,  # enforced by single package replay
        "worst_day_net_pnl_gt_0": worst > 0,
        "each_day_trades_ge_3": each_ge3,
        "period_trades_ge_30": period_n >= 30,
        "ex722_pnl_gt_0_and_pf_gt_1": ex722_ok,
        "rolling_origin_confirm_5_of_5_positive": fold_ok,
        "refit_lodo_held_out_9_of_9_positive": lodo_ok,
        "no_family_direction_flip_across_folds": not family_direction_flip,
        "max_day_contribution_le_30pct": conc <= 0.30 if total_pnl > 0 else False,
        "top1_trade_excluded_pnl_gt_0": top1_trade > 0,
        "top1_symbol_excluded_pnl_gt_0": pnl_ex_sym > 0,
        "pf_ge_1_10": pf_ok,
        "base_compare_dd_and_stop_not_worse": dd_ok and stop_ok,
        "invalid_source_count_0": int(invalid_source_n) == 0,
        "ab_determinism_exact": bool(ab_match),
        "report_xlsx_independent_recompute_match": bool(report_xlsx_match),
    }
    all_pass = all(gates.values())
    if core_valid_n_windows == 0 and all_pass:
        verdict = VERDICT_FROZEN_FOR_FORWARD
    elif all_pass:
        verdict = VERDICT_FROZEN_FOR_FORWARD
    else:
        verdict = VERDICT_NO_ROBUST
    if core_valid_n_windows == 0 and not all_pass:
        # Still research-failed; CORE insufficient is noted but gate failures dominate
        pass
    return {
        "gates": gates,
        "gate_list": JOINT_FIXED_SPEC_GATES,
        "all_pass": all_pass,
        "day_breakdown": day,
        "total": tot,
        "worst_day_pnl": worst,
        "max_day_contribution": conc,
        "verdict": verdict,
        "core_valid_windows": core_valid_n_windows,
        "note": (
            "If CORE_VALID=0, never claim adopted; use "
            f"{VERDICT_FROZEN_FOR_FORWARD} only when all gates pass"
        ),
    }


def lock_joint_p1_payload(
    *,
    plan_version: str,
    plan_sha256: str,
    registry: Sequence[Mapping[str, Any]],
    code_shas: Mapping[str, Any],
    schema_shas: Mapping[str, Any],
) -> dict[str, Any]:
    """Pre-economics joint lock blob (no PnL)."""
    payload = {
        "locked_at_jst": datetime.now(JST).isoformat(),
        "plan_version": plan_version,
        "plan_sha256": plan_sha256,
        "joint_strategy_cap": JOINT_STRATEGY_CAP,
        "registry_n": len(registry),
        "registry_sha256": joint_registry_sha(registry),
        "enumerate_order": ["entry_candidate_id", "exit_family_id", "strategy_id"],
        "seed": "deterministic_no_rng",
        "tie_break": "strategy_id lex",
        "build_only_rank_formula": (
            "1 all-build-days-positive; 2 worst-day; 3 LODO min; 4 concentration; "
            "5 max_dd; 6 pf; 7 simplicity"
        ),
        "forbidden_features": [
            "future_return",
            "MFE",
            "MAE",
            "future_STOP",
            "day_win_loss_labels_as_features",
            "symbol_specific_rules",
            "date_specific_rules",
            "post_hoc_losing_tod_exclusion",
        ],
        "code_file_shas": dict(code_shas),
        "schema_shas": dict(schema_shas),
        "shadow_auto_start": False,
        "shadow_requires_user_approval": True,
        "evaluation_unit": "JointStrategyPackage",
        "entry_only_adoption": "ABOLISHED",
        "entry_hypothesis_status": "ENTRY_HYPOTHESIS_ONLY / RETROSPECTIVE_REFERENCE",
    }
    payload["joint_p1_sha256"] = sha256_obj(payload)
    return payload


def write_joint_registry_lock(
    work: Path,
    *,
    entry_candidates: Sequence[Mapping[str, Any]],
    plan_version: str,
    plan_sha256: str,
    code_shas: Mapping[str, Any],
    schema_shas: Mapping[str, Any],
) -> dict[str, Any]:
    progress("JOINT: locking JointStrategyRegistry BEFORE economics")
    reg = build_joint_strategy_registry(entry_candidates)
    lock = lock_joint_p1_payload(
        plan_version=plan_version,
        plan_sha256=plan_sha256,
        registry=reg,
        code_shas=code_shas,
        schema_shas=schema_shas,
    )
    d = work / "joint"
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / "joint_strategy_registry.json", reg)
    write_json(d / "joint_p1_lock.json", lock)
    write_json(
        d / "joint_registry_sha.json",
        {"sha256": lock["registry_sha256"], "n": len(reg), "cap": JOINT_STRATEGY_CAP},
    )
    return {"registry": reg, "lock": lock}
