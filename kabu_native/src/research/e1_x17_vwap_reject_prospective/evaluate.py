"""Prospective metrics, gate, freshness diagnostics."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import HIST_A2_VS_A1, MIN_A2_SUPPORT

LABEL = "forward_return_180s"
TOUCH = "plus5_before_minus5"


def _mean(xs: list[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def cohort_metrics(rows: list[dict[str, Any]], flag: str) -> dict[str, Any]:
    ok = [r for r in rows if r.get(flag)]
    sessions = sorted({r["session"] for r in ok})
    fr = [float(r[LABEL]) for r in ok if r.get(LABEL) is not None]
    return {
        "flag": flag,
        "support": len(ok),
        "symbols_n": len({r["symbol"] for r in ok}),
        "sessions": sessions,
        "forward_return_30s": _mean([float(r["forward_return_30s"]) for r in ok if r.get("forward_return_30s") is not None]),
        "forward_return_60s": _mean([float(r["forward_return_60s"]) for r in ok if r.get("forward_return_60s") is not None]),
        "forward_return_180s": _mean(fr),
        "forward_return_300s": _mean([float(r["forward_return_300s"]) for r in ok if r.get("forward_return_300s") is not None]),
        "MFE_60s": _mean([float(r["MFE_60s"]) for r in ok if r.get("MFE_60s") is not None]),
        "MAE_60s": _mean([float(r["MAE_60s"]) for r in ok if r.get("MAE_60s") is not None]),
        "MFE_180s": _mean([float(r["MFE_180s"]) for r in ok if r.get("MFE_180s") is not None]),
        "MAE_180s": _mean([float(r["MAE_180s"]) for r in ok if r.get("MAE_180s") is not None]),
        "MFE_300s": _mean([float(r["MFE_300s"]) for r in ok if r.get("MFE_300s") is not None]),
        "MAE_300s": _mean([float(r["MAE_300s"]) for r in ok if r.get("MAE_300s") is not None]),
        "plus5_before_minus5": _mean([float(r[TOUCH]) for r in ok if r.get(TOUCH) is not None]),
        "plus5_before_minus10": _mean([float(r["plus5_before_minus10"]) for r in ok if r.get("plus5_before_minus10") is not None]),
        "plus10_before_minus10": _mean([float(r["plus10_before_minus10"]) for r in ok if r.get("plus10_before_minus10") is not None]),
        "plus10_before_minus15": _mean([float(r["plus10_before_minus15"]) for r in ok if r.get("plus10_before_minus15") is not None]),
        "NO_PROGRESS_300S": _mean([1.0 if r.get("NO_PROGRESS_300S") else 0.0 for r in ok]) if ok else None,
    }


def freshness_bucket(age: Optional[float]) -> Optional[str]:
    if age is None:
        return None
    a = float(age)
    if a <= 60:
        return "FRESH_60"
    if a <= 300:
        return "MID_300"
    return "STALE_300"


def freshness_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = ("FRESH_60", "MID_300", "STALE_300")
    out: dict[str, Any] = {}
    sensitive = False
    a2_overall = cohort_metrics(rows, "in_A2").get("forward_return_180s")
    for b in buckets:
        sub = [r for r in rows if freshness_bucket(r.get("vwap_age_sec")) == b]
        c0 = cohort_metrics([{**r, "in_C0": r.get("in_C0")} for r in sub], "in_C0")
        a2 = cohort_metrics(sub, "in_A2")
        rej = cohort_metrics(sub, "in_A2_Rejected")
        out[b] = {
            "C0": c0,
            "A2": a2,
            "A2_Rejected": rej,
            "A2_minus_C0_fr": (
                (a2.get("forward_return_180s") - c0.get("forward_return_180s"))
                if a2.get("forward_return_180s") is not None and c0.get("forward_return_180s") is not None
                else None
            ),
        }
        # major reversal vs overall A2 direction vs C0
        delta = out[b]["A2_minus_C0_fr"]
        if a2_overall is not None and delta is not None:
            # if overall A2>C0 positive lift but bucket strongly negative
            overall_c0 = cohort_metrics(rows, "in_C0").get("forward_return_180s")
            if overall_c0 is not None:
                overall_d = a2_overall - overall_c0
                if overall_d > 0 and delta < -abs(overall_d):
                    sensitive = True
                if overall_d < 0 and delta > abs(overall_d):
                    sensitive = True
    out["VWAP_REJECT_FRESHNESS_SENSITIVE"] = sensitive
    out["note"] = "diagnostic_only_not_used_for_prospective_gate"
    return out


def primary_gate(c0: dict[str, Any], a2: dict[str, Any], rej: dict[str, Any]) -> dict[str, Any]:
    if (a2.get("support") or 0) < MIN_A2_SUPPORT:
        return {
            "status": "INSUFFICIENT_PROSPECTIVE_SUPPORT",
            "pass": False,
            "insufficient": True,
            "reasons": ["a2_support_lt_20"],
            "reject_checks_passed": 0,
            "a2_checks": {},
        }

    a2_checks = {
        "fr_ge_c0": (a2.get("forward_return_180s") is not None and c0.get("forward_return_180s") is not None
                     and a2["forward_return_180s"] >= c0["forward_return_180s"]),
        "touch_ge_c0": (a2.get("plus5_before_minus5") is not None and c0.get("plus5_before_minus5") is not None
                        and a2["plus5_before_minus5"] >= c0["plus5_before_minus5"]),
        "mae_ge_c0": (a2.get("MAE_180s") is not None and c0.get("MAE_180s") is not None
                      and a2["MAE_180s"] >= c0["MAE_180s"]),
        "np_le_c0": (a2.get("NO_PROGRESS_300S") is not None and c0.get("NO_PROGRESS_300S") is not None
                     and a2["NO_PROGRESS_300S"] <= c0["NO_PROGRESS_300S"]),
    }
    rej_checks = {
        "fr_lt_a2": (rej.get("forward_return_180s") is not None and a2.get("forward_return_180s") is not None
                     and rej["forward_return_180s"] < a2["forward_return_180s"]),
        "touch_lt_a2": (rej.get("plus5_before_minus5") is not None and a2.get("plus5_before_minus5") is not None
                        and rej["plus5_before_minus5"] < a2["plus5_before_minus5"]),
        "mae_lt_a2": (rej.get("MAE_180s") is not None and a2.get("MAE_180s") is not None
                      and rej["MAE_180s"] < a2["MAE_180s"]),
        "np_gt_a2": (rej.get("NO_PROGRESS_300S") is not None and a2.get("NO_PROGRESS_300S") is not None
                     and rej["NO_PROGRESS_300S"] > a2["NO_PROGRESS_300S"]),
    }
    rej_pass_n = sum(1 for v in rej_checks.values() if v)
    a2_all = all(a2_checks.values())
    a2_any = any(a2_checks.values())
    reasons = [k for k, v in a2_checks.items() if not v]
    if rej_pass_n < 2:
        reasons.append(f"reject_checks_lt_2({rej_pass_n})")

    if a2_all and rej_pass_n >= 2:
        status = "PASS"
        passed = True
    elif a2_any or rej_pass_n >= 1:
        status = "MIXED"
        passed = False
    else:
        status = "FAIL"
        passed = False

    return {
        "status": status,
        "pass": passed,
        "insufficient": False,
        "a2_checks": a2_checks,
        "reject_checks": rej_checks,
        "reject_checks_passed": rej_pass_n,
        "reasons": reasons,
    }


def historical_direction(a2: dict[str, Any], c0: dict[str, Any]) -> dict[str, Any]:
    """Compare prospective A2 vs C0 deltas to fixed historical A2 vs A1 direction (sign only)."""
    def d(a, b):
        if a is None or b is None:
            return None
        return float(a) - float(b)

    prosp = {
        "fr_delta": d(a2.get("forward_return_180s"), c0.get("forward_return_180s")),
        "touch_delta": d(a2.get("plus5_before_minus5"), c0.get("plus5_before_minus5")),
        "MAE_delta": d(a2.get("MAE_180s"), c0.get("MAE_180s")),
        "NoProgress_delta": d(a2.get("NO_PROGRESS_300S"), c0.get("NO_PROGRESS_300S")),
    }
    hist = HIST_A2_VS_A1
    same = {}
    for pk, hk in (
        ("fr_delta", "day_balanced_fr_delta"),
        ("touch_delta", "first_touch_delta"),
        ("MAE_delta", "MAE_delta"),
        ("NoProgress_delta", "NoProgress_delta"),
    ):
        pv, hv = prosp.get(pk), hist.get(hk)
        if pv is None or hv is None:
            same[pk] = None
        else:
            same[pk] = (pv > 0 and hv > 0) or (pv < 0 and hv < 0) or (pv == 0 and hv == 0)
    return {
        "historical_fixed": hist,
        "prospective_a2_vs_c0": prosp,
        "same_direction": same,
        "note": "historical values frozen — not retuned",
    }


def a3_a4_diagnostic(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """NON_DECISION_DIAGNOSTIC_ONLY — rebound/activity not in candidate."""
    from research.e1_x16_same_anchor_vwap_reject import REBOUND_MIN_BPS, VOLUME_PERCENTILE_MIN, MIN_UNIVERSE
    a3 = []
    for r in rows:
        if not r.get("in_A2"):
            continue
        reb = r.get("rebound_from_recent_low_bps")
        if reb is not None and float(reb) >= REBOUND_MIN_BPS:
            a3.append(r)
    return {
        "role": "NON_DECISION_DIAGNOSTIC_ONLY",
        "A3_support": len(a3),
        "A3_forward_return_180s": _mean([float(r[LABEL]) for r in a3 if r.get(LABEL) is not None]),
        "A4_support": 0,
        "A4_note": "activity/universe not applied — not in sealed candidate",
        "VOLUME_PERCENTILE_MIN_ref_only": VOLUME_PERCENTILE_MIN,
        "MIN_UNIVERSE_ref_only": MIN_UNIVERSE,
        "does_not_affect_a2_prospective": True,
    }
