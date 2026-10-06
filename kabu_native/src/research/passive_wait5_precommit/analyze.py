"""W5 precommit parity, day/session/clock robustness, incremental quality, gates A-J."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from research.canonical_entry_performance_rebase.analyze import _f, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_execution_feasibility.analyze import labeled_rows
from research.execution_aware_target_feasibility.analyze import fillable_geometry
from research.passive_wait5_precommit import (
    CLOCK_IMPROVE_MIN_FRAC,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    RATE_MIN,
    SURVIVAL_EXACT,
    WAIT_POLICY_CANDIDATE,
)
from research.passive_wait_policy_reassessment.analyze import (
    _close,
    _rate,
    joint_access,
    overlay_wait,
    postfill_pack,
    survival,
    wait_body,
)


def _cmp(a: Optional[float], b: Optional[float], tol: float = 1e-12) -> str:
    if a is None or b is None:
        return "na"
    if float(a) > float(b) + float(tol):
        return "gt"
    if float(a) < float(b) - float(tol):
        return "lt"
    return "eq"


def _ge(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return float(a) + 1e-12 >= float(b)


def slice_metrics(rows: list[dict[str, Any]], wid: str) -> dict[str, Any]:
    over = overlay_wait(rows, wid)
    geom = fillable_geometry(over)
    joint = joint_access(over)
    surv = survival(over)
    fill_n = sum(1 for r in over if r.get("WOULD_FILL"))
    return {
        "wait_id": wid,
        **geom,
        **joint,
        **surv,
        "ALL_WOULD_FILL_N": fill_n,
    }


def freeze_parity(*, w1: dict[str, Any], w5: dict[str, Any], w5_new_n: int) -> dict[str, Any]:
    obs = {
        "W1_ANY_RATE": w1.get("FILLABLE_ANY_COHORT_RATE"),
        "W1_MULTI_RATE": w1.get("MULTI_FILLABLE_COHORT_RATE"),
        "W5_ANY_RATE": w5.get("FILLABLE_ANY_COHORT_RATE"),
        "W5_MULTI_RATE": w5.get("MULTI_FILLABLE_COHORT_RATE"),
        "W5_WOULD_FILL_N": w5.get("ALL_WOULD_FILL_N"),
        "W5_FILLABLE_JOINT_RATE": w5.get("FILLABLE_JOINT_AVAILABLE_RATE"),
        "W5_JOINT_SURVIVAL": w5.get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"),
        "W5_NEW_FILL_N": w5_new_n,
    }
    checks = {
        "W1_ANY_RATE": _close(obs["W1_ANY_RATE"], PARITY_EXPECTED["W1_ANY_RATE"], PARITY_ABS_TOL),
        "W1_MULTI_RATE": _close(obs["W1_MULTI_RATE"], PARITY_EXPECTED["W1_MULTI_RATE"], PARITY_ABS_TOL),
        "W5_ANY_RATE": _close(obs["W5_ANY_RATE"], PARITY_EXPECTED["W5_ANY_RATE"], PARITY_ABS_TOL),
        "W5_MULTI_RATE": _close(obs["W5_MULTI_RATE"], PARITY_EXPECTED["W5_MULTI_RATE"], PARITY_ABS_TOL),
        "W5_WOULD_FILL_N": int(obs["W5_WOULD_FILL_N"] or -1) == int(PARITY_EXPECTED["W5_WOULD_FILL_N"]),
        "W5_FILLABLE_JOINT_RATE": _close(
            obs["W5_FILLABLE_JOINT_RATE"], PARITY_EXPECTED["W5_FILLABLE_JOINT_RATE"], PARITY_ABS_TOL
        ),
        "W5_JOINT_SURVIVAL": _close(obs["W5_JOINT_SURVIVAL"], PARITY_EXPECTED["W5_JOINT_SURVIVAL"], PARITY_ABS_TOL),
        "W5_NEW_FILL_N": int(obs["W5_NEW_FILL_N"] or -1) == int(PARITY_EXPECTED["W5_NEW_FILL_N"]),
    }
    return {
        "BASE_PARITY": all(checks.values()),
        "checks": checks,
        "expected": dict(PARITY_EXPECTED),
        "observed": obs,
    }


def nested_inclusion(rows: list[dict[str, Any]]) -> dict[str, Any]:
    w1_only = 0
    both = 0
    w5_only = 0
    for r in labeled_rows(rows):
        a = bool(wait_body(r, "W1").get("WOULD_FILL"))
        b = bool(wait_body(r, "W5").get("WOULD_FILL"))
        if a and b:
            both += 1
        elif a and not b:
            w1_only += 1
        elif b and not a:
            w5_only += 1
    return {
        "W1_AND_W5_FILL_N": both,
        "W1_ONLY_FILL_N": w1_only,
        "W5_ONLY_FILL_N": w5_only,
        "NESTED_INCLUSION_OK": w1_only == 0,
    }


def day_robustness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    by_day: dict[str, list] = defaultdict(list)
    for r in lab:
        by_day[str(r.get("date") or "")].append(r)
    recs = []
    any_gt = any_lt = any_eq = 0
    multi_gt = multi_lt = multi_eq = 0
    fill_gt = fill_lt = fill_eq = 0
    for day in ELIGIBLE_DAYS:
        grp = by_day.get(day) or []
        w1 = slice_metrics(grp, "W1")
        w5 = slice_metrics(grp, "W5")
        ca = _cmp(w5.get("FILLABLE_ANY_COHORT_RATE"), w1.get("FILLABLE_ANY_COHORT_RATE"))
        cm = _cmp(w5.get("MULTI_FILLABLE_COHORT_RATE"), w1.get("MULTI_FILLABLE_COHORT_RATE"))
        cf = _cmp(float(w5.get("ALL_WOULD_FILL_N") or 0), float(w1.get("ALL_WOULD_FILL_N") or 0))
        any_gt += int(ca == "gt")
        any_lt += int(ca == "lt")
        any_eq += int(ca == "eq")
        multi_gt += int(cm == "gt")
        multi_lt += int(cm == "lt")
        multi_eq += int(cm == "eq")
        fill_gt += int(cf == "gt")
        fill_lt += int(cf == "lt")
        fill_eq += int(cf == "eq")
        recs.append(
            {
                "date": day,
                "W1_ANY": w1.get("FILLABLE_ANY_COHORT_RATE"),
                "W5_ANY": w5.get("FILLABLE_ANY_COHORT_RATE"),
                "W1_MULTI": w1.get("MULTI_FILLABLE_COHORT_RATE"),
                "W5_MULTI": w5.get("MULTI_FILLABLE_COHORT_RATE"),
                "W1_FILL_N": w1.get("ALL_WOULD_FILL_N"),
                "W5_FILL_N": w5.get("ALL_WOULD_FILL_N"),
                "W1_FILLABLE_JOINT_RATE": w1.get("FILLABLE_JOINT_AVAILABLE_RATE"),
                "W5_FILLABLE_JOINT_RATE": w5.get("FILLABLE_JOINT_AVAILABLE_RATE"),
                "ANY_CMP": ca,
                "MULTI_CMP": cm,
                "FILL_N_CMP": cf,
            }
        )
    return {
        "daily": recs,
        "W5_ANY_GT_W1_DAYS": any_gt,
        "W5_ANY_LT_W1_DAYS": any_lt,
        "W5_ANY_EQ_W1_DAYS": any_eq,
        "W5_MULTI_GT_W1_DAYS": multi_gt,
        "W5_MULTI_LT_W1_DAYS": multi_lt,
        "W5_MULTI_EQ_W1_DAYS": multi_eq,
        "W5_FILL_N_GT_W1_DAYS": fill_gt,
        "W5_FILL_N_LT_W1_DAYS": fill_lt,
        "W5_FILL_N_EQ_W1_DAYS": fill_eq,
        "DAY_N": len(ELIGIBLE_DAYS),
    }


def session_robustness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    out: dict[str, Any] = {}
    for sess in ("AM", "PM"):
        grp = [r for r in lab if session_of(r) == sess]
        w1 = slice_metrics(grp, "W1")
        w5 = slice_metrics(grp, "W5")
        out[sess] = {
            "session": sess,
            "N": len(grp),
            "W1_ANY": w1.get("FILLABLE_ANY_COHORT_RATE"),
            "W5_ANY": w5.get("FILLABLE_ANY_COHORT_RATE"),
            "W1_MULTI": w1.get("MULTI_FILLABLE_COHORT_RATE"),
            "W5_MULTI": w5.get("MULTI_FILLABLE_COHORT_RATE"),
            "W1_FILL_N": w1.get("ALL_WOULD_FILL_N"),
            "W5_FILL_N": w5.get("ALL_WOULD_FILL_N"),
            "W1_FILLABLE_JOINT_RATE": w1.get("FILLABLE_JOINT_AVAILABLE_RATE"),
            "W5_FILLABLE_JOINT_RATE": w5.get("FILLABLE_JOINT_AVAILABLE_RATE"),
        }
    return out


def clock_robustness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    anchors = sorted({str(r.get("anchor") or "") for r in lab if r.get("anchor")})
    recs = []
    any_imp = any_eq = any_worse = 0
    multi_imp = multi_eq = multi_worse = 0
    w5_any_ge = w5_multi_ge = 0
    n_clock = 0
    for an in anchors:
        grp = [r for r in lab if str(r.get("anchor") or "") == an]
        w1 = slice_metrics(grp, "W1")
        w5 = slice_metrics(grp, "W5")
        if int(w1.get("COHORT_N") or 0) <= 0:
            continue
        n_clock += 1
        ca = _cmp(w5.get("FILLABLE_ANY_COHORT_RATE"), w1.get("FILLABLE_ANY_COHORT_RATE"))
        cm = _cmp(w5.get("MULTI_FILLABLE_COHORT_RATE"), w1.get("MULTI_FILLABLE_COHORT_RATE"))
        any_imp += int(ca == "gt")
        any_eq += int(ca == "eq")
        any_worse += int(ca == "lt")
        multi_imp += int(cm == "gt")
        multi_eq += int(cm == "eq")
        multi_worse += int(cm == "lt")
        if _ge(w5.get("FILLABLE_ANY_COHORT_RATE"), RATE_MIN):
            w5_any_ge += 1
        if _ge(w5.get("MULTI_FILLABLE_COHORT_RATE"), RATE_MIN):
            w5_multi_ge += 1
        recs.append(
            {
                "anchor": an,
                "COHORT_N": w1.get("COHORT_N"),
                "W1_ANY": w1.get("FILLABLE_ANY_COHORT_RATE"),
                "W5_ANY": w5.get("FILLABLE_ANY_COHORT_RATE"),
                "DELTA_ANY": (
                    None
                    if w1.get("FILLABLE_ANY_COHORT_RATE") is None or w5.get("FILLABLE_ANY_COHORT_RATE") is None
                    else float(w5["FILLABLE_ANY_COHORT_RATE"]) - float(w1["FILLABLE_ANY_COHORT_RATE"])
                ),
                "W1_MULTI": w1.get("MULTI_FILLABLE_COHORT_RATE"),
                "W5_MULTI": w5.get("MULTI_FILLABLE_COHORT_RATE"),
                "DELTA_MULTI": (
                    None
                    if w1.get("MULTI_FILLABLE_COHORT_RATE") is None or w5.get("MULTI_FILLABLE_COHORT_RATE") is None
                    else float(w5["MULTI_FILLABLE_COHORT_RATE"]) - float(w1["MULTI_FILLABLE_COHORT_RATE"])
                ),
                "ANY_CMP": ca,
                "MULTI_CMP": cm,
            }
        )
    min_imp = int(n_clock * float(CLOCK_IMPROVE_MIN_FRAC)) if n_clock else 0
    return {
        "clocks": recs,
        "CLOCKS_N": n_clock,
        "CLOCKS_ANY_IMPROVED_N": any_imp,
        "CLOCKS_ANY_UNCHANGED_N": any_eq,
        "CLOCKS_ANY_WORSENED_N": any_worse,
        "CLOCKS_MULTI_IMPROVED_N": multi_imp,
        "CLOCKS_MULTI_UNCHANGED_N": multi_eq,
        "CLOCKS_MULTI_WORSENED_N": multi_worse,
        "CLOCKS_W5_ANY_GE_050_N": w5_any_ge,
        "CLOCKS_W5_MULTI_GE_050_N": w5_multi_ge,
        "CLOCK_IMPROVE_MIN_N": min_imp,
        "CLOCK_ANY_MAJORITY_OK": bool(n_clock > 0 and any_imp >= min_imp and any_worse == 0),
        "CLOCK_MULTI_MAJORITY_OK": bool(n_clock > 0 and multi_imp >= min_imp and multi_worse == 0),
    }


def delay_geometry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    over = overlay_wait(rows, "W5")
    buckets = {
        "0-1s": [],
        "1-2s": [],
        "2-5s": [],
        "overflow": [],
    }
    for r in over:
        if not r.get("WOULD_FILL"):
            continue
        ttf = _f(r.get("TIME_TO_FILL_SEC"))
        if ttf is None:
            buckets["overflow"].append(r)
            continue
        if ttf <= 1.0 + 1e-12:
            buckets["0-1s"].append(r)
        elif ttf <= 2.0 + 1e-12:
            buckets["1-2s"].append(r)
        elif ttf <= 5.0 + 1e-12:
            buckets["2-5s"].append(r)
        else:
            buckets["overflow"].append(r)
    recs = []
    for name in ("0-1s", "1-2s", "2-5s"):
        grp = buckets[name]
        pf = postfill_pack(grp)
        jp = sum(1 for r in grp if int(r.get("joint_label") or 0) == 1)
        recs.append(
            {
                "bucket": name,
                "N": len(grp),
                "POSTFILL_MFE_MEAN": pf.get("POSTFILL_MFE_MEAN"),
                "POSTFILL_MFE_MEDIAN": pf.get("POSTFILL_MFE_MEDIAN"),
                "POSTFILL_DOWNSIDE_MEAN": pf.get("POSTFILL_DOWNSIDE_MEAN"),
                "POSTFILL_DOWNSIDE_MEDIAN": pf.get("POSTFILL_DOWNSIDE_MEDIAN"),
                "OLD_JOINT_POSITIVE_N": jp,
                "OLD_JOINT_POSITIVE_RATE": _rate(jp, len(grp)),
            }
        )
    return {
        "buckets": recs,
        "OVERFLOW_N": len(buckets["overflow"]),
        "W5_FILL_BUCKET_SUM": sum(len(buckets[k]) for k in ("0-1s", "1-2s", "2-5s")),
    }


def w5_new_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in labeled_rows(rows):
        w1 = wait_body(r, "W1")
        w5 = wait_body(r, "W5")
        if w5.get("WOULD_FILL") and not w1.get("WOULD_FILL"):
            rec = dict(r)
            rec["WOULD_FILL"] = True
            rec["POSTFILL_MFE_600"] = w5.get("POSTFILL_MFE_600")
            rec["POSTFILL_DOWNSIDE_AVOID_600"] = w5.get("POSTFILL_DOWNSIDE_AVOID_600")
            out.append(rec)
    return out


def day_new_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Paired day direction vs same-day W1 fill medians. Positive = W5_NEW better (higher)."""
    lab = labeled_rows(rows)
    by_day: dict[str, list] = defaultdict(list)
    for r in lab:
        by_day[str(r.get("date") or "")].append(r)
    recs = []
    mfe_pos = mfe_neg = mfe_eq = 0
    dn_pos = dn_neg = dn_eq = 0
    comparable = 0
    for day in ELIGIBLE_DAYS:
        grp = by_day.get(day) or []
        w1_over = overlay_wait(grp, "W1")
        w1_fill = [r for r in w1_over if r.get("WOULD_FILL")]
        w5_new = w5_new_rows(grp)
        w1_pf = postfill_pack(w1_fill)
        new_pf = postfill_pack(w5_new)
        rec = {
            "date": day,
            "W1_FILL_N": len(w1_fill),
            "W5_NEW_N": len(w5_new),
            "W1_MFE_MEDIAN": w1_pf.get("POSTFILL_MFE_MEDIAN"),
            "W5_NEW_MFE_MEDIAN": new_pf.get("POSTFILL_MFE_MEDIAN"),
            "W1_DOWNSIDE_MEDIAN": w1_pf.get("POSTFILL_DOWNSIDE_MEDIAN"),
            "W5_NEW_DOWNSIDE_MEDIAN": new_pf.get("POSTFILL_DOWNSIDE_MEDIAN"),
            "COMPARABLE": False,
            "MFE_CMP": "na",
            "DOWNSIDE_CMP": "na",
        }
        ok = (
            w1_pf.get("POSTFILL_MFE_MEDIAN") is not None
            and new_pf.get("POSTFILL_MFE_MEDIAN") is not None
            and w1_pf.get("POSTFILL_DOWNSIDE_MEDIAN") is not None
            and new_pf.get("POSTFILL_DOWNSIDE_MEDIAN") is not None
        )
        if ok:
            comparable += 1
            rec["COMPARABLE"] = True
            cm = _cmp(new_pf.get("POSTFILL_MFE_MEDIAN"), w1_pf.get("POSTFILL_MFE_MEDIAN"))
            cd = _cmp(new_pf.get("POSTFILL_DOWNSIDE_MEDIAN"), w1_pf.get("POSTFILL_DOWNSIDE_MEDIAN"))
            rec["MFE_CMP"] = cm
            rec["DOWNSIDE_CMP"] = cd
            mfe_pos += int(cm == "gt")
            mfe_neg += int(cm == "lt")
            mfe_eq += int(cm == "eq")
            dn_pos += int(cd == "gt")
            dn_neg += int(cd == "lt")
            dn_eq += int(cd == "eq")
        recs.append(rec)
    return {
        "daily": recs,
        "COMPARABLE_DAYS": comparable,
        "W5_NEW_MFE_POSITIVE_DAYS": mfe_pos,
        "W5_NEW_MFE_NEGATIVE_DAYS": mfe_neg,
        "W5_NEW_MFE_EQUAL_DAYS": mfe_eq,
        "W5_NEW_DOWNSIDE_POSITIVE_DAYS": dn_pos,
        "W5_NEW_DOWNSIDE_NEGATIVE_DAYS": dn_neg,
        "W5_NEW_DOWNSIDE_EQUAL_DAYS": dn_eq,
    }


def dual_collapse(*, w1_mfe: Optional[float], w1_dn: Optional[float], new_mfe: Optional[float], new_dn: Optional[float]) -> bool:
    if None in (w1_mfe, w1_dn, new_mfe, new_dn):
        return False
    return float(new_mfe) < float(w1_mfe) and float(new_dn) < float(w1_dn)


def gates(
    *,
    w5: dict[str, Any],
    sess: dict[str, Any],
    clocks: dict[str, Any],
    w1_fill: dict[str, Any],
    w5_new: dict[str, Any],
    nested_ok: bool,
    day: dict[str, Any],
    delay: dict[str, Any],
    parity_ok: bool,
) -> dict[str, Any]:
    am = sess.get("AM") or {}
    pm = sess.get("PM") or {}
    surv = _f(w5.get("JOINT_LABEL_SURVIVAL_AFTER_FILL_RATE"))
    new_mfe = _f(w5_new.get("POSTFILL_MFE_MEDIAN"))
    new_dn = _f(w5_new.get("POSTFILL_DOWNSIDE_MEDIAN"))
    collapse = dual_collapse(
        w1_mfe=_f(w1_fill.get("POSTFILL_MFE_MEDIAN")),
        w1_dn=_f(w1_fill.get("POSTFILL_DOWNSIDE_MEDIAN")),
        new_mfe=new_mfe,
        new_dn=new_dn,
    )
    body = {
        "A_W5_ANY_GE_050": _ge(w5.get("FILLABLE_ANY_COHORT_RATE"), RATE_MIN),
        "B_W5_MULTI_GE_050": _ge(w5.get("MULTI_FILLABLE_COHORT_RATE"), RATE_MIN),
        "C_AM_W5_ANY_GE_W1": _ge(am.get("W5_ANY"), am.get("W1_ANY")),
        "D_PM_W5_ANY_GE_W1": _ge(pm.get("W5_ANY"), pm.get("W1_ANY")),
        "E_AM_W5_MULTI_GE_W1": _ge(am.get("W5_MULTI"), am.get("W1_MULTI")),
        "F_PM_W5_MULTI_GE_W1": _ge(pm.get("W5_MULTI"), pm.get("W1_MULTI")),
        "G_W5_SURVIVAL_1": bool(surv is not None and abs(float(surv) - float(SURVIVAL_EXACT)) <= PARITY_ABS_TOL),
        "H_W5_NEW_MFE_MEDIAN_GT_0": bool(new_mfe is not None and float(new_mfe) > 0.0),
        "I_W5_NEW_DOWNSIDE_SANITY_NO_COLLAPSE": bool(
            new_dn is not None and float(new_dn) < 0.0 and (not collapse)
        ),
        "J_NO_INTEGRITY_VIOLATION": bool(
            parity_ok
            and nested_ok
            and int(day.get("W5_ANY_LT_W1_DAYS") or 0) == 0
            and int(day.get("W5_MULTI_LT_W1_DAYS") or 0) == 0
            and int(day.get("W5_FILL_N_LT_W1_DAYS") or 0) == 0
            and int(clocks.get("CLOCKS_ANY_WORSENED_N") or 0) == 0
            and int(clocks.get("CLOCKS_MULTI_WORSENED_N") or 0) == 0
            and int(delay.get("OVERFLOW_N") or 0) == 0
        ),
        "DUAL_COMPONENT_COLLAPSE": collapse,
        "CLOCK_ANY_MAJORITY_OK": bool(clocks.get("CLOCK_ANY_MAJORITY_OK")),
        "CLOCK_MULTI_MAJORITY_OK": bool(clocks.get("CLOCK_MULTI_MAJORITY_OK")),
    }
    needed = ("A_W5_ANY_GE_050", "B_W5_MULTI_GE_050", "C_AM_W5_ANY_GE_W1", "D_PM_W5_ANY_GE_W1",
              "E_AM_W5_MULTI_GE_W1", "F_PM_W5_MULTI_GE_W1", "G_W5_SURVIVAL_1",
              "H_W5_NEW_MFE_MEDIAN_GT_0", "I_W5_NEW_DOWNSIDE_SANITY_NO_COLLAPSE", "J_NO_INTEGRITY_VIOLATION")
    body["W5_PRECOMMIT_PASS"] = all(bool(body[k]) for k in needed)
    return body


def decide(*, gates_body: dict[str, Any]) -> dict[str, Any]:
    if not gates_body.get("J_NO_INTEGRITY_VIOLATION"):
        return {
            "CASE": "D",
            "VERDICT": "WAIT5_PRECOMMIT_INTEGRITY_FAILED",
            "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "W5 precommit could not reproduce the frozen WAIT harvest control, or nested WAIT inclusion failed.",
            "note": "CASE D. STOP. Runtime WAIT remains 1.0.",
        }
    quality_ok = bool(gates_body.get("G_W5_SURVIVAL_1") and gates_body.get("H_W5_NEW_MFE_MEDIAN_GT_0")
                      and gates_body.get("I_W5_NEW_DOWNSIDE_SANITY_NO_COLLAPSE"))
    if not quality_ok:
        return {
            "CASE": "C",
            "VERDICT": "WAIT5_INCREMENTAL_FILL_QUALITY_FAIL",
            "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
            "NEXT_RESEARCH": "PASSIVE_PRICE_POLICY_REASSESSMENT",
            "PRIMARY_FINDING": "W5 incremental fills fail the frozen post-fill sanity gate or joint survival.",
            "note": "CASE C. STOP. Price-policy test not started this run.",
        }
    sess_ok = bool(
        gates_body.get("C_AM_W5_ANY_GE_W1")
        and gates_body.get("D_PM_W5_ANY_GE_W1")
        and gates_body.get("E_AM_W5_MULTI_GE_W1")
        and gates_body.get("F_PM_W5_MULTI_GE_W1")
    )
    clock_ok = bool(gates_body.get("CLOCK_ANY_MAJORITY_OK") and gates_body.get("CLOCK_MULTI_MAJORITY_OK"))
    overall_ok = bool(gates_body.get("A_W5_ANY_GE_050") and gates_body.get("B_W5_MULTI_GE_050"))
    if overall_ok and (not sess_ok or not clock_ok):
        return {
            "CASE": "B",
            "VERDICT": "WAIT5_OPPORTUNITY_NOT_ROBUST",
            "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
            "NEXT_RESEARCH": "EXECUTION_POLICY_REASSESSMENT",
            "PRIMARY_FINDING": "W5 clears overall ANY/MULTI bars, but AM/PM or clock robustness fails the frozen gates.",
            "note": "CASE B. STOP. Execution-policy reassessment not started this run.",
        }
    if gates_body.get("W5_PRECOMMIT_PASS") and clock_ok:
        return {
            "CASE": "A",
            "VERDICT": "PASSIVE_WAIT5_PRECOMMIT_SUPPORTED",
            "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
            "NEXT_RESEARCH": "WAIT5_EXECUTION_AWARE_ENTRY_REBASE",
            "PRIMARY_FINDING": (
                "W5 is the shortest evaluated WAIT that clears BOTH fillable-any and multi-fillable 0.50 bars, "
                "is nested over W1, holds on AM and PM, and does not fail the incremental-fill quality sanity check. "
                "W5 is frozen as the development candidate only. Runtime WAIT remains 1.0."
            ),
            "note": "CASE A. STOP. W5 not adopted into runtime. ENTRY rebase not started this run.",
        }
    return {
        "CASE": "B",
        "VERDICT": "WAIT5_OPPORTUNITY_NOT_ROBUST",
        "WAIT_POLICY_CANDIDATE": WAIT_POLICY_CANDIDATE,
        "NEXT_RESEARCH": "EXECUTION_POLICY_REASSESSMENT",
        "PRIMARY_FINDING": "W5 did not clear the frozen precommit opportunity/robustness set.",
        "note": "CASE B. STOP.",
    }
