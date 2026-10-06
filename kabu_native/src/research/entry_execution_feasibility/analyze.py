"""Oracle parity, fillability counts, frozen 0.50 bar, CASE A-D."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_execution_feasibility import (
    EARLY_HORIZONS_SEC,
    ELIGIBLE_DAYS,
    FILLABLE_JOINT_MIN_RATE,
    ORACLE_EXPECTED,
    PARITY_ABS_TOL,
    SURVIVAL_LOW_MAX,
)
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.multiobjective_feasibility.analyze import cohort_key, group_cohorts, pareto_mask


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


def labeled_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("joint_label") in (0, 1)]


def oracle_availability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = group_cohorts(labeled_rows(rows))
    n_coh = 0
    n_avail = 0
    for _k, grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        n_coh += 1
        if any(int(r.get("joint_label") or 0) == 1 for r in grp):
            n_avail += 1
    pos = sum(1 for r in labeled_rows(rows) if int(r.get("joint_label") or 0) == 1)
    neg = sum(1 for r in labeled_rows(rows) if int(r.get("joint_label") or 0) == 0)
    return {
        "JOINT_LABEL_POSITIVE_N": pos,
        "JOINT_LABEL_NEGATIVE_N": neg,
        "LABELED_N": pos + neg,
        "LABELED_COHORT_N": n_coh,
        "JOINT_AVAILABLE_COHORT_N": n_avail,
        "JOINT_AVAILABLE_RATE": _rate(n_avail, n_coh),
    }


def base_parity(oracle: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "POS": int(oracle.get("JOINT_LABEL_POSITIVE_N") or -1) == int(ORACLE_EXPECTED["JOINT_LABEL_POSITIVE_N"]),
        "NEG": int(oracle.get("JOINT_LABEL_NEGATIVE_N") or -1) == int(ORACLE_EXPECTED["JOINT_LABEL_NEGATIVE_N"]),
        "AVAIL_N": int(oracle.get("JOINT_AVAILABLE_COHORT_N") or -1)
        == int(ORACLE_EXPECTED["JOINT_AVAILABLE_COHORT_N"]),
        "COHORT_N": int(oracle.get("LABELED_COHORT_N") or -1) == int(ORACLE_EXPECTED["LABELED_COHORT_N"]),
        "AVAIL_RATE": _close(
            oracle.get("JOINT_AVAILABLE_RATE"),
            ORACLE_EXPECTED["JOINT_AVAILABLE_RATE"],
            PARITY_ABS_TOL,
        ),
    }
    return {
        "BASE_PARITY": all(checks.values()),
        "checks": checks,
        "expected": dict(ORACLE_EXPECTED),
        "observed": {
            "JOINT_LABEL_POSITIVE_N": oracle.get("JOINT_LABEL_POSITIVE_N"),
            "JOINT_LABEL_NEGATIVE_N": oracle.get("JOINT_LABEL_NEGATIVE_N"),
            "JOINT_AVAILABLE_COHORT_N": oracle.get("JOINT_AVAILABLE_COHORT_N"),
            "LABELED_COHORT_N": oracle.get("LABELED_COHORT_N"),
            "JOINT_AVAILABLE_RATE": oracle.get("JOINT_AVAILABLE_RATE"),
        },
    }


def fill_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    all_n = len(lab)
    all_fill = sum(1 for r in lab if r.get("WOULD_FILL"))
    jp = [r for r in lab if int(r.get("joint_label") or 0) == 1]
    jn = [r for r in lab if int(r.get("joint_label") or 0) == 0]
    jp_fill = sum(1 for r in jp if r.get("WOULD_FILL"))
    jn_fill = sum(1 for r in jn if r.get("WOULD_FILL"))
    top = [r for r in lab if r.get("is_current_top3")]
    top_fill = sum(1 for r in top if r.get("WOULD_FILL"))
    return {
        "ALL_CANDIDATE_N": all_n,
        "ALL_WOULD_FILL_N": all_fill,
        "ALL_WOULD_FILL_RATE": _rate(all_fill, all_n),
        "JOINT_POSITIVE_N": len(jp),
        "JOINT_POSITIVE_WOULD_FILL_N": jp_fill,
        "JOINT_POSITIVE_WOULD_FILL_RATE": _rate(jp_fill, len(jp)),
        "JOINT_NEGATIVE_N": len(jn),
        "JOINT_NEGATIVE_WOULD_FILL_N": jn_fill,
        "JOINT_NEGATIVE_WOULD_FILL_RATE": _rate(jn_fill, len(jn)),
        "CURRENT_TOP3_N": len(top),
        "CURRENT_TOP3_WOULD_FILL_N": top_fill,
        "CURRENT_TOP3_WOULD_FILL_RATE": _rate(top_fill, len(top)),
    }


def fillable_joint_availability(rows: list[dict[str, Any]], *, oracle_avail_n: int) -> dict[str, Any]:
    by = group_cohorts(labeled_rows(rows))
    n_coh = 0
    n_fillable = 0
    for _k, grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        n_coh += 1
        if any(int(r.get("joint_label") or 0) == 1 and r.get("WOULD_FILL") for r in grp):
            n_fillable += 1
    rate = _rate(n_fillable, n_coh)
    lost_n = int(oracle_avail_n) - int(n_fillable)
    return {
        "FILLABLE_JOINT_AVAILABLE_COHORT_N": n_fillable,
        "FILLABLE_JOINT_AVAILABLE_RATE": rate,
        "JOINT_AVAILABILITY_LOST_TO_FILL_N": lost_n,
        "JOINT_AVAILABILITY_LOST_TO_FILL_RATE": _rate(lost_n, n_coh),
        "LABELED_COHORT_N": n_coh,
        "FILLABLE_JOINT_FEASIBLE": bool(rate is not None and float(rate) >= float(FILLABLE_JOINT_MIN_RATE)),
    }


def actual_joint_pareto_fill(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = group_cohorts(labeled_rows(rows))
    pareto_n = 0
    pareto_fill = 0
    for _k, grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        jp = [r for r in grp if int(r.get("joint_label") or 0) == 1]
        if not jp:
            continue
        u = [_f(r.get("T1")) for r in jp]
        d = [_f(r.get("T2")) for r in jp]
        if any(v is None for v in u) or any(v is None for v in d):
            continue
        front = pareto_mask([float(v) for v in u], [float(v) for v in d])
        for r, ok in zip(jp, front):
            if not ok:
                continue
            pareto_n += 1
            if r.get("WOULD_FILL"):
                pareto_fill += 1
    return {
        "ACTUAL_JOINT_PARETO_N": pareto_n,
        "ACTUAL_JOINT_PARETO_WOULD_FILL_N": pareto_fill,
        "ACTUAL_JOINT_PARETO_WOULD_FILL_RATE": _rate(pareto_fill, pareto_n),
    }


def nonfill_mechanism(rows: list[dict[str, Any]]) -> dict[str, Any]:
    miss = [r for r in labeled_rows(rows) if int(r.get("joint_label") or 0) == 1 and not r.get("WOULD_FILL")]
    counts = {
        "NO_ASK_CROSS_WITHIN_WAIT": 0,
        "BOARD_BECAME_NONEXECUTABLE": 0,
        "NO_VALID_CONTINUOUS_BOARD": 0,
        "OTHER": 0,
    }
    for r in miss:
        k = str(r.get("nonfill_class") or "OTHER")
        if k not in counts:
            k = "OTHER"
        counts[k] += 1
    return {
        "JOINT_NONFILL_N": len(miss),
        "JOINT_NONFILL_NO_ASK_CROSS_N": counts["NO_ASK_CROSS_WITHIN_WAIT"],
        "JOINT_NONFILL_NONEXEC_N": counts["BOARD_BECAME_NONEXECUTABLE"],
        "JOINT_NONFILL_NO_VALID_BOARD_N": counts["NO_VALID_CONTINUOUS_BOARD"],
        "JOINT_NONFILL_OTHER_N": counts["OTHER"],
    }


def early_path_compare(rows: list[dict[str, Any]]) -> dict[str, Any]:
    jp = [r for r in labeled_rows(rows) if int(r.get("joint_label") or 0) == 1]
    fill = [r for r in jp if r.get("WOULD_FILL")]
    miss = [r for r in jp if not r.get("WOULD_FILL")]

    def pack(xs: list[dict[str, Any]], key: str) -> dict[str, Any]:
        vals = [float(v) for r in xs if (v := _f(r.get(key))) is not None]
        return {"n": len(vals), "median": _median(vals), "mean": float(np.mean(vals)) if vals else None}

    out = {"fill": {}, "nonfill": {}, "delta_nonfill_minus_fill_median": {}}
    for sec, key in zip(EARLY_HORIZONS_SEC, ("ret_1s", "ret_5s", "ret_30s")):
        a = pack(fill, key)
        b = pack(miss, key)
        out["fill"][f"{int(sec)}s"] = a
        out["nonfill"][f"{int(sec)}s"] = b
        da, db = a.get("median"), b.get("median")
        out["delta_nonfill_minus_fill_median"][f"{int(sec)}s"] = (
            None if da is None or db is None else float(db) - float(da)
        )
    return out


def survival_after_fill(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled_jp = [
        r
        for r in labeled_rows(rows)
        if int(r.get("joint_label") or 0) == 1 and r.get("WOULD_FILL")
    ]
    usable = []
    survive = 0
    incomplete = 0
    for r in filled_jp:
        mfe = _f(r.get("MFE_FROM_FILL"))
        dn = _f(r.get("DOWNSIDE_FROM_FILL"))
        cu = _f(r.get("CURRENT_U_BASELINE"))
        cd = _f(r.get("CURRENT_D_BASELINE"))
        if mfe is None or dn is None or cu is None or cd is None:
            incomplete += 1
            continue
        usable.append(r)
        if float(mfe) > float(cu) and float(dn) > float(cd):
            survive += 1
    return {
        "JOINT_POSITIVE_WOULD_FILL_N": len(filled_jp),
        "JOINT_LABEL_SURVIVAL_AFTER_FILL_N": survive,
        "JOINT_LABEL_SURVIVAL_AFTER_FILL_DENOM": len(usable),
        "JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE": _rate(survive, len(usable)),
        "FILL_PATH_INCOMPLETE_N": incomplete,
    }


def decide(
    *,
    parity_ok: bool,
    integ_ok: bool,
    integ_note: str,
    fillable: dict[str, Any],
    survival: dict[str, Any],
    counts: dict[str, Any],
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "ENTRY_EXECUTION_FEASIBILITY_INTEGRITY_FAILED",
            "PRIMARY_MECHANISM": None,
            "NEXT_RESEARCH": "NONE",
            "FILLABLE_JOINT_FEASIBLE": False,
            "PRIMARY_FINDING": "Oracle joint availability / label counts did not reproduce the frozen Direct Joint control.",
            "note": "STOP. BASE_PARITY failed.",
        }
    if not integ_ok:
        return {
            "CASE": None,
            "VERDICT": "ENTRY_EXECUTION_FEASIBILITY_INTEGRITY_FAILED",
            "PRIMARY_MECHANISM": None,
            "NEXT_RESEARCH": "NONE",
            "FILLABLE_JOINT_FEASIBLE": False,
            "PRIMARY_FINDING": f"Execution-feasibility integrity fail: {integ_note}.",
            "note": "STOP. ENTRY_EXECUTION_FEASIBILITY_INTEGRITY_FAILED.",
        }
    rate = _f(fillable.get("FILLABLE_JOINT_AVAILABLE_RATE"))
    surv = _f(survival.get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"))
    feasible = bool(rate is not None and float(rate) >= float(FILLABLE_JOINT_MIN_RATE))
    jp_fill = _f(counts.get("JOINT_POSITIVE_WOULD_FILL_RATE"))
    many_fillable = bool(jp_fill is not None and float(jp_fill) >= float(FILLABLE_JOINT_MIN_RATE))
    if not feasible:
        return {
            "CASE": "B",
            "VERDICT": "JOINT_OPPORTUNITY_NOT_PASSIVELY_ACCESSIBLE",
            "PRIMARY_MECHANISM": "TARGET_EXECUTION_MISMATCH",
            "NEXT_RESEARCH": "EXECUTION_AWARE_ENTRY_TARGET_REDESIGN",
            "FILLABLE_JOINT_FEASIBLE": False,
            "PRIMARY_FINDING": (
                "Oracle joint opportunity exists, but current Passive Fill cannot access it "
                f"in >= {FILLABLE_JOINT_MIN_RATE:.2f} of cohorts."
            ),
            "note": "CASE B. STOP. Execution-aware target redesign not started this run.",
        }
    if surv is not None and float(surv) < float(SURVIVAL_LOW_MAX):
        return {
            "CASE": "C",
            "VERDICT": "JOINT_TARGET_NOT_STABLE_TO_FILL_TIME",
            "PRIMARY_MECHANISM": "DECISION_TO_FILL_TARGET_DECAY",
            "NEXT_RESEARCH": "FILL_TIME_ALIGNED_TARGET_REDESIGN",
            "FILLABLE_JOINT_FEASIBLE": True,
            "PRIMARY_FINDING": (
                "Fillable joint opportunity is common at t0, but the Direct Joint label often "
                "does not survive the same 600s contract from actual fill_time."
            ),
            "note": "CASE C. STOP. Fill-time target redesign not started this run.",
        }
    if feasible and many_fillable and (surv is None or float(surv) >= float(SURVIVAL_LOW_MAX)):
        return {
            "CASE": "A",
            "VERDICT": "FILLABLE_JOINT_OPPORTUNITY_CONFIRMED",
            "PRIMARY_MECHANISM": "SELECTION_OBSERVABILITY_BOTTLENECK",
            "NEXT_RESEARCH": "ENTRY_INFORMATION_SOURCE_REASSESSMENT",
            "FILLABLE_JOINT_FEASIBLE": True,
            "PRIMARY_FINDING": (
                "Current Passive Fill can still access joint MFE+downside opportunities in most "
                "cohorts. Execution is not the primary blocker; identification is."
            ),
            "note": "CASE A. STOP. Information-source reassessment not started this run.",
        }
    if feasible and (surv is None or float(surv) >= float(SURVIVAL_LOW_MAX)) and not many_fillable:
        return {
            "CASE": "D",
            "VERDICT": "ENTRY_EXECUTION_FEASIBILITY_INCONCLUSIVE",
            "PRIMARY_MECHANISM": None,
            "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
            "FILLABLE_JOINT_FEASIBLE": True,
            "PRIMARY_FINDING": (
                "At least one fillable joint candidate exists in most cohorts, but joint-positive "
                "standalone fill rate is below the frozen 0.50 bar. Mixed execution evidence."
            ),
            "note": "CASE D. STOP.",
        }
    return {
        "CASE": "D",
        "VERDICT": "ENTRY_EXECUTION_FEASIBILITY_INCONCLUSIVE",
        "PRIMARY_MECHANISM": None,
        "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
        "FILLABLE_JOINT_FEASIBLE": feasible,
        "PRIMARY_FINDING": "Fillable joint evidence is mixed and cannot be reduced to a single mechanism.",
        "note": "CASE D. STOP.",
    }
