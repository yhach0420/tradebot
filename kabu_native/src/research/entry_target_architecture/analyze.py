"""Integrity, target aggregation, A-J gates, CASE A-E. No nested selector. No cherry-pick."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_panel_exact_reconciliation.analyze import causality_from_snaps
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.entry_target_architecture import (
    CONSENSUS_POS_DENOM,
    CONSENSUS_POS_MIN,
    POS_SPEC_DENOM,
    POS_SPEC_MIN,
    TARGET_IDS,
    TARGET_KEYS,
)


def cohort_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    common = [r for r in rows if r.get("common_cohort")]
    common_exec = [r for r in common if r.get("executable_at_t0")]
    native = {}
    for tid, key in TARGET_KEYS.items():
        if tid == "T4_EARLY_120":
            n = sum(1 for r in rows if r.get("executable_at_t0") and _f(r.get("T4_NATIVE")) is not None)
        else:
            n = sum(1 for r in rows if r.get("executable_at_t0") and _f(r.get(key)) is not None)
        native[f"{tid}_NATIVE_EXECUTABLE_N"] = n
        native[f"{tid}_NATIVE_NOTE"] = "Diagnostic only. Not used for Target pass/fail."
    return {
        "HARVEST_ROWS": len(rows),
        "COMMON_TARGET_COHORT_N": len(common),
        "COMMON_TARGET_COHORT_EXECUTABLE_N": len(common_exec),
        **native,
    }


def path_integrity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cas = causality_from_snaps(rows)
    future_snap = int(cas.get("FUTURE_EVENT_USE_N") or 0)
    future_path = sum(1 for r in rows if r.get("future_event_use") or int(r.get("future_event_n") or 0) > 0)
    future_n = max(future_snap, future_path)
    unexpected = 0
    for r in rows:
        if not r.get("executable_at_t0"):
            continue
        if not r.get("tse_t0_cont") or not r.get("tse_t1_cont"):
            continue
        if _f(r.get("m0")) is None:
            continue
        if not r.get("path_complete"):
            unexpected += 1
    itayose_n = sum(1 for r in rows if r.get("used_itayose"))
    special_n = sum(1 for r in rows if r.get("used_special"))
    close_n = sum(1 for r in rows if r.get("closing_auction_endpoint"))
    carry_n = sum(1 for r in rows if r.get("session_carry"))
    ok = (
        future_n == 0
        and unexpected == 0
        and itayose_n == 0
        and special_n == 0
        and close_n == 0
        and carry_n == 0
    )
    return {
        "FUTURE_EVENT_USE_N": future_n,
        "BOARD_EVENT_AFTER_T0_N": cas.get("BOARD_EVENT_AFTER_T0_N"),
        "UNEXPECTED_TARGET_MISSING_N": unexpected,
        "ITAYOSE_CONTAMINATION_N": itayose_n,
        "SPECIAL_CONTAMINATION_N": special_n,
        "CLOSING_AUCTION_CONTAMINATION_N": close_n,
        "SESSION_CARRY_N": carry_n,
        "ok": ok,
    }


def _spec_row(body: dict[str, Any], tid: str) -> dict[str, Any]:
    m = (body.get("by_target") or {}).get(tid) or {}
    return {
        "target_id": tid,
        "spec_id": body.get("spec_id"),
        "feature_set": body.get("feature_set"),
        "normalization": body.get("normalization"),
        "alpha": body.get("alpha"),
        "n_features": body.get("n_features"),
        "outer_folds": m.get("outer_folds"),
        "MATCHED_RANKING_ROWS": m.get("MATCHED_RANKING_ROWS"),
        "MEAN_DAILY_SPEARMAN": m.get("MEAN_DAILY_SPEARMAN"),
        "TOP1_UPLIFT": m.get("TOP1_UPLIFT"),
        "TOP3_UPLIFT": m.get("TOP3_UPLIFT"),
        "TOP5_UPLIFT": m.get("TOP5_UPLIFT"),
        "CURRENT_TOP1_UPLIFT": m.get("CURRENT_TOP1_UPLIFT"),
        "CURRENT_TOP3_UPLIFT": m.get("CURRENT_TOP3_UPLIFT"),
        "CURRENT_TOP5_UPLIFT": m.get("CURRENT_TOP5_UPLIFT"),
        "TOP1_DELTA": m.get("TOP1_DELTA"),
        "TOP3_DELTA": m.get("TOP3_DELTA"),
        "TOP5_DELTA": m.get("TOP5_DELTA"),
        "TOP3_DELTA_MEAN": m.get("TOP3_DELTA_MEAN"),
        "TOP3_DELTA_MEDIAN": m.get("TOP3_DELTA_MEDIAN"),
        "TOP3_DELTA_POSITIVE_DAYS": m.get("TOP3_DELTA_POSITIVE_DAYS"),
        "TOP3_DELTA_NEGATIVE_DAYS": m.get("TOP3_DELTA_NEGATIVE_DAYS"),
        "TOP3_DELTA_EX_BEST_DAY": m.get("TOP3_DELTA_EX_BEST_DAY"),
        "TOP3_DELTA_EX_TOP3_DAYS": m.get("TOP3_DELTA_EX_TOP3_DAYS"),
        "days": m.get("days") or [],
    }


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def aggregate_target(spec_rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    t3_delta = [float(v) for r in spec_rows if (v := _f(r.get("TOP3_DELTA"))) is not None]
    t3_abs = [float(v) for r in spec_rows if (v := _f(r.get("TOP3_UPLIFT"))) is not None]
    t5_delta = [float(v) for r in spec_rows if (v := _f(r.get("TOP5_DELTA"))) is not None]
    sp = [float(v) for r in spec_rows if (v := _f(r.get("MEAN_DAILY_SPEARMAN"))) is not None]
    gt = 0
    pos_abs = 0
    for r in spec_rows:
        d = _f(r.get("TOP3_DELTA"))
        u = _f(r.get("TOP3_UPLIFT"))
        cur = _f(r.get("CURRENT_TOP3_UPLIFT"))
        if d is not None and float(d) > 0:
            gt += 1
        elif d is None and u is not None and cur is not None and float(u) > float(cur):
            gt += 1
        if u is not None and float(u) > 0:
            pos_abs += 1
    by_day: dict[str, list[float]] = {d: [] for d in days}
    for r in spec_rows:
        for rec in r.get("days") or []:
            dd = str(rec.get("date") or "")
            v = _f(rec.get("DELTA_TOP3_UPLIFT"))
            if dd in by_day and v is not None:
                by_day[dd].append(float(v))
    consensus_days = []
    consensus_vals = []
    for d in days:
        xs = by_day.get(d) or []
        if not xs:
            continue
        med = float(np.median(xs))
        consensus_days.append({"date": d, "CONSENSUS_DAILY_TOP3_DELTA": med, "n_specs": len(xs)})
        consensus_vals.append(med)
    cstats = delta_series_stats(consensus_vals)
    med_t3d = _median(t3_delta)
    med_t3a = _median(t3_abs)
    med_t5d = _median(t5_delta)
    med_sp = _median(sp)
    pos_c = int(cstats.get("positive_days") or 0)
    neg_c = int(cstats.get("negative_days") or 0)
    ex1 = cstats.get("ex_best_day")
    ex3 = cstats.get("ex_top3_days")
    gates = {
        "A_MEDIAN_SPEC_TOP3_DELTA_GT_0": bool(med_t3d is not None and med_t3d > 0),
        "B_MEDIAN_SPEC_TOP3_ABSOLUTE_UPLIFT_GT_0": bool(med_t3a is not None and med_t3a > 0),
        "C_TOP3_GT_CURRENT_SPEC_N": bool(gt >= POS_SPEC_MIN),
        "D_TOP3_POSITIVE_ABSOLUTE_SPEC_N": bool(pos_abs >= POS_SPEC_MIN),
        "E_MEDIAN_SPEC_TOP5_DELTA_GE_0": bool(med_t5d is not None and med_t5d >= 0),
        "F_MEDIAN_SPEC_SPEARMAN_GT_0": bool(med_sp is not None and med_sp > 0),
        "G_CONSENSUS_POSITIVE_DAYS": bool(pos_c >= CONSENSUS_POS_MIN),
        "H_CONSENSUS_POS_GT_NEG": bool(pos_c > neg_c),
        "I_CONSENSUS_EX_BEST_DAY_GT_0": bool(ex1 is not None and float(ex1) > 0),
        "J_CONSENSUS_EX_TOP3_DAYS_GE_0": bool(ex3 is not None and float(ex3) >= 0),
    }
    fail = [k for k, v in gates.items() if not v]
    return {
        "SPEC_N": len(spec_rows),
        "MEDIAN_SPEC_TOP3_DELTA": med_t3d,
        "MEDIAN_SPEC_TOP3_ABSOLUTE_UPLIFT": med_t3a,
        "MEDIAN_SPEC_TOP5_DELTA": med_t5d,
        "MEDIAN_SPEC_SPEARMAN": med_sp,
        "TOP3_GT_CURRENT_SPEC_N": gt,
        "TOP3_POSITIVE_ABSOLUTE_SPEC_N": pos_abs,
        "POS_SPEC_MIN": POS_SPEC_MIN,
        "POS_SPEC_DENOM": POS_SPEC_DENOM,
        "CONSENSUS_DAILY_TOP3_DELTA": consensus_days,
        "CONSENSUS_POSITIVE_DAYS": pos_c,
        "CONSENSUS_NEGATIVE_DAYS": neg_c,
        "CONSENSUS_EX_BEST_DAY": ex1,
        "CONSENSUS_EX_TOP3_DAYS": ex3,
        "CONSENSUS_POS_MIN": CONSENSUS_POS_MIN,
        "CONSENSUS_POS_DENOM": CONSENSUS_POS_DENOM,
        "gates": gates,
        "gate_fail": fail,
        "TARGET_PASS": len(fail) == 0,
    }


def decide(pass_ids: list[str]) -> dict[str, Any]:
    s = set(pass_ids)
    n = len(pass_ids)
    t0 = "T0_TERMINAL_600" in s
    t2 = "T2_DOWNSIDE_AVOID_600" in s
    arch = [x for x in ("T1_MFE_600", "T3_PATH_QUALITY_600", "T4_EARLY_120") if x in s]
    if n == 0:
        return {
            "CASE": "A",
            "VERDICT": "EXISTING_LINEAR_ENTRY_TARGET_NOT_FOUND",
            "NEXT_RESEARCH": "CONSTRAINED_MODEL_ARCHITECTURE_PROBE",
            "PRIMARY_FINDING": (
                "Existing Canonical features × Ridge do not yield a day-robust target architecture "
                "on the frozen 27-spec grid. This is not a claim that features have no signal."
            ),
            "note": "0 learnable targets. STOP. No new model this run.",
        }
    if n == 1 and t0:
        return {
            "CASE": "B",
            "VERDICT": "TERMINAL_RETURN_TARGET_SURVIVES",
            "NEXT_RESEARCH": "MODEL_ARCHITECTURE_REDESIGN_ON_T0",
            "PRIMARY_FINDING": "Only the existing terminal 600s return target is day-robust under Ridge.",
            "note": "T0 only. STOP. No model family search this run.",
        }
    if n == 1 and t2:
        return {
            "CASE": "D",
            "VERDICT": "DOWNSIDE_RISK_PREDICTABILITY_FOUND",
            "NEXT_RESEARCH": "RISK_AWARE_ENTRY_ARCHITECTURE_DESIGN",
            "PRIMARY_FINDING": "RISK_PREDICTABILITY_FOUND. T2-only is not an alpha-target discovery.",
            "note": "T2 only. STOP. Do not call this an alpha target.",
        }
    if n == 1 and len(arch) == 1:
        return {
            "CASE": "C",
            "VERDICT": "ENTRY_TARGET_ARCHITECTURE_FOUND",
            "NEXT_RESEARCH": "TARGET_SPECIFIC_MODEL_DEVELOPMENT_PRECOMMIT",
            "PRIMARY_FINDING": f"Single non-terminal path/early target is learnable: {arch[0]}.",
            "note": "Single T1/T3/T4. STOP. Do not cherry-pick by raw uplift.",
        }
    return {
        "CASE": "E",
        "VERDICT": "MULTIPLE_ENTRY_TARGETS_LEARNABLE",
        "NEXT_RESEARCH": "TARGET_ARCHITECTURE_PRECOMMIT_SELECTION",
        "PRIMARY_FINDING": (
            "Multiple target structures pass A-J. This run does not select one by max uplift."
        ),
        "note": "2+ PASS. STOP. Do not narrow to one target this run.",
    }


def flatten_spec_rows(bodies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out = {tid: [] for tid in TARGET_IDS}
    for body in bodies:
        for tid in TARGET_IDS:
            out[tid].append(_spec_row(body, tid))
    return out
