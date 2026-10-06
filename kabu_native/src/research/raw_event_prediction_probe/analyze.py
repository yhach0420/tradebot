"""S1/E0 parity, frozen A-J prediction gate, incremental vs S1, CASE A-D."""
from __future__ import annotations

from typing import Any, Optional

from research.canonical_entry_performance_rebase.analyze import _f
from research.joint_feature_architecture.analyze import gates as _ai_gates
from research.raw_event_prediction_probe import (
    ELIGIBLE_DAYS,
    JOINT_RATE_MIN,
    S1_EXPECTED,
    S1_PARITY_ABS_TOL,
)


def _close(a: Any, b: Any, tol: float) -> bool:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return False
    return abs(float(x) - float(y)) <= float(tol)


def _sub(a: Any, b: Any) -> Optional[float]:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return None
    return float(x) - float(y)


def base_parity(e0: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "MFE": _close(e0.get("TOP3_MFE_DELTA"), S1_EXPECTED["TOP3_MFE_DELTA"], S1_PARITY_ABS_TOL),
        "DOWNSIDE": _close(e0.get("TOP3_DOWNSIDE_DELTA"), S1_EXPECTED["TOP3_DOWNSIDE_DELTA"], S1_PARITY_ABS_TOL),
        "JOINT": _close(
            e0.get("JOINT_COHORT_SUCCESS_RATE"),
            S1_EXPECTED["JOINT_COHORT_SUCCESS_RATE"],
            S1_PARITY_ABS_TOL,
        ),
        "MFE_POS_DAYS": int(e0.get("MFE_POSITIVE_DAYS") or -1) == int(S1_EXPECTED["MFE_POSITIVE_DAYS"]),
        "MFE_NEG_DAYS": int(e0.get("MFE_NEGATIVE_DAYS") or -1) == int(S1_EXPECTED["MFE_NEGATIVE_DAYS"]),
        "DOWNSIDE_POS_DAYS": int(e0.get("DOWNSIDE_POSITIVE_DAYS") or -1)
        == int(S1_EXPECTED["DOWNSIDE_POSITIVE_DAYS"]),
        "DOWNSIDE_NEG_DAYS": int(e0.get("DOWNSIDE_NEGATIVE_DAYS") or -1)
        == int(S1_EXPECTED["DOWNSIDE_NEGATIVE_DAYS"]),
        "DOWNSIDE_EX_TOP3": _close(
            e0.get("DOWNSIDE_EX_TOP3_DAYS"),
            S1_EXPECTED["DOWNSIDE_EX_TOP3_DAYS"],
            S1_PARITY_ABS_TOL,
        ),
    }
    return {
        "BASE_PARITY": all(checks.values()),
        "checks": checks,
        "expected": dict(S1_EXPECTED),
        "observed": {
            "TOP3_MFE_DELTA": e0.get("TOP3_MFE_DELTA"),
            "TOP3_DOWNSIDE_DELTA": e0.get("TOP3_DOWNSIDE_DELTA"),
            "JOINT_COHORT_SUCCESS_RATE": e0.get("JOINT_COHORT_SUCCESS_RATE"),
            "MFE_POSITIVE_DAYS": e0.get("MFE_POSITIVE_DAYS"),
            "MFE_NEGATIVE_DAYS": e0.get("MFE_NEGATIVE_DAYS"),
            "DOWNSIDE_POSITIVE_DAYS": e0.get("DOWNSIDE_POSITIVE_DAYS"),
            "DOWNSIDE_NEGATIVE_DAYS": e0.get("DOWNSIDE_NEGATIVE_DAYS"),
            "DOWNSIDE_EX_TOP3_DAYS": e0.get("DOWNSIDE_EX_TOP3_DAYS"),
        },
    }


def gates(e1: dict[str, Any], *, d_joint: Optional[float]) -> dict[str, Any]:
    g = _ai_gates(e1)
    j_ok = bool(d_joint is not None and float(d_joint) > 0)
    flags = dict(g["gates"])
    flags["J_DELTA_JOINT_RATE_VS_S1_GT_0"] = j_ok
    fail = [k for k, v in flags.items() if not v]
    return {
        "gates": flags,
        "gate_fail": fail,
        "PASS": len(fail) == 0,
        "MFE_DELTA": g["MFE_DELTA"],
        "DOWNSIDE_DELTA": g["DOWNSIDE_DELTA"],
        "JOINT_RATE": g["JOINT_RATE"],
        "JOINT_RATE_MIN": JOINT_RATE_MIN,
    }


def incremental(e1: dict[str, Any], e0: dict[str, Any]) -> dict[str, Any]:
    d_mfe = _sub(e1.get("TOP3_MFE_DELTA"), e0.get("TOP3_MFE_DELTA"))
    d_dn = _sub(e1.get("TOP3_DOWNSIDE_DELTA"), e0.get("TOP3_DOWNSIDE_DELTA"))
    d_jt = _sub(e1.get("JOINT_COHORT_SUCCESS_RATE"), e0.get("JOINT_COHORT_SUCCESS_RATE"))
    e1_ex = _f(e1.get("DOWNSIDE_EX_TOP3_DAYS"))
    e0_ex = _f(e0.get("DOWNSIDE_EX_TOP3_DAYS"))
    ex_ok = bool(e1_ex is not None and e0_ex is not None and float(e1_ex) >= float(e0_ex))
    flag = bool(
        d_jt is not None
        and float(d_jt) > 0
        and d_mfe is not None
        and float(d_mfe) >= 0
        and d_dn is not None
        and float(d_dn) >= 0
        and ex_ok
    )
    return {
        "DELTA_MFE_VS_S1": d_mfe,
        "DELTA_DOWNSIDE_VS_S1": d_dn,
        "DELTA_JOINT_RATE_VS_S1": d_jt,
        "RAW_DESCRIPTOR_INCREMENTAL": flag,
        "downside_ex_top3_ge_s1": ex_ok,
    }


def _daily_map(rows: list[dict[str, Any]] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for rec in rows or []:
        d = str(rec.get("date") or "")
        v = _f(rec.get("value"))
        if not d or v is None:
            continue
        out[d] = float(v)
    return out


def paired_joint_days(e0: dict[str, Any], e1: dict[str, Any]) -> dict[str, Any]:
    a = _daily_map(e0.get("daily_joint"))
    b = _daily_map(e1.get("daily_joint"))
    improved = 0
    worsened = 0
    tied = 0
    missing = 0
    daily = []
    for d in ELIGIBLE_DAYS:
        x = a.get(d)
        y = b.get(d)
        rec = {"date": d, "E0_JOINT_RATE": x, "E1_JOINT_RATE": y}
        if x is None or y is None:
            missing += 1
            rec["delta"] = None
            rec["status"] = "missing"
        elif y > x:
            improved += 1
            rec["delta"] = float(y) - float(x)
            rec["status"] = "improved"
        elif y < x:
            worsened += 1
            rec["delta"] = float(y) - float(x)
            rec["status"] = "worsened"
        else:
            tied += 1
            rec["delta"] = 0.0
            rec["status"] = "tied"
        daily.append(rec)
    return {
        "JOINT_RATE_IMPROVED_DAYS": improved,
        "JOINT_RATE_WORSENED_DAYS": worsened,
        "JOINT_RATE_TIED_DAYS": tied,
        "JOINT_RATE_MISSING_DAYS": missing,
        "daily": daily,
    }


def arch_summary(body: dict[str, Any], *, d_joint: Optional[float] = None) -> dict[str, Any]:
    if body.get("architecture_id") == "E1" or body.get("representation_id") == "E1":
        g = gates(body, d_joint=d_joint)
    else:
        g = _ai_gates(body)
    return {
        "architecture_id": body.get("architecture_id") or body.get("representation_id"),
        "name": body.get("name"),
        "n_features": body.get("n_features") or len(body.get("features") or []),
        "n_cohorts": body.get("n_cohorts"),
        "MFE_DELTA": g.get("MFE_DELTA"),
        "DOWNSIDE_DELTA": g.get("DOWNSIDE_DELTA"),
        "JOINT_RATE": g.get("JOINT_RATE"),
        "MFE_POSITIVE_DAYS": body.get("MFE_POSITIVE_DAYS"),
        "MFE_NEGATIVE_DAYS": body.get("MFE_NEGATIVE_DAYS"),
        "DOWNSIDE_POSITIVE_DAYS": body.get("DOWNSIDE_POSITIVE_DAYS"),
        "DOWNSIDE_NEGATIVE_DAYS": body.get("DOWNSIDE_NEGATIVE_DAYS"),
        "MFE_EX_BEST_DAY": body.get("MFE_EX_BEST_DAY"),
        "MFE_EX_TOP3_DAYS": body.get("MFE_EX_TOP3_DAYS"),
        "DOWNSIDE_EX_BEST_DAY": body.get("DOWNSIDE_EX_BEST_DAY"),
        "DOWNSIDE_EX_TOP3_DAYS": body.get("DOWNSIDE_EX_TOP3_DAYS"),
        "ROC_AUC": body.get("ROC_AUC"),
        "AVERAGE_PRECISION": body.get("AVERAGE_PRECISION"),
        "PASS": g.get("PASS"),
        "gate_fail": g.get("gate_fail"),
        "gates": g.get("gates"),
    }


def integrity_ok(
    *,
    future_n: int,
    session_carry_n: int,
    itayose_n: int,
    special_n: int,
    after_t0_n: int,
    contamination_n: int,
    join_miss_n: int,
    raw_n: int,
) -> tuple[bool, str]:
    if int(future_n) != 0:
        return False, f"FUTURE_EVENT_USE_N={future_n}"
    if int(session_carry_n) != 0:
        return False, f"SESSION_CARRY_N={session_carry_n}"
    if int(itayose_n) != 0:
        return False, f"ITAYOSE_EVENT_USE_N={itayose_n}"
    if int(special_n) != 0:
        return False, f"SPECIAL_EVENT_USE_N={special_n}"
    if int(after_t0_n) != 0:
        return False, f"EVENT_TIME_AFTER_T0_N={after_t0_n}"
    if int(contamination_n) != 0:
        return False, f"TARGET_CONTAMINATION_N={contamination_n}"
    if int(join_miss_n) != 0:
        return False, f"JOIN_MISS_N={join_miss_n}"
    if int(raw_n) != 23:
        return False, f"RAW_DESCRIPTOR_N={raw_n}"
    return True, "ok"


def decide(
    e1: dict[str, Any],
    *,
    e0: dict[str, Any],
    parity_ok: bool,
    integ_ok: bool,
    integ_note: str,
    paired: dict[str, Any],
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "RAW_EVENT_PREDICTION_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "E0 did not reproduce the frozen S1 flattened 5s sequence control.",
            "RAW_DESCRIPTOR_PREDICTION_PASS": False,
            "RAW_DESCRIPTOR_INCREMENTAL": False,
            "note": "STOP. BASE_PARITY failed.",
        }
    if not integ_ok:
        return {
            "CASE": None,
            "VERDICT": "RAW_EVENT_PREDICTION_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": f"Raw-descriptor prediction integrity fail: {integ_note}.",
            "RAW_DESCRIPTOR_PREDICTION_PASS": False,
            "RAW_DESCRIPTOR_INCREMENTAL": False,
            "note": "STOP. RAW_EVENT_PREDICTION_INTEGRITY_FAILED.",
        }
    inc = incremental(e1, e0)
    g = gates(e1, d_joint=inc.get("DELTA_JOINT_RATE_VS_S1"))
    mfe = g["MFE_DELTA"]
    dn = g["DOWNSIDE_DELTA"]
    mfe_up = bool(mfe is not None and float(mfe) > 0)
    dn_up = bool(dn is not None and float(dn) > 0)
    out = {
        "DELTA_MFE_VS_S1": inc["DELTA_MFE_VS_S1"],
        "DELTA_DOWNSIDE_VS_S1": inc["DELTA_DOWNSIDE_VS_S1"],
        "DELTA_JOINT_RATE_VS_S1": inc["DELTA_JOINT_RATE_VS_S1"],
        "RAW_DESCRIPTOR_INCREMENTAL": inc["RAW_DESCRIPTOR_INCREMENTAL"],
        "RAW_DESCRIPTOR_PREDICTION_PASS": bool(g["PASS"]),
        "JOINT_RATE_IMPROVED_DAYS": paired.get("JOINT_RATE_IMPROVED_DAYS"),
        "JOINT_RATE_WORSENED_DAYS": paired.get("JOINT_RATE_WORSENED_DAYS"),
        "gate_fail": g["gate_fail"],
        "gates": g["gates"],
    }
    if g["PASS"]:
        return {
            **out,
            "CASE": "A",
            "VERDICT": "RAW_EVENT_INFORMATION_PREDICTIVE",
            "NEXT_RESEARCH": "RAW_EVENT_ARCHITECTURE_PRECOMMITTED_DEVELOPMENT",
            "PRIMARY_FINDING": (
                "Precommitted R1-R4 raw descriptors added to S1 pass frozen A-J vs CURRENT Top3 "
                "on 18-day OOF. Not adopted. Event-level deep model not started."
            ),
            "note": "CASE A. STOP. Do not start precommitted raw-event architecture this run.",
        }
    if inc["RAW_DESCRIPTOR_INCREMENTAL"]:
        return {
            **out,
            "CASE": "B",
            "VERDICT": "RAW_EVENT_INCREMENTAL_SIGNAL_PRESENT",
            "NEXT_RESEARCH": "EVENT_LEVEL_MODEL_ARCHITECTURE_PRECOMMIT",
            "PRIMARY_FINDING": (
                "All 23 raw descriptors move joint selection vs S1 without a sign tradeoff, "
                f"but frozen A-J fails: {g['gate_fail']}"
            ),
            "note": "CASE B. STOP. Event-level model architecture not started this run.",
        }
    if (mfe_up and not dn_up) or (dn_up and not mfe_up):
        return {
            **out,
            "CASE": "D",
            "VERDICT": "RAW_EVENT_STILL_SINGLE_OBJECTIVE",
            "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Adding the 23 raw descriptors still moves only one of MFE / downside-avoid vs CURRENT Top3."
            ),
            "note": "CASE D. STOP. Architecture reassessment not started this run.",
        }
    return {
        **out,
        "CASE": "C",
        "VERDICT": "RAW_EVENT_DESCRIPTORS_NOT_PREDICTIVE",
        "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
        "PRIMARY_FINDING": (
            "Precommitted R1-R4 raw descriptors do not improve held-out-day joint selection vs S1."
        ),
        "note": "CASE C. STOP. Event-level deep model not started.",
    }
