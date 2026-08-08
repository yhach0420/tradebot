"""Incremental metrics, stability, selection gate."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from . import GATE, VARIANTS

LABEL = "forward_return_180s"
TOUCH = "plus5_before_minus5"


def _mean(xs: list[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def variant_metrics(matched: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    p = variant.lower()
    ok = [m for m in matched if m.get(f"{p}_status") == "OK"]
    c0_ok = [m for m in matched if m.get("c0_status") == "OK"]
    n_ep = len(matched)
    retention = len(ok) / n_ep if n_ep else 0.0
    exclusion = 1.0 - retention

    def col(key: str) -> list[float]:
        out = []
        for m in ok:
            v = m.get(f"{p}_{key}")
            if v is not None:
                out.append(float(v))
        return out

    fr = col(LABEL)
    mfe180 = col("MFE_180s")
    mae180 = col("MAE_180s")
    mfe300 = col("MFE_300s")
    mae300 = col("MAE_300s")
    touch = col(TOUCH)
    np_rate = _mean([1.0 if m.get(f"{p}_NO_PROGRESS_300S") else 0.0 for m in ok]) if ok else None
    tdelta = col("time_delta_vs_c0_sec") if variant != "C0" else [0.0]
    pdelta = col("price_delta_vs_c0_sec") if False else col("price_delta_vs_c0_bps")

    days = sorted({m["date"] for m in ok})
    syms = sorted({m["symbol"] for m in ok})

    # day-balanced mean forward return
    by_day: dict[str, list[float]] = defaultdict(list)
    for m in ok:
        v = m.get(f"{p}_{LABEL}")
        if v is not None:
            by_day[m["date"]].append(float(v))
    day_bal = _mean([float(np.mean(v)) for v in by_day.values() if v])

    by_sym: dict[str, list[float]] = defaultdict(list)
    for m in ok:
        v = m.get(f"{p}_{LABEL}")
        if v is not None:
            by_sym[m["symbol"]].append(float(v))
    sym_bal = _mean([float(np.mean(v)) for v in by_sym.values() if len(v) >= 3])

    day_contrib = {d: len(v) / len(ok) for d, v in by_day.items()} if ok else {}
    sym_contrib = {s: len(v) / len(ok) for s, v in by_sym.items()} if ok else {}
    max_day = max(day_contrib.items(), key=lambda x: x[1]) if day_contrib else ("", 0.0)
    max_sym = max(sym_contrib.items(), key=lambda x: x[1]) if sym_contrib else ("", 0.0)

    # LODO: leave-one-day mean FR
    lodo = []
    for leave in days:
        sub = [float(m[f"{p}_{LABEL}"]) for m in ok
               if m["date"] != leave and m.get(f"{p}_{LABEL}") is not None]
        if len(sub) >= 20:
            lodo.append({"leave_day": leave, "mean_fr": float(np.mean(sub))})

    return {
        "variant": variant,
        "support_episodes": len(ok),
        "n_episodes_universe": n_ep,
        "episode_retention_rate": retention,
        "episode_exclusion_rate": exclusion,
        "entry_days": len(days),
        "symbols_n": len(syms),
        "mean_forward_return_180s": _mean(fr),
        "mean_MFE_180s": _mean(mfe180),
        "mean_MAE_180s": _mean(mae180),
        "mean_MFE_300s": _mean(mfe300),
        "mean_MAE_300s": _mean(mae300),
        "mean_first_touch_plus5_before_minus5": _mean(touch),
        "no_progress_rate": np_rate,
        "mean_time_delta_vs_c0_sec": _mean(tdelta) if variant != "C0" else 0.0,
        "mean_price_delta_vs_c0_bps": _mean(pdelta) if variant != "C0" else 0.0,
        "day_balanced_forward_return": day_bal,
        "symbol_balanced_forward_return": sym_bal,
        "max_single_day_contribution": {"day": max_day[0], "frac": max_day[1]},
        "max_single_symbol_contribution": {"symbol": max_sym[0], "frac": max_sym[1]},
        "lodo": lodo,
        "c0_ok_n": len(c0_ok),
    }


def incremental(a: dict[str, Any], b: dict[str, Any], label: str) -> dict[str, Any]:
    """a - b deltas (improvement if FR/touch higher, MAE less negative / higher, NP lower)."""
    def d(key: str, invert: bool = False) -> Optional[float]:
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            return None
        return float(vb - va) if invert else float(va - vb)

    return {
        "contrast": label,
        "forward_return_delta": d("mean_forward_return_180s"),
        "day_balanced_fr_delta": d("day_balanced_forward_return"),
        "MFE_180_delta": d("mean_MFE_180s"),
        "MAE_180_delta": d("mean_MAE_180s"),  # higher (less negative) is better
        "first_touch_delta": d("mean_first_touch_plus5_before_minus5"),
        "NoProgress_delta": d("no_progress_rate"),  # lower better → negative delta good
        "time_delta_sec": d("mean_time_delta_vs_c0_sec"),
        "price_delta_bps": d("mean_price_delta_vs_c0_bps"),
        "retention_delta": d("episode_retention_rate"),
    }


def exclude_day(matched: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [m for m in matched if m["date"] != day]


def freshness_strata(matched: list[dict[str, Any]], variant: str = "C3") -> dict[str, Any]:
    p = variant.lower()
    ok = [m for m in matched if m.get(f"{p}_status") == "OK"]
    ages = [float(m[f"{p}_price_age_sec"]) for m in ok if m.get(f"{p}_price_age_sec") is not None]
    if len(ages) < 30:
        return {"status": "INSUFFICIENT", "ACTIVITY_SIGNAL_FRESHNESS_SENSITIVE": False}
    q1, q2 = float(np.quantile(ages, 1 / 3)), float(np.quantile(ages, 2 / 3))

    def grp(age: float) -> str:
        if age <= q1:
            return "high"  # fresher = lower age
        if age <= q2:
            return "middle"
        return "low"

    # remap: low age = high freshness
    def grp2(age: float) -> str:
        if age <= q1:
            return "high_freshness"
        if age <= q2:
            return "middle_freshness"
        return "low_freshness"

    effects = {}
    sensitive = False
    overall = _mean([float(m[f"{p}_{LABEL}"]) for m in ok if m.get(f"{p}_{LABEL}") is not None])
    for gname in ("high_freshness", "middle_freshness", "low_freshness"):
        sub = []
        for m in ok:
            age = m.get(f"{p}_price_age_sec")
            if age is None or m.get(f"{p}_{LABEL}") is None:
                continue
            if grp2(float(age)) == gname:
                sub.append(float(m[f"{p}_{LABEL}"]))
        effects[gname] = _mean(sub)
        if overall is not None and effects[gname] is not None:
            # major reversal: overall positive but group negative or vice versa with magnitude
            if overall > 0 and effects[gname] < -abs(overall):
                sensitive = True
            if overall < 0 and effects[gname] > abs(overall):
                sensitive = True
    return {
        "age_tercile_cuts_sec": {"q33": q1, "q66": q2},
        "group_mean_forward_return_180s": effects,
        "overall_mean_forward_return_180s": overall,
        "ACTIVITY_SIGNAL_FRESHNESS_SENSITIVE": sensitive,
    }


def selection_gate(
    metrics: dict[str, dict[str, Any]],
    incs: dict[str, dict[str, Any]],
    with22: dict[str, Any],
    without22: dict[str, Any],
) -> dict[str, Any]:
    """Pick at most one candidate among C1/C2/C3 vs C0."""
    c0 = metrics["C0"]

    def passes(v: str) -> tuple[bool, list[str]]:
        m = metrics[v]
        reasons = []
        if m["support_episodes"] < GATE["support_min"]:
            reasons.append("support")
        if m["entry_days"] < GATE["entry_days_min"]:
            reasons.append("entry_days")
        # day-balanced FR improve vs C0
        if m.get("day_balanced_forward_return") is None or c0.get("day_balanced_forward_return") is None:
            reasons.append("missing_day_bal")
        elif m["day_balanced_forward_return"] <= c0["day_balanced_forward_return"]:
            reasons.append("day_bal_not_improved")
        # first-touch improve
        if m.get("mean_first_touch_plus5_before_minus5") is None or c0.get("mean_first_touch_plus5_before_minus5") is None:
            reasons.append("missing_touch")
        elif m["mean_first_touch_plus5_before_minus5"] <= c0["mean_first_touch_plus5_before_minus5"]:
            reasons.append("touch_not_improved")
        # MAE not worse (MAE more negative = worse)
        if m.get("mean_MAE_180s") is not None and c0.get("mean_MAE_180s") is not None:
            if m["mean_MAE_180s"] < c0["mean_MAE_180s"] - 1e-12:
                reasons.append("MAE_worse")
        # NoProgress not increased
        if m.get("no_progress_rate") is not None and c0.get("no_progress_rate") is not None:
            if m["no_progress_rate"] > c0["no_progress_rate"] + 1e-12:
                reasons.append("NoProgress_increased")
        # 20260722 exclusion direction
        w = with22.get("day_balanced_forward_return")
        wo = without22.get("day_balanced_forward_return")
        w0 = metrics["C0"]  # need without for C0 too — passed in with22/without22 for this variant only
        # caller passes variant-specific with/without; compare delta sign
        if w is not None and wo is not None and c0.get("day_balanced_forward_return") is not None:
            # both should beat C0 day-bal or at least same direction of improvement
            pass
        max_day = (m.get("max_single_day_contribution") or {}).get("frac") or 1
        max_sym = (m.get("max_single_symbol_contribution") or {}).get("frac") or 1
        if max_day > GATE["max_day_contrib"]:
            reasons.append("max_day")
        if max_sym > GATE["max_sym_contrib"]:
            reasons.append("max_sym")
        return (len(reasons) == 0), reasons

    # 0722 sensitivity for each variant stored externally
    results = {}
    for v in ("C1", "C2", "C3"):
        ok, reasons = passes(v)
        results[v] = {"gate_pass": ok, "fail_reasons": reasons}

    # incremental requirements
    # C2 needs C2>C1; C3 needs C3>C2
    selected = None
    # Prefer simplest that passes: try C1, then C2 if improves C1, then C3 if improves C2
    if results["C1"]["gate_pass"]:
        selected = "C1"
    c2_inc = incs.get("C2_vs_C1") or {}
    c3_inc = incs.get("C3_vs_C2") or {}

    def improves(inc: dict) -> bool:
        fr = inc.get("day_balanced_fr_delta")
        ft = inc.get("first_touch_delta")
        return (fr is not None and fr > 0) or (ft is not None and ft > 0)

    if results["C2"]["gate_pass"] and improves(c2_inc):
        # C2 only if improves C1; if C1 didn't pass, still allow C2 if gate pass and improves over C0 (already in gate)
        if results["C1"]["gate_pass"]:
            selected = "C2"
        elif selected is None:
            selected = "C2"
    elif results["C2"]["gate_pass"] and results["C1"]["gate_pass"] and not improves(c2_inc):
        results["C2"]["fail_reasons"] = list(results["C2"]["fail_reasons"]) + ["no_incremental_vs_C1"]
        if selected == "C2":
            selected = "C1"

    if results["C3"]["gate_pass"] and improves(c3_inc):
        if selected == "C2" or (selected is None and results["C2"]["gate_pass"]):
            selected = "C3"
        elif selected == "C1" and improves(c3_inc) and improves(c2_inc):
            selected = "C3"
        elif selected is None:
            selected = "C3"
    elif results["C3"]["gate_pass"] and selected in ("C1", "C2") and not improves(c3_inc):
        results["C3"]["fail_reasons"] = list(results["C3"]["fail_reasons"]) + ["no_incremental_vs_C2"]

    # Don't pick complex C3 if equal to simpler — if C3 selected but not improve C2, drop
    if selected == "C3" and not improves(c3_inc):
        selected = "C2" if results["C2"]["gate_pass"] and improves(c2_inc) else (
            "C1" if results["C1"]["gate_pass"] else None
        )

    return {"per_variant": results, "selected_candidate": selected}
