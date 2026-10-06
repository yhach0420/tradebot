"""V4 Volume quality RCA: Spearman, quartiles, day stability, mechanism gates. No thresholds. No C14."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family.stages import good_upmove

AXES = (
    ("VQ1", "MAGNITUDE"),
    ("VQ2", "PERSISTENCE"),
    ("VQ3", "DIRECTION"),
    ("VQ4", "PRICE_RESPONSE"),
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if _finite(x)]
    if not vs:
        return None
    return float(np.mean(vs))


def _median(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if _finite(x)]
    if not vs:
        return None
    return float(np.median(vs))


def _qtiles(xs: list[Any]) -> dict[str, Any]:
    vs = [float(x) for x in xs if _finite(x)]
    if not vs:
        return {"q25": None, "q50": None, "q75": None}
    a = np.asarray(vs, dtype=float)
    return {
        "q25": float(np.percentile(a, 25)),
        "q50": float(np.percentile(a, 50)),
        "q75": float(np.percentile(a, 75)),
    }


def spearman(xs: list[Any], ys: list[Any]) -> Optional[float]:
    pairs = [(float(a), float(b)) for a, b in zip(xs, ys) if _finite(a) and _finite(b)]
    if len(pairs) < 5:
        return None
    x = np.asarray([p[0] for p in pairs], dtype=float)
    y = np.asarray([p[1] for p in pairs], dtype=float)
    rx = np.argsort(np.argsort(x))
    ry = np.argsort(np.argsort(y))
    if float(np.std(rx)) < 1e-12 or float(np.std(ry)) < 1e-12:
        return None
    return float(np.corrcoef(rx.astype(float), ry.astype(float))[0, 1])


def coverage(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    hit = sum(1 for r in rows if _finite(r.get(key)))
    return {
        "N": n,
        "COVERAGE": (hit / n) if n else None,
        "MISSING": n - hit,
        "MEDIAN": _median([r.get(key) for r in rows]),
        **{k.upper() if k.startswith("q") else k: v for k, v in _qtiles([r.get(key) for r in rows]).items()},
    }


def quartiles(rows: list[dict[str, Any]], feat: str, mark: str) -> list[dict[str, Any]]:
    usable = [r for r in rows if _finite(r.get(feat))]
    if len(usable) < 4:
        return [{"feature": feat, "quartile": i, "N": 0} for i in range(1, 5)]
    vals = np.asarray([float(r[feat]) for r in usable], dtype=float)
    edges = np.unique(np.percentile(vals, [0, 25, 50, 75, 100]))
    if int(edges.size) < 3:
        return [{"feature": feat, "quartile": 1, "N": len(usable), "feat_median": _median(list(vals))}]
    bins = np.digitize(vals, edges[1:-1], right=True) + 1
    grouped: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
    for r, b in zip(usable, bins):
        grouped[int(min(max(int(b), 1), 4))].append(r)
    out = []
    for q in range(1, 5):
        grp = grouped[q]
        n180 = sum(1 for r in grp if _finite(r.get("markout_180")))
        out.append(
            {
                "feature": feat,
                "mark": mark,
                "quartile": q,
                "N": len(grp),
                "feat_median": _median([r.get(feat) for r in grp]),
                "MARKOUT60_MEAN": _mean([r.get("markout_60") for r in grp]),
                "MARKOUT180_MEAN": _mean([r.get("markout_180") for r in grp]),
                "MARKOUT300_MEAN": _mean([r.get("markout_300") for r in grp]),
                "MARKOUT180_MEDIAN": _median([r.get("markout_180") for r in grp]),
                "MARKOUT300_MEDIAN": _median([r.get("markout_300") for r in grp]),
                "POS_RATE_180": (
                    (sum(1 for r in grp if _finite(r.get("markout_180")) and float(r["markout_180"]) > 0) / n180)
                    if n180
                    else None
                ),
                "MFE_MEDIAN": _median([r.get("mfe_bps") for r in grp]),
                "MAE_MEDIAN": _median([r.get("mae_bps") for r in grp]),
            }
        )
    return out


def half_split(rows: list[dict[str, Any]], feat: str, mark: str) -> dict[str, Any]:
    usable = [r for r in rows if _finite(r.get(feat)) and _finite(r.get(mark))]
    if len(usable) < 4:
        return {"top_mean": None, "bot_mean": None, "top_median": None, "bot_median": None, "top_gt_bot_mean": None, "top_gt_bot_median": None, "n": len(usable)}
    med = float(np.median([float(r[feat]) for r in usable]))
    top = [r for r in usable if float(r[feat]) > med]
    bot = [r for r in usable if float(r[feat]) <= med]
    tm, bm = _mean([r.get(mark) for r in top]), _mean([r.get(mark) for r in bot])
    td, bd = _median([r.get(mark) for r in top]), _median([r.get(mark) for r in bot])
    return {
        "n": len(usable),
        "feat_median": med,
        "top_n": len(top),
        "bot_n": len(bot),
        "top_mean": tm,
        "bot_mean": bm,
        "top_median": td,
        "bot_median": bd,
        "top_gt_bot_mean": bool(tm is not None and bm is not None and float(tm) > float(bm)),
        "top_gt_bot_median": bool(td is not None and bd is not None and float(td) > float(bd)),
    }


def day_stability(rows: list[dict[str, Any]], feat: str, mark: str = "markout_180") -> dict[str, Any]:
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        by[str(r.get("date") or "")].append(r)
    pos = neg = zero = 0
    day_rows = []
    for d, xs in sorted(by.items()):
        rho = spearman([r.get(feat) for r in xs], [r.get(mark) for r in xs])
        hs = half_split(xs, feat, mark)
        if rho is None:
            zero += 1
            sign = 0
        elif rho > 1e-12:
            pos += 1
            sign = 1
        elif rho < -1e-12:
            neg += 1
            sign = -1
        else:
            zero += 1
            sign = 0
        day_rows.append({"date": d, "n": len(xs), "spearman": rho, "sign": sign, **{f"half_{k}": v for k, v in hs.items()}})
    return {
        "POSITIVE_RELATION_DAY_N": pos,
        "NEGATIVE_RELATION_DAY_N": neg,
        "ZERO_OR_UNAVAILABLE_DAY_N": zero,
        "days": day_rows,
    }


def axis_pack(rows: list[dict[str, Any]], feat: str) -> dict[str, Any]:
    cov = coverage(rows, feat)
    s60 = spearman([r.get(feat) for r in rows], [r.get("markout_60") for r in rows])
    s180 = spearman([r.get(feat) for r in rows], [r.get("markout_180") for r in rows])
    s300 = spearman([r.get(feat) for r in rows], [r.get("markout_300") for r in rows])
    h180 = half_split(rows, feat, "markout_180")
    h300 = half_split(rows, feat, "markout_300")
    d180 = day_stability(rows, feat, "markout_180")
    d300 = day_stability(rows, feat, "markout_300")
    q180 = quartiles(rows, feat, "markout_180")
    # best-day exclusion (best = highest mean markout_180)
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        by[str(r.get("date") or "")].append(r)
    day_mean = {d: _mean([r.get("markout_180") for r in xs]) for d, xs in by.items()}
    usable_days = [d for d, m in day_mean.items() if m is not None]
    best = max(usable_days, key=lambda d: float(day_mean[d])) if usable_days else None
    rest = [r for r in rows if str(r.get("date") or "") != str(best)]
    s180_ex = spearman([r.get(feat) for r in rest], [r.get("markout_180") for r in rest])
    s300_ex = spearman([r.get(feat) for r in rest], [r.get("markout_300") for r in rest])
    # drop top symbol by |count|
    scount: dict[str, int] = defaultdict(int)
    for r in rows:
        scount[str(r.get("symbol") or "")] += 1
    top_sym = max(scount, key=lambda s: scount[s]) if scount else None
    nosym = [r for r in rows if str(r.get("symbol") or "") != str(top_sym)]
    s180_ns = spearman([r.get(feat) for r in nosym], [r.get("markout_180") for r in nosym])
    s300_ns = spearman([r.get(feat) for r in nosym], [r.get("markout_300") for r in nosym])
    return {
        "feature": feat,
        **cov,
        "SPEARMAN_60": s60,
        "SPEARMAN_180": s180,
        "SPEARMAN_300": s300,
        "HALF_180": h180,
        "HALF_300": h300,
        "DAY_180": {k: d180[k] for k in d180 if k != "days"},
        "DAY_300": {k: d300[k] for k in d300 if k != "days"},
        "day_rows_180": d180["days"],
        "EX_BEST_DAY": best,
        "SPEARMAN_180_EX_BEST": s180_ex,
        "SPEARMAN_300_EX_BEST": s300_ex,
        "TOP_SYMBOL": top_sym,
        "SPEARMAN_180_DROP_TOP_SYMBOL": s180_ns,
        "SPEARMAN_300_DROP_TOP_SYMBOL": s300_ns,
        "quartiles": q180,
    }


def _pos(v: Any) -> bool:
    return v is not None and float(v) > 1e-12


def _same_sign(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if abs(float(a)) <= 1e-12 or abs(float(b)) <= 1e-12:
        return False
    return (float(a) > 0) == (float(b) > 0)


def mechanism_gates(pack: dict[str, Any], good6: list[dict[str, Any]], other23: list[dict[str, Any]], feat: str) -> dict[str, Any]:
    s180 = pack.get("SPEARMAN_180")
    s300 = pack.get("SPEARMAN_300")
    h180 = pack.get("HALF_180") or {}
    h300 = pack.get("HALF_300") or {}
    d180 = pack.get("DAY_180") or {}
    a = bool(h180.get("top_gt_bot_mean")) and _pos(s180)
    b = bool(h300.get("top_gt_bot_mean")) and _pos(s300)
    c = bool(h180.get("top_gt_bot_median")) and bool(h300.get("top_gt_bot_median"))
    d = int(d180.get("POSITIVE_RELATION_DAY_N") or 0) > int(d180.get("NEGATIVE_RELATION_DAY_N") or 0) and int(d180.get("POSITIVE_RELATION_DAY_N") or 0) >= 2
    e = _same_sign(s180, pack.get("SPEARMAN_180_EX_BEST")) and _pos(pack.get("SPEARMAN_180_EX_BEST"))
    f = _same_sign(s180, pack.get("SPEARMAN_180_DROP_TOP_SYMBOL")) and _pos(pack.get("SPEARMAN_180_DROP_TOP_SYMBOL"))
    g6m = _median([r.get(feat) for r in good6])
    o23 = _median([r.get(feat) for r in other23])
    g = g6m is not None and o23 is not None and float(g6m) > float(o23)
    h = _pos(s180) and _pos(s300)  # PRE_VOLUME pack already
    ok = bool(a and b and c and d and e and f and g and h)
    return {
        "A_180_IMPROVE": a,
        "B_300_IMPROVE": b,
        "C_MEDIAN_SAME_DIR": c,
        "D_MULTI_DAY": d,
        "E_EX_BEST": e,
        "F_NOT_ONE_SYMBOL": f,
        "G_GOOD6": g,
        "H_PRE_VOLUME": h,
        "GOOD6_MEDIAN": g6m,
        "OTHER23_MEDIAN": o23,
        "SUPPORTED": ok,
    }


def pick_mechanism(gates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    order = ["VQ3", "VQ2", "VQ4", "VQ1"]
    names = {"VQ1": "MAGNITUDE", "VQ2": "PERSISTENCE", "VQ3": "DIRECTION", "VQ4": "PRICE_RESPONSE"}
    hits = [k for k in order if (gates.get(k) or {}).get("SUPPORTED")]
    if not hits:
        return {
            "SUPPORTED_VOLUME_MECHANISM": "NONE",
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_VOLUME_NO_ROBUST_MECHANISM_FOUND",
            "PRIMARY_DEFICIENCY": "VOLUME_QUALITY_UNRESOLVED",
            "NEXT": "Do not auto-rotate. Judge from full component evidence whether to advance RCA to REVERSAL. Family stays open. No ENTRY change. No EXIT.",
        }
    k = hits[0]
    case_map = {
        "VQ3": ("A", "SIMPLE_TECH_VOLUME_DIRECTION_SUPPORTED", "TOTAL_VOLUME_DOES_NOT_DISTINGUISH_BUY_SELL", "V4_VOLUME_DIRECTION_RULE_PRECOMMIT"),
        "VQ2": ("B", "SIMPLE_TECH_VOLUME_PERSISTENCE_SUPPORTED", "VOLUME_CONTINUITY_NOT_MEASURED", "V4_VOLUME_PERSISTENCE_RULE_PRECOMMIT"),
        "VQ4": ("C", "SIMPLE_TECH_VOLUME_PRICE_RESPONSE_SUPPORTED", "VOLUME_NOT_LINKED_TO_UPWARD_PRICE_IMPACT", "V4_VOLUME_PRICE_RESPONSE_RULE_PRECOMMIT"),
        "VQ1": ("D", "SIMPLE_TECH_VOLUME_MAGNITUDE_SUPPORTED", "VOLUME_THRESHOLD_OR_POSITION_MAY_BE_WRONG", "V4_VOLUME_MAGNITUDE_STABILITY_STUDY"),
    }
    case, verd, defn, nxt = case_map[k]
    return {
        "SUPPORTED_VOLUME_MECHANISM": names[k],
        "CASE": case,
        "VERDICT": verd,
        "PRIMARY_DEFICIENCY": defn,
        "NEXT": f"{nxt}. Do not choose a threshold in this run. MA/BB/RCI/Price Action/Board stay frozen. No EXIT.",
    }


def answers(p1: dict[str, Any], p2: dict[str, Any], p3: dict[str, Any], p4: dict[str, Any], g6: dict[str, Any], gates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    q1 = "inverse_or_invalid"
    if _pos(p1.get("SPEARMAN_180")) and _pos(p1.get("SPEARMAN_300")):
        q1 = "positive_on_pre_volume"
    elif p1.get("SPEARMAN_180") is not None and float(p1["SPEARMAN_180"]) < 0 and p1.get("SPEARMAN_300") is not None and float(p1["SPEARMAN_300"]) < 0:
        q1 = "inverse_on_pre_volume"
    q2 = "unavailable_or_weaker"
    if p2.get("SPEARMAN_180") is not None and p1.get("SPEARMAN_180") is not None:
        d2 = p2.get("DAY_180") or {}
        d1 = p1.get("DAY_180") or {}
        more_stable = int(d2.get("POSITIVE_RELATION_DAY_N") or 0) > int(d1.get("POSITIVE_RELATION_DAY_N") or 0)
        q2 = "more_stable_than_magnitude" if more_stable and _pos(p2.get("SPEARMAN_180")) else "not_more_stable_than_magnitude"
    q3 = "yes" if _pos(p3.get("SPEARMAN_180")) and _pos(p3.get("SPEARMAN_300")) else "no_robust_separation"
    q4 = "price_response_needed_or_unsupported"
    if (gates.get("VQ4") or {}).get("SUPPORTED"):
        q4 = "yes_price_response_supported"
    elif _pos(p3.get("SPEARMAN_180")) and not (gates.get("VQ3") or {}).get("SUPPORTED"):
        q4 = "direction_alone_not_sufficient"
    q5 = "no_shared_state"
    if g6.get("align_vq"):
        q5 = f"higher_{g6['align_vq']}_than_other23"
    # Q6 is the supported VQ2 mechanism on PRE_VOLUME, not GOOD6-contrast align_vq.
    vq2g = gates.get("VQ2") or {}
    if bool(vq2g.get("D_MULTI_DAY")) and bool(vq2g.get("H_PRE_VOLUME")):
        q6 = "Persistence's sign holds on full PRE_VOLUME population across multiple days"
    else:
        q6 = "no_persistence_pre_volume_multi_day"
    return {
        "Q1": q1,
        "Q2": q2,
        "Q3": q3,
        "Q4": q4,
        "Q5": q5,
        "Q6": q6,
        "Q6_AXIS": "VQ2",
        "Q6_USES_ALIGN_VQ": False,
        "Q5_USES_ALIGN_VQ": True,
        "ALIGN_VQ_ROLE": "GOOD6_VS_OTHER23_FIRST_HIGHER_AXIS",
    }


def good_vs_other(g6: list[dict[str, Any]], o23: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"GOOD6_N": len(g6), "OTHER23_N": len(o23)}
    align = None
    align_key = None
    for feat, name in AXES:
        gm = _median([r.get(feat) for r in g6])
        om = _median([r.get(feat) for r in o23])
        out[f"GOOD6_{feat}"] = gm
        out[f"OTHER23_{feat}"] = om
        out[f"GOOD6_GT_{feat}"] = bool(gm is not None and om is not None and float(gm) > float(om))
        if out[f"GOOD6_GT_{feat}"] and align is None:
            align = name
            align_key = feat
    out["align_vq"] = align
    out["align_key"] = align_key
    out["align_vq_role"] = "GOOD6_VS_OTHER23_FIRST_HIGHER_AXIS"
    out["align_vq_is_supported_mechanism"] = False
    out["good6_higher_axes"] = [name for feat, name in AXES if out.get(f"GOOD6_GT_{feat}")]
    return out


def bad23_diag(rows: list[dict[str, Any]]) -> dict[str, Any]:
    a = b = c = d = 0
    for r in rows:
        high = _finite(r.get("VQ1")) and float(r["VQ1"]) >= 1.5
        ratio = r.get("VQ3")
        ret = r.get("RET_60_BPS")
        vq2 = r.get("VQ2")
        vq2_med = _median([x.get("VQ2") for x in rows])
        if high and (not _finite(ratio) or float(ratio) <= 0):
            a += 1
        if high and (not _finite(ret) or float(ret) <= 0):
            b += 1
        if high and _finite(vq2) and vq2_med is not None and float(vq2) < float(vq2_med):
            c += 1
        if _finite(ratio) and float(ratio) > 0 and (not _finite(ret) or float(ret) <= 0):
            d += 1
    return {
        "N": len(rows),
        "A_SPIKE_DIRECTION_LE0": a,
        "B_SPIKE_PRICE_RESPONSE_LE0": b,
        "C_HIGH_PERSISTENCE_LOW": c,
        "D_BUY_BUT_WEAK_PRICE_RESPONSE": d,
    }


def taxonomy_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "VM1_LOW_ACTIVITY",
        "VM2_HIGH_VOLUME_BUY_DOMINANT",
        "VM3_HIGH_VOLUME_SELL_DOMINANT",
        "VM4_HIGH_VOLUME_NEUTRAL",
        "VM5_PERSISTENT_BUYING",
        "VM6_VOLUME_SPIKE_NO_PRICE_RESPONSE",
        "VM7_BUY_VOLUME_POSITIVE_PRICE_RESPONSE",
    )
    out = []
    for k in keys:
        hit = [r for r in rows if k in (r.get("taxonomy") or [])]
        out.append(
            {
                "label": k,
                "N": len(hit),
                "MARKOUT180_MEAN": _mean([r.get("markout_180") for r in hit]),
                "MARKOUT300_MEAN": _mean([r.get("markout_300") for r in hit]),
            }
        )
    return out
