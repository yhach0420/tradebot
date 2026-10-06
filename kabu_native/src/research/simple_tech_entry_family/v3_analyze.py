"""V3 exit-neutral gates: Ask markout edge, passive incompatibility, component RCA. No C14."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family.stages import good_upmove
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC, V1_LOCKED


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if x is not None and isinstance(x, (int, float)) and x == x]
    if not vs:
        return None
    return float(np.mean(vs))


def _median(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if x is not None and isinstance(x, (int, float)) and x == x]
    if not vs:
        return None
    return float(np.median(vs))


def _pos_rate(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if x is not None and isinstance(x, (int, float)) and x == x]
    if not vs:
        return None
    return float(sum(1 for v in vs if v > 0.0) / len(vs))


def _vals(rows: list[dict[str, Any]], key: str) -> list[Any]:
    return [r.get(key) for r in rows]


def horizon_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"N": len(rows)}
    for h in HORIZONS_SEC:
        k = f"markout_{int(h)}"
        xs = _vals(rows, k)
        out[f"MARKOUT_{int(h)}_MEAN"] = _mean(xs)
        out[f"MARKOUT_{int(h)}_MEDIAN"] = _median(xs)
        out[f"MARKOUT_{int(h)}_POS_RATE"] = _pos_rate(xs)
        cr = [bool(r.get(f"cost_recovered_{int(h)}")) for r in rows]
        out[f"COST_RECOVERY_RATE_{int(h)}"] = (sum(1 for x in cr if x) / len(cr)) if cr else None
    out["MFE_MEAN"] = _mean(_vals(rows, "mfe_bps"))
    out["MFE_MEDIAN"] = _median(_vals(rows, "mfe_bps"))
    out["MAE_MEAN"] = _mean(_vals(rows, "mae_bps"))
    out["MAE_MEDIAN"] = _median(_vals(rows, "mae_bps"))
    return out


def day_rows(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r.get("date") or "")].append(r)
    out = []
    for d in days:
        xs = by.get(d) or []
        rec = {
            "date": d,
            "SIGNAL_N": len(xs),
            "EXECUTABLE_N": sum(1 for r in xs if r.get("executable_signal")),
            "PASSIVE_FILL_N": sum(1 for r in xs if r.get("passive_filled")),
            "MARKOUT30_MEAN": _mean(_vals(xs, "markout_30")),
            "MARKOUT60_MEAN": _mean(_vals(xs, "markout_60")),
            "MARKOUT180_MEAN": _mean(_vals(xs, "markout_180")),
            "MARKOUT300_MEAN": _mean(_vals(xs, "markout_300")),
            "COST_RECOVERY_RATE_300": (
                sum(1 for r in xs if r.get("cost_recovered_300")) / len(xs) if xs else None
            ),
            "MFE_MEDIAN": _median(_vals(xs, "mfe_bps")),
            "MAE_MEDIAN": _median(_vals(xs, "mae_bps")),
        }
        out.append(rec)
    return out


def day_sign_counts(daily: list[dict[str, Any]], key: str) -> dict[str, Any]:
    vals = [r.get(key) for r in daily if r.get("SIGNAL_N")]
    nums = [float(v) for v in vals if v is not None and v == v]
    pos = sum(1 for v in nums if v > 1e-12)
    neg = sum(1 for v in nums if v < -1e-12)
    zero = len(nums) - pos - neg
    return {
        "POSITIVE_DAY_N": pos,
        "NEGATIVE_DAY_N": neg,
        "ZERO_DAY_N": zero,
        "DAY_MEDIAN_MARKOUT": float(np.median(nums)) if nums else None,
        "DAY_N_WITH_SIGNALS": len(nums),
    }


def concentration(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    xs = [(str(r.get("date") or ""), str(r.get("symbol") or ""), r.get(key)) for r in rows]
    usable = [(d, s, float(v)) for d, s, v in xs if v is not None and v == v]
    if not usable:
        return {
            "BEST_DAY_CONTRIBUTION": None,
            "TOP3_DAY_CONTRIBUTION": None,
            "TOP_SYMBOL_CONTRIBUTION": None,
            "EX_BEST_DAY_MARKOUT": None,
            "EX_TOP3_DAY_MARKOUT": None,
            "BEST_DAY": None,
        }
    total = float(sum(v for _d, _s, v in usable))
    by_day: dict[str, list[float]] = defaultdict(list)
    by_sym: dict[str, list[float]] = defaultdict(list)
    for d, s, v in usable:
        by_day[d].append(v)
        by_sym[s].append(v)
    day_sum = {d: float(sum(vs)) for d, vs in by_day.items()}
    day_mean = {d: float(np.mean(vs)) for d, vs in by_day.items()}
    ordered_days = sorted(day_mean.keys(), key=lambda d: day_mean[d], reverse=True)
    best = ordered_days[0]
    top3 = ordered_days[:3]
    sym_sum = {s: float(sum(vs)) for s, vs in by_sym.items()}
    top_sym = max(sym_sum.keys(), key=lambda s: abs(sym_sum[s]))
    ex_best = [v for d, _s, v in usable if d != best]
    ex_top3 = [v for d, _s, v in usable if d not in set(top3)]
    def _contrib(part: float) -> Optional[float]:
        if abs(total) < 1e-12:
            return None
        return float(part / total)

    return {
        "BEST_DAY": best,
        "BEST_DAY_CONTRIBUTION": _contrib(day_sum[best]),
        "TOP3_DAY_CONTRIBUTION": _contrib(sum(day_sum[d] for d in top3)),
        "TOP_SYMBOL": top_sym,
        "TOP_SYMBOL_CONTRIBUTION": _contrib(sym_sum[top_sym]),
        "EX_BEST_DAY_MARKOUT": _mean(ex_best),
        "EX_TOP3_DAY_MARKOUT": _mean(ex_top3),
        "BEST_DAY_MEAN": day_mean[best],
    }


def passive_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in rows if r.get("passive_filled")]
    non = [r for r in rows if not r.get("passive_filled")]
    pf = horizon_pack(filled)
    pn = horizon_pack(non)
    out = {"FILLED_N": len(filled), "NONFILLED_N": len(non), "filled": pf, "nonfilled": pn}
    for h in (60, 180, 300):
        a = pf.get(f"MARKOUT_{h}_MEAN")
        b = pn.get(f"MARKOUT_{h}_MEAN")
        out[f"PASSIVE_FILLED_MARKOUT_{h}"] = a
        out[f"PASSIVE_NONFILLED_MARKOUT_{h}"] = b
        out[f"NONFILL_GT_FILL_{h}"] = bool(a is not None and b is not None and float(b) > float(a))
    multi = {}
    for h in (60, 180, 300):
        by: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"f": [], "n": []})
        for r in rows:
            v = r.get(f"markout_{h}")
            if v is None or v != v:
                continue
            d = str(r.get("date") or "")
            (by[d]["f"] if r.get("passive_filled") else by[d]["n"]).append(float(v))
        n_days = 0
        for rec in by.values():
            if rec["f"] and rec["n"] and float(np.mean(rec["n"])) > float(np.mean(rec["f"])):
                n_days += 1
        multi[h] = n_days
        out[f"PASSIVE_DIFF_POS_DAYS_{h}"] = n_days
    out["MULTI_DAY"] = all(int(multi[h]) >= 2 for h in (60, 180, 300))
    return out


def good6_pack(v1_signals: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    lost = [r for r in v1_signals if good_upmove(r) and not r.get("WOULD_FILL")]
    keys = {
        (str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0))
        for r in lost
    }
    matched = [
        r
        for r in rows
        if (str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0)) in keys
    ]
    hp = horizon_pack(matched)
    ask_edge = bool(
        (hp.get("MARKOUT_60_MEAN") or 0) > 0
        and (hp.get("MARKOUT_180_MEAN") or 0) > 0
        and (hp.get("MARKOUT_300_MEAN") or 0) > 0
    )
    return {
        "N": len(matched),
        "V1_N": len(lost),
        "GOOD_UPMOVE_6_ASK_EDGE": ask_edge,
        **hp,
        "rows": matched,
    }


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 0.0


def _ge0(v: Any) -> bool:
    return v is not None and float(v) >= 0.0


def entry_edge_gate(
    pack: dict[str, Any],
    d180: dict[str, Any],
    d300: dict[str, Any],
    c180: dict[str, Any],
    c300: dict[str, Any],
    *,
    executable_n: int,
    signal_n: int,
    integrity_ok: bool,
) -> dict[str, Any]:
    a = _gt0(pack.get("MARKOUT_60_MEAN"))
    b = _gt0(pack.get("MARKOUT_180_MEAN"))
    c = _gt0(pack.get("MARKOUT_300_MEAN"))
    d = _ge0(pack.get("MARKOUT_180_MEDIAN"))
    e = _ge0(pack.get("MARKOUT_300_MEDIAN"))
    f = int(d180.get("POSITIVE_DAY_N") or 0) > int(d180.get("NEGATIVE_DAY_N") or 0)
    g = int(d300.get("POSITIVE_DAY_N") or 0) > int(d300.get("NEGATIVE_DAY_N") or 0)
    h = _gt0(c180.get("EX_BEST_DAY_MARKOUT"))
    i = _gt0(c300.get("EX_BEST_DAY_MARKOUT"))
    j = int(executable_n) >= 20 and (float(executable_n) / float(max(signal_n, 1))) >= 0.8
    k = bool(integrity_ok)
    ok = bool(a and b and c and d and e and f and g and h and i and j and k)
    return {
        "A_MARKOUT_60_MEAN": a,
        "B_MARKOUT_180_MEAN": b,
        "C_MARKOUT_300_MEAN": c,
        "D_MARKOUT_180_MEDIAN": d,
        "E_MARKOUT_300_MEDIAN": e,
        "F_POS_DAYS_180": f,
        "G_POS_DAYS_300": g,
        "H_EX_BEST_180": h,
        "I_EX_BEST_300": i,
        "J_COVERAGE": j,
        "K_INTEGRITY": k,
        "ENTRY_SIGNAL_EDGE_SUPPORTED": ok,
    }


def passive_incompat_gate(split: dict[str, Any]) -> dict[str, Any]:
    d60 = bool(split.get("NONFILL_GT_FILL_60"))
    d180 = bool(split.get("NONFILL_GT_FILL_180"))
    d300 = bool(split.get("NONFILL_GT_FILL_300"))
    multi = bool(split.get("MULTI_DAY"))
    ok = bool(d60 and d180 and d300 and multi)
    return {
        "DIR_60": d60,
        "DIR_180": d180,
        "DIR_300": d300,
        "MULTI_DAY": multi,
        "PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED": ok,
    }


def _median_split_gap(rows: list[dict[str, Any]], getter, mark_key: str = "markout_180") -> dict[str, Any]:
    pairs = []
    for r in rows:
        try:
            x = getter(r)
            y = r.get(mark_key)
            if x is None or y is None:
                continue
            xf = float(x)
            yf = float(y)
            if xf != xf or yf != yf:
                continue
            pairs.append((xf, yf, r))
        except (TypeError, ValueError):
            continue
    if len(pairs) < 4:
        return {"n": len(pairs), "gap": None, "low_mean": None, "high_mean": None}
    med = float(np.median([p[0] for p in pairs]))
    low = [p[1] for p in pairs if p[0] <= med]
    high = [p[1] for p in pairs if p[0] > med]
    lm = _mean(low)
    hm = _mean(high)
    gap = None if lm is None or hm is None else float(lm) - float(hm)
    return {"n": len(pairs), "median_x": med, "low_n": len(low), "high_n": len(high), "low_mean": lm, "high_mean": hm, "gap": gap}


def component_rca(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tests = [
        ("VOLUME", "VOLUME_QUALITY_INSUFFICIENT", lambda r: r.get("vol_accel") if r.get("vol_accel") is not None else r.get("volume")),
        ("REVERSAL", "REVERSAL_CONFIRMATION_INSUFFICIENT", lambda r: r.get("rci9")),
        ("TREND", "TREND_STATE_INSUFFICIENT", lambda r: None if r.get("ema9") is None or r.get("ema21") is None else float(r["ema9"]) - float(r["ema21"])),
        ("PULLBACK", "PULLBACK_STATE_INSUFFICIENT", lambda r: None if r.get("close") is None or r.get("ema9") is None else float(r["close"]) - float(r["ema9"])),
    ]
    scored = []
    detail = {}
    for name, defn, getter in tests:
        g180 = _median_split_gap(rows, getter, "markout_180")
        g300 = _median_split_gap(rows, getter, "markout_300")
        detail[name] = {"h180": g180, "h300": g300}
        worst = None
        for g in (g180, g300):
            if g.get("low_mean") is None:
                continue
            # worse half identified as the more negative mean
            w = min(float(g["low_mean"]), float(g["high_mean"]) if g.get("high_mean") is not None else float(g["low_mean"]))
            if worst is None or w < worst:
                worst = w
        scored.append((worst if worst is not None else 0.0, name, defn))
    scored.sort()
    pick = scored[0] if scored else (0.0, "VOLUME", "VOLUME_QUALITY_INSUFFICIENT")
    return {
        "PRIMARY_COMPONENT": pick[1],
        "PRIMARY_DEFICIENCY": pick[2],
        "worst_half_markout": pick[0],
        "splits": detail,
        "note": "Chosen from markout path splits, not C14. Priority hint Volume/RCI/Trend/Pullback is not an auto-rotate.",
    }


def decide_case(
    *,
    edge: dict[str, Any],
    passive: dict[str, Any],
    c180: dict[str, Any],
    integrity_ok: bool,
    rca: dict[str, Any],
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_V3_INTEGRITY_FAILED",
            "PRIMARY_DEFICIENCY_AFTER_V3": "INTEGRITY_FAILURE",
            "NEXT": "Fix integrity. Do not implement EXIT. Do not adopt Runtime.",
        }
    edge_ok = bool(edge.get("ENTRY_SIGNAL_EDGE_SUPPORTED"))
    pas_ok = bool(passive.get("PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED"))
    fragile = (
        not edge_ok
        and bool(edge.get("A_MARKOUT_60_MEAN"))
        and bool(edge.get("B_MARKOUT_180_MEAN"))
        and bool(edge.get("C_MARKOUT_300_MEAN"))
        and (
            not edge.get("H_EX_BEST_180")
            or not edge.get("I_EX_BEST_300")
            or not edge.get("F_POS_DAYS_180")
            or not edge.get("G_POS_DAYS_300")
            or (c180.get("TOP_SYMBOL_CONTRIBUTION") is not None and abs(float(c180["TOP_SYMBOL_CONTRIBUTION"])) > 0.5)
            or (c180.get("BEST_DAY_CONTRIBUTION") is not None and abs(float(c180["BEST_DAY_CONTRIBUTION"])) > 0.5)
        )
    )
    if edge_ok and pas_ok:
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V3_ENTRY_EDGE_SUPPORTED_PASSIVE_INCOMPATIBLE",
            "PRIMARY_DEFICIENCY_AFTER_V3": "PASSIVE_FILL_INCOMPATIBLE_WITH_MOMENTUM_ENTRY",
            "NEXT": "SIMPLE_TECH_EXECUTION_ARCHITECTURE_V1. Freeze MA/BB/RCI/Volume/Price Action/Board. Do not adopt Ask Runtime yet. Do not implement EXIT.",
            "ENTRY_SIGNAL_SPEC_FROZEN": True,
            "ENTRY_EXECUTION_SPEC_FROZEN": False,
        }
    if edge_ok and not pas_ok:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V3_ENTRY_EDGE_SUPPORTED_EXECUTION_UNRESOLVED",
            "PRIMARY_DEFICIENCY_AFTER_V3": "EXECUTION_UNRESOLVED",
            "NEXT": "execution RCA. Freeze indicators. Do not implement EXIT. Ask Runtime adoption forbidden.",
            "ENTRY_SIGNAL_SPEC_FROZEN": True,
            "ENTRY_EXECUTION_SPEC_FROZEN": False,
        }
    if fragile:
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V3_ENTRY_EDGE_FRAGILE",
            "PRIMARY_DEFICIENCY_AFTER_V3": "ENTRY_EDGE_FRAGILE_CONCENTRATION",
            "NEXT": "regime / component RCA. Family stays open. Do not implement EXIT.",
            "ENTRY_SIGNAL_SPEC_FROZEN": False,
            "ENTRY_EXECUTION_SPEC_FROZEN": False,
        }
    return {
        "CASE": "C",
        "VERDICT": "SIMPLE_TECH_V3_ENTRY_SIGNAL_INSUFFICIENT",
        "PRIMARY_DEFICIENCY_AFTER_V3": rca.get("PRIMARY_DEFICIENCY"),
        "NEXT": (
            f"Simple Tech component deficiency RCA: {rca.get('PRIMARY_COMPONENT')}. "
            "Family stays open. Do not patch from C14. Do not implement EXIT."
        ),
        "ENTRY_SIGNAL_SPEC_FROZEN": False,
        "ENTRY_EXECUTION_SPEC_FROZEN": False,
        "component_rca": rca,
    }


# V1_LOCKED referenced for documentation in reports.
_ = V1_LOCKED
_ = good_upmove
