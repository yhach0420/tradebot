"""W1 parity, wait geometry, incremental fills, quality, CASE A-D."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_execution_feasibility.analyze import labeled_rows
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.execution_aware_target_feasibility.analyze import fillable_geometry
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment import (
    EXTENDED_IDS,
    PARITY_ABS_TOL,
    RATE_MIN,
    SURVIVAL_LOW_MAX,
    W1_EXPECTED,
)


def _close(a: Any, b: Any, tol: float) -> bool:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return False
    return abs(float(x) - float(y)) <= float(tol)


def _rate(n: int, d: int) -> Optional[float]:
    if int(d) <= 0:
        return None
    return float(n) / float(d)


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.mean(xs))


def _pct(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    return float(np.quantile(np.asarray(xs, dtype=float), q))


def wait_body(r: dict[str, Any], wid: str) -> dict[str, Any]:
    return dict((r.get("waits") or {}).get(wid) or {})


def overlay_wait(rows: list[dict[str, Any]], wid: str) -> list[dict[str, Any]]:
    out = []
    for r in labeled_rows(rows):
        rec = dict(r)
        w = wait_body(r, wid)
        rec["WOULD_FILL"] = bool(w.get("WOULD_FILL"))
        rec["nonfill_class"] = w.get("nonfill_class")
        rec["POSTFILL_MFE_600"] = w.get("POSTFILL_MFE_600")
        rec["POSTFILL_DOWNSIDE_AVOID_600"] = w.get("POSTFILL_DOWNSIDE_AVOID_600")
        rec["fill_t"] = w.get("fill_t")
        rec["TIME_TO_FILL_SEC"] = w.get("TIME_TO_FILL_SEC")
        out.append(rec)
    return out


def mechanism(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    f0 = f1 = f2 = f3 = other = 0
    for r in lab:
        if r.get("WOULD_FILL"):
            f2 += 1
            continue
        klass = str(r.get("nonfill_class") or "OTHER")
        if klass == "NO_VALID_CONTINUOUS_BOARD":
            f0 += 1
        elif klass == "NO_ASK_CROSS_WITHIN_WAIT":
            f1 += 1
        elif klass == "BOARD_BECAME_NONEXECUTABLE":
            f3 += 1
        else:
            other += 1
    valid = f1 + f2 + f3
    return {
        "F0_NO_VALID_BOARD_N": f0,
        "F1_NO_ASK_CROSS_N": f1,
        "F2_ASK_CROSS_FILL_N": f2,
        "F3_BECAME_NONEXEC_N": f3,
        "F_OTHER_N": other,
        "VALID_BOARD_WITHIN_WAIT_N": valid,
        "VALID_BOARD_WITHIN_WAIT_RATE": _rate(valid, len(lab)),
        "ASK_CROSS_GIVEN_VALID_BOARD_N": f2,
        "ASK_CROSS_GIVEN_VALID_BOARD_RATE": _rate(f2, f1 + f2),
        "ALL_WOULD_FILL_N": f2,
        "ALL_WOULD_FILL_RATE": _rate(f2, len(lab)),
        "ALL_CANDIDATE_N": len(lab),
    }


def joint_access(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    jp_fill = sum(1 for r in lab if r.get("WOULD_FILL") and int(r.get("joint_label") or 0) == 1)
    by = group_cohorts(lab)
    n_coh = 0
    n_avail = 0
    for _k, grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        n_coh += 1
        if any(r.get("WOULD_FILL") and int(r.get("joint_label") or 0) == 1 for r in grp):
            n_avail += 1
    return {
        "JOINT_POSITIVE_WOULD_FILL_N": jp_fill,
        "FILLABLE_JOINT_AVAILABLE_COHORT_N": n_avail,
        "FILLABLE_JOINT_AVAILABLE_RATE": _rate(n_avail, n_coh),
    }


def survival(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled_jp = [
        r
        for r in labeled_rows(rows)
        if r.get("WOULD_FILL") and int(r.get("joint_label") or 0) == 1
    ]
    usable = 0
    survive = 0
    for r in filled_jp:
        mfe = _f(r.get("POSTFILL_MFE_600"))
        dn = _f(r.get("POSTFILL_DOWNSIDE_AVOID_600"))
        cu = _f(r.get("CURRENT_U_BASELINE"))
        cd = _f(r.get("CURRENT_D_BASELINE"))
        if mfe is None or dn is None or cu is None or cd is None:
            continue
        usable += 1
        if float(mfe) > float(cu) and float(dn) > float(cd):
            survive += 1
    return {
        "JOINT_LABEL_SURVIVAL_AFTER_FILL_N": survive,
        "JOINT_LABEL_SURVIVAL_AFTER_FILL_DENOM": usable,
        "JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE": _rate(survive, usable),
    }


def postfill_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in labeled_rows(rows) if r.get("WOULD_FILL")]
    mfe = [float(v) for r in filled if (v := _f(r.get("POSTFILL_MFE_600"))) is not None]
    dn = [float(v) for r in filled if (v := _f(r.get("POSTFILL_DOWNSIDE_AVOID_600"))) is not None]
    return {
        "N": len(filled),
        "POSTFILL_MFE_N": len(mfe),
        "POSTFILL_MFE_MEDIAN": _median(mfe),
        "POSTFILL_MFE_MEAN": _mean(mfe),
        "POSTFILL_DOWNSIDE_N": len(dn),
        "POSTFILL_DOWNSIDE_MEDIAN": _median(dn),
        "POSTFILL_DOWNSIDE_MEAN": _mean(dn),
    }


def wait_metrics(rows: list[dict[str, Any]], wid: str) -> dict[str, Any]:
    over = overlay_wait(rows, wid)
    geom = fillable_geometry(over)
    mech = mechanism(over)
    joint = joint_access(over)
    surv = survival(over)
    pf = postfill_pack(over)
    jp_n = sum(1 for r in over if r.get("WOULD_FILL") and int(r.get("joint_label") or 0) == 1)
    fill_n = int(pf.get("N") or 0)
    return {
        "wait_id": wid,
        **geom,
        **mech,
        **joint,
        **surv,
        "POSTFILL_FILL_N": fill_n,
        "POSTFILL_MFE_N": pf.get("POSTFILL_MFE_N"),
        "POSTFILL_MFE_MEDIAN": pf.get("POSTFILL_MFE_MEDIAN"),
        "POSTFILL_MFE_MEAN": pf.get("POSTFILL_MFE_MEAN"),
        "POSTFILL_DOWNSIDE_N": pf.get("POSTFILL_DOWNSIDE_N"),
        "POSTFILL_DOWNSIDE_MEDIAN": pf.get("POSTFILL_DOWNSIDE_MEDIAN"),
        "POSTFILL_DOWNSIDE_MEAN": pf.get("POSTFILL_DOWNSIDE_MEAN"),
        "OLD_JOINT_POSITIVE_RATE_AMONG_FILLS": _rate(jp_n, fill_n),
    }


def w1_parity(w1: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "ALL_WOULD_FILL_N": int(w1.get("ALL_WOULD_FILL_N") or -1) == int(W1_EXPECTED["ALL_WOULD_FILL_N"]),
        "FILLABLE_ANY_COHORT_N": int(w1.get("FILLABLE_ANY_COHORT_N") or -1)
        == int(W1_EXPECTED["FILLABLE_ANY_COHORT_N"]),
        "FILLABLE_ANY_COHORT_RATE": _close(
            w1.get("FILLABLE_ANY_COHORT_RATE"), W1_EXPECTED["FILLABLE_ANY_COHORT_RATE"], PARITY_ABS_TOL
        ),
        "MULTI_FILLABLE_COHORT_N": int(w1.get("MULTI_FILLABLE_COHORT_N") or -1)
        == int(W1_EXPECTED["MULTI_FILLABLE_COHORT_N"]),
        "MULTI_FILLABLE_COHORT_RATE": _close(
            w1.get("MULTI_FILLABLE_COHORT_RATE"), W1_EXPECTED["MULTI_FILLABLE_COHORT_RATE"], PARITY_ABS_TOL
        ),
        "VALID_BOARD_WITHIN_WAIT_RATE": _close(
            w1.get("VALID_BOARD_WITHIN_WAIT_RATE"),
            W1_EXPECTED["VALID_BOARD_WITHIN_WAIT_RATE"],
            PARITY_ABS_TOL,
        ),
        "ASK_CROSS_GIVEN_VALID_BOARD_RATE": _close(
            w1.get("ASK_CROSS_GIVEN_VALID_BOARD_RATE"),
            W1_EXPECTED["ASK_CROSS_GIVEN_VALID_BOARD_RATE"],
            PARITY_ABS_TOL,
        ),
    }
    return {
        "BASE_PARITY": all(checks.values()),
        "checks": checks,
        "expected": dict(W1_EXPECTED),
        "observed": {k: w1.get(k) for k in W1_EXPECTED},
    }


def incremental(rows: list[dict[str, Any]], wid: str) -> dict[str, Any]:
    lab = labeled_rows(rows)
    new = []
    from_board = 0
    from_cross = 0
    from_other = 0
    for r in lab:
        w1 = wait_body(r, "W1")
        w = wait_body(r, wid)
        if w.get("WOULD_FILL") and not w1.get("WOULD_FILL"):
            rec = dict(r)
            rec["WOULD_FILL"] = True
            rec["POSTFILL_MFE_600"] = w.get("POSTFILL_MFE_600")
            rec["POSTFILL_DOWNSIDE_AVOID_600"] = w.get("POSTFILL_DOWNSIDE_AVOID_600")
            rec["nonfill_class_w1"] = w1.get("nonfill_class")
            new.append(rec)
            klass = str(w1.get("nonfill_class") or "OTHER")
            if klass == "NO_VALID_CONTINUOUS_BOARD":
                from_board += 1
            elif klass == "NO_ASK_CROSS_WITHIN_WAIT":
                from_cross += 1
            else:
                from_other += 1
    pf = postfill_pack(new)
    jp = sum(1 for r in new if int(r.get("joint_label") or 0) == 1)
    return {
        "wait_id": wid,
        "NEW_FILL_VS_W1_N": len(new),
        "NEW_FILL_FROM_NO_VALID_BOARD_N": from_board,
        "NEW_FILL_FROM_PRIOR_NO_ASK_CROSS_N": from_cross,
        "NEW_FILL_FROM_OTHER_N": from_other,
        "OLD_JOINT_POSITIVE_N": jp,
        "OLD_JOINT_POSITIVE_RATE": _rate(jp, len(new)),
        "POSTFILL_MFE_MEDIAN": pf.get("POSTFILL_MFE_MEDIAN"),
        "POSTFILL_MFE_MEAN": pf.get("POSTFILL_MFE_MEAN"),
        "POSTFILL_DOWNSIDE_MEDIAN": pf.get("POSTFILL_DOWNSIDE_MEDIAN"),
        "POSTFILL_DOWNSIDE_MEAN": pf.get("POSTFILL_DOWNSIDE_MEAN"),
        "POSTFILL_MFE_N": pf.get("POSTFILL_MFE_N"),
        "POSTFILL_DOWNSIDE_N": pf.get("POSTFILL_DOWNSIDE_N"),
    }


def time_to_fill(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = []
    buckets = {"0-1s": 0, "1-2s": 0, "2-5s": 0, "5-10s": 0}
    for r in labeled_rows(rows):
        w = wait_body(r, "W10")
        if not w.get("WOULD_FILL"):
            continue
        ttf = _f(w.get("TIME_TO_FILL_SEC"))
        if ttf is None:
            continue
        xs.append(float(ttf))
        if ttf <= 1.0 + 1e-12:
            buckets["0-1s"] += 1
        elif ttf <= 2.0 + 1e-12:
            buckets["1-2s"] += 1
        elif ttf <= 5.0 + 1e-12:
            buckets["2-5s"] += 1
        elif ttf <= 10.0 + 1e-12:
            buckets["5-10s"] += 1
    return {
        "N": len(xs),
        "TIME_TO_FILL_P10_SEC": _pct(xs, 0.10),
        "TIME_TO_FILL_MEDIAN_SEC": _median(xs),
        "TIME_TO_FILL_P75_SEC": _pct(xs, 0.75),
        "TIME_TO_FILL_P90_SEC": _pct(xs, 0.90),
        "TIME_TO_FILL_P95_SEC": _pct(xs, 0.95),
        "BUCKET_0_1": buckets["0-1s"],
        "BUCKET_1_2": buckets["1-2s"],
        "BUCKET_2_5": buckets["2-5s"],
        "BUCKET_5_10": buckets["5-10s"],
    }


def fill_quality_group(rows: list[dict[str, Any]], wid: str) -> dict[str, Any]:
    over = overlay_wait(rows, wid)
    filled = [r for r in over if r.get("WOULD_FILL")]
    pf = postfill_pack(filled)
    jp = sum(1 for r in filled if int(r.get("joint_label") or 0) == 1)
    return {
        "group_id": f"{wid}_FILL",
        "N": len(filled),
        "OLD_JOINT_POSITIVE_N": jp,
        "OLD_JOINT_POSITIVE_RATE": _rate(jp, len(filled)),
        "POSTFILL_MFE_MEDIAN": pf.get("POSTFILL_MFE_MEDIAN"),
        "POSTFILL_MFE_MEAN": pf.get("POSTFILL_MFE_MEAN"),
        "POSTFILL_DOWNSIDE_MEDIAN": pf.get("POSTFILL_DOWNSIDE_MEDIAN"),
        "POSTFILL_DOWNSIDE_MEAN": pf.get("POSTFILL_DOWNSIDE_MEAN"),
        "POSTFILL_MFE_N": pf.get("POSTFILL_MFE_N"),
        "POSTFILL_DOWNSIDE_N": pf.get("POSTFILL_DOWNSIDE_N"),
    }


def incremental_source_mechanism(by_inc: dict[str, dict[str, Any]]) -> str:
    w10 = by_inc.get("W10") or {}
    board_n = int(w10.get("NEW_FILL_FROM_NO_VALID_BOARD_N") or 0)
    cross_n = int(w10.get("NEW_FILL_FROM_PRIOR_NO_ASK_CROSS_N") or 0)
    if board_n == 0 and cross_n == 0:
        return "NO_INCREMENTAL_FILL"
    if board_n > cross_n:
        return "WAIT_ADDS_BOARD_OBSERVATION"
    if cross_n > board_n:
        return "WAIT_ADDS_ASK_CROSS"
    return "WAIT_ADDS_BOARD_AND_ASK_CROSS"


def _worse(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return float(a) < float(b)


def adverse_wait(*, w1: dict[str, Any], inc: dict[str, Any], surv: Optional[float]) -> bool:
    if int(inc.get("NEW_FILL_VS_W1_N") or 0) <= 0:
        return False
    mfe_worse = _worse(inc.get("POSTFILL_MFE_MEDIAN"), w1.get("POSTFILL_MFE_MEDIAN"))
    dn_worse = _worse(inc.get("POSTFILL_DOWNSIDE_MEDIAN"), w1.get("POSTFILL_DOWNSIDE_MEDIAN"))
    surv_bad = bool(surv is not None and float(surv) < float(SURVIVAL_LOW_MAX))
    return bool((mfe_worse and dn_worse) or surv_bad)


def decide(
    *,
    parity_ok: bool,
    by_wait: dict[str, dict[str, Any]],
    by_inc: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "PASSIVE_WAIT_POLICY_INTEGRITY_FAILED",
            "PRIMARY_MECHANISM": None,
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "W1=1s did not reproduce the frozen Passive Fill control.",
            "note": "STOP. BASE_PARITY failed.",
        }
    w1 = by_wait["W1"]
    restored = []
    adverse = []
    for wid in EXTENDED_IDS:
        any_r = _f(by_wait[wid].get("FILLABLE_ANY_COHORT_RATE"))
        if any_r is None or float(any_r) < float(RATE_MIN):
            continue
        surv = _f(by_wait[wid].get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"))
        if adverse_wait(w1=w1, inc=by_inc[wid], surv=surv):
            adverse.append(wid)
        else:
            restored.append(wid)
    if not restored and not adverse:
        return {
            "CASE": "C",
            "VERDICT": "WAIT_ALONE_CANNOT_RESTORE_OPPORTUNITY",
            "PRIMARY_MECHANISM": "WAIT_HORIZON_INSUFFICIENT",
            "NEXT_RESEARCH": "PASSIVE_PRICE_POLICY_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Extending WAIT to 10s without changing limit/ask-cross still leaves "
                "FILLABLE_ANY_COHORT_RATE below 0.50."
            ),
            "note": "CASE C. STOP. Price-policy test not started this run.",
        }
    if adverse and not restored:
        return {
            "CASE": "B",
            "VERDICT": "WAIT_EXTENSION_CREATES_ADVERSE_SELECTION",
            "PRIMARY_MECHANISM": "LONGER_WAIT_ADDS_WORSE_FILLS",
            "NEXT_RESEARCH": "PASSIVE_PRICE_POLICY_REASSESSMENT",
            "PRIMARY_FINDING": (
                "A longer WAIT restores fillable-any rate to >=0.50, but incremental fills "
                "are worse on both post-fill components or joint survival collapses."
            ),
            "note": "CASE B. STOP. Price-policy test not started this run.",
        }
    multi_ok = [wid for wid in restored if _f(by_wait[wid].get("MULTI_FILLABLE_COHORT_RATE")) is not None
                and float(by_wait[wid]["MULTI_FILLABLE_COHORT_RATE"]) >= float(RATE_MIN)]
    if multi_ok:
        return {
            "CASE": "A",
            "VERDICT": "PASSIVE_WAIT_EXTENSION_FEASIBLE",
            "PRIMARY_MECHANISM": "WAIT_RESTORES_FILLABLE_CROSS_SECTION",
            "NEXT_RESEARCH": "PASSIVE_WAIT_POLICY_PRECOMMIT_SELECTION",
            "PRIMARY_FINDING": (
                "At least one precommitted longer WAIT restores fillable-any and multi-fillable "
                "rates to >=0.50 without a frozen adverse-selection collapse vs W1. "
                "No WAIT is adopted this run."
            ),
            "note": "CASE A. STOP. WAIT policy not selected this run.",
            "restored_waits": restored,
            "multi_ok_waits": multi_ok,
        }
    return {
        "CASE": "D",
        "VERDICT": "PASSIVE_WAIT_IMPROVES_ACCESS_NOT_SELECTION",
        "PRIMARY_MECHANISM": "WAIT_RESTORES_ACCESS_WITHOUT_CROSS_SECTION",
        "NEXT_RESEARCH": "EXECUTION_POLICY_ARCHITECTURE_REASSESSMENT",
        "PRIMARY_FINDING": (
            "A longer WAIT can put at least one fillable name in most cohorts, but "
            "MULTI_FILLABLE_COHORT_RATE stays below 0.50. Access improves; ranking opportunity does not."
        ),
        "note": "CASE D. STOP. Architecture reassessment not started this run.",
        "restored_waits": restored,
    }
