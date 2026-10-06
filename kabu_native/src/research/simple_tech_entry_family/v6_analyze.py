"""V6 Trend/Pullback stage + quality gates. GOOD6 diagnostic only. No thresholds. No C14."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from research.simple_tech_entry_family.v4_analyze import _mean, _median, _pos, _same_sign
from research.simple_tech_entry_family.v6_spec import PQ_PICK_ORDER, TQ_PICK_ORDER

TQ_AXES = (
    ("TQ1", "EMA_SEPARATION"),
    ("TQ2", "EMA21_SLOPE"),
    ("TQ3", "EMA9_SLOPE"),
    ("TQ4", "SEPARATION_EXPANSION"),
)
PQ_AXES = (
    ("PQ1", "LOW_EMA9_DISTANCE"),
    ("PQ2", "BOLLINGER_PCTB"),
    ("PQ3", "PCTB_RECOVERY"),
    ("PQ4", "EMA9_TOUCH_RECENCY"),
)
AXIS_HIGHER_BETTER = {
    "TQ1": True,
    "TQ2": True,
    "TQ3": True,
    "TQ4": True,
    "PQ1": False,
    "PQ2": False,
    "PQ3": True,
    "PQ4": False,
}


def _neg(v: Any) -> bool:
    return v is not None and float(v) < -1e-12


def _gt(a: Any, b: Any) -> bool:
    return a is not None and b is not None and float(a) > float(b)


def quality_gates(pack: dict[str, Any], *, higher_better: bool = True) -> dict[str, Any]:
    s180 = pack.get("SPEARMAN_180")
    s300 = pack.get("SPEARMAN_300")
    h180 = pack.get("HALF_180") or {}
    h300 = pack.get("HALF_300") or {}
    d180 = pack.get("DAY_180") or {}
    if higher_better:
        a = bool(h180.get("top_gt_bot_mean")) and _pos(s180)
        b = bool(h300.get("top_gt_bot_mean")) and _pos(s300)
        c = bool(h180.get("top_gt_bot_median")) and bool(h300.get("top_gt_bot_median"))
        e = _same_sign(s180, pack.get("SPEARMAN_180_EX_BEST")) and _pos(pack.get("SPEARMAN_180_EX_BEST"))
        f = _same_sign(s180, pack.get("SPEARMAN_180_DROP_TOP_SYMBOL")) and _pos(pack.get("SPEARMAN_180_DROP_TOP_SYMBOL"))
        h = _pos(s180) and _pos(s300)
        d = int(d180.get("POSITIVE_RELATION_DAY_N") or 0) > int(d180.get("NEGATIVE_RELATION_DAY_N") or 0) and int(
            d180.get("POSITIVE_RELATION_DAY_N") or 0
        ) >= 2
    else:
        a = (
            h180.get("top_mean") is not None
            and h180.get("bot_mean") is not None
            and float(h180["bot_mean"]) > float(h180["top_mean"])
            and _neg(s180)
        )
        b = (
            h300.get("top_mean") is not None
            and h300.get("bot_mean") is not None
            and float(h300["bot_mean"]) > float(h300["top_mean"])
            and _neg(s300)
        )
        c = (
            h180.get("top_median") is not None
            and h180.get("bot_median") is not None
            and h300.get("top_median") is not None
            and h300.get("bot_median") is not None
            and float(h180["bot_median"]) > float(h180["top_median"])
            and float(h300["bot_median"]) > float(h300["top_median"])
        )
        e = _same_sign(s180, pack.get("SPEARMAN_180_EX_BEST")) and _neg(pack.get("SPEARMAN_180_EX_BEST"))
        f = _same_sign(s180, pack.get("SPEARMAN_180_DROP_TOP_SYMBOL")) and _neg(pack.get("SPEARMAN_180_DROP_TOP_SYMBOL"))
        h = _neg(s180) and _neg(s300)
        d = int(d180.get("NEGATIVE_RELATION_DAY_N") or 0) > int(d180.get("POSITIVE_RELATION_DAY_N") or 0) and int(
            d180.get("NEGATIVE_RELATION_DAY_N") or 0
        ) >= 2
    ok = bool(a and b and c and d and e and f and h)
    return {
        "A_180_IMPROVE": a,
        "B_300_IMPROVE": b,
        "C_MEDIAN_SAME_DIR": c,
        "D_MULTI_DAY": d,
        "E_EX_BEST": e,
        "F_NOT_ONE_SYMBOL": f,
        "H_POPULATION": h,
        "HIGHER_BETTER": higher_better,
        "SUPPORTED": ok,
    }


def good_vs_other(g6: list[dict[str, Any]], o23: list[dict[str, Any]], axes: tuple) -> dict[str, Any]:
    out: dict[str, Any] = {"GOOD6_N": len(g6), "OTHER23_N": len(o23), "diagnostic_only": True, "not_mechanism_gate": True}
    aligned = []
    for feat, name in axes:
        gm = _median([r.get(feat) for r in g6])
        om = _median([r.get(feat) for r in o23])
        higher = bool(AXIS_HIGHER_BETTER.get(feat, True))
        out[f"GOOD6_{feat}"] = gm
        out[f"OTHER23_{feat}"] = om
        if gm is None or om is None:
            better = False
        elif higher:
            better = float(gm) > float(om)
        else:
            better = float(gm) < float(om)
        out[f"GOOD6_BETTER_{feat}"] = better
        out[f"GOOD6_GT_{feat}"] = bool(gm is not None and om is not None and float(gm) > float(om))
        if better:
            aligned.append(name)
    out["good6_aligned_axes"] = aligned
    return out


def _horizon_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "N": len(rows),
        "MARKOUT_60_MEAN": _mean([r.get("markout_60") for r in rows]),
        "MARKOUT_180_MEAN": _mean([r.get("markout_180") for r in rows]),
        "MARKOUT_300_MEAN": _mean([r.get("markout_300") for r in rows]),
        "MARKOUT_60_MEDIAN": _median([r.get("markout_60") for r in rows]),
        "MARKOUT_180_MEDIAN": _median([r.get("markout_180") for r in rows]),
        "MARKOUT_300_MEDIAN": _median([r.get("markout_300") for r in rows]),
        "MFE_MEAN": _mean([r.get("mfe_bps") for r in rows]),
        "MFE_MEDIAN": _median([r.get("mfe_bps") for r in rows]),
        "MAE_MEAN": _mean([r.get("mae_bps") for r in rows]),
        "MAE_MEDIAN": _median([r.get("mae_bps") for r in rows]),
    }


def stage_audit(pass_rows: list[dict[str, Any]], fail_rows: list[dict[str, Any]], *, all_n: int, pass_n: int, fail_n: int) -> dict[str, Any]:
    p = _horizon_pack(pass_rows)
    f = _horizon_pack(fail_rows)
    by_p: dict[str, list] = defaultdict(list)
    by_f: dict[str, list] = defaultdict(list)
    for r in pass_rows:
        by_p[str(r.get("date") or "")].append(r)
    for r in fail_rows:
        by_f[str(r.get("date") or "")].append(r)
    pos = neg = zero = compared = 0
    day_rows = []
    for d in sorted(set(by_p) | set(by_f)):
        pm = _mean([r.get("markout_180") for r in by_p.get(d) or []])
        fm = _mean([r.get("markout_180") for r in by_f.get(d) or []])
        rec = {"date": d, "PASS_N": len(by_p.get(d) or []), "FAIL_N": len(by_f.get(d) or []), "PASS_180": pm, "FAIL_180": fm}
        if pm is None or fm is None:
            zero += 1
            rec["sign"] = 0
        else:
            compared += 1
            if float(pm) > float(fm) + 1e-12:
                pos += 1
                rec["sign"] = 1
            elif float(pm) < float(fm) - 1e-12:
                neg += 1
                rec["sign"] = -1
            else:
                zero += 1
                rec["sign"] = 0
        day_rows.append(rec)
    pooled = pass_rows + fail_rows
    day_mean = {}
    by_all: dict[str, list] = defaultdict(list)
    for r in pooled:
        by_all[str(r.get("date") or "")].append(r)
    for d, xs in by_all.items():
        day_mean[d] = _mean([r.get("markout_180") for r in xs])
    usable = [d for d, m in day_mean.items() if m is not None]
    best = max(usable, key=lambda d: float(day_mean[d])) if usable else None
    rest_p = [r for r in pass_rows if str(r.get("date") or "") != str(best)]
    rest_f = [r for r in fail_rows if str(r.get("date") or "") != str(best)]
    counts: dict[str, int] = defaultdict(int)
    for r in pooled:
        counts[str(r.get("symbol") or "")] += 1
    top = max(counts, key=lambda s: counts[s]) if counts else None
    nosym_p = [r for r in pass_rows if str(r.get("symbol") or "") != str(top)]
    nosym_f = [r for r in fail_rows if str(r.get("symbol") or "") != str(top)]
    rp = _horizon_pack(rest_p)
    rf = _horizon_pack(rest_f)
    sp = _horizon_pack(nosym_p)
    sf = _horizon_pack(nosym_f)
    improves = _gt(p.get("MARKOUT_180_MEAN"), f.get("MARKOUT_180_MEAN")) and _gt(p.get("MARKOUT_300_MEAN"), f.get("MARKOUT_300_MEAN"))
    return {
        "ALL_N": all_n,
        "PASS_N": pass_n,
        "FAIL_N": fail_n,
        "EXECUTABLE_PASS_N": len(pass_rows),
        "EXECUTABLE_FAIL_N": len(fail_rows),
        "PASS": p,
        "FAIL": f,
        "PASS_GT_FAIL_60": _gt(p.get("MARKOUT_60_MEAN"), f.get("MARKOUT_60_MEAN")),
        "PASS_GT_FAIL_180": _gt(p.get("MARKOUT_180_MEAN"), f.get("MARKOUT_180_MEAN")),
        "PASS_GT_FAIL_300": _gt(p.get("MARKOUT_300_MEAN"), f.get("MARKOUT_300_MEAN")),
        "STAGE_IMPROVES": improves,
        "DAY_POS": pos,
        "DAY_NEG": neg,
        "DAY_ZERO": zero,
        "DAY_COMPARED": compared,
        "MULTI_DAY": bool(pos >= 2 and pos > neg),
        "BEST_DAY": best,
        "EX_BEST_PASS_180": rp.get("MARKOUT_180_MEAN"),
        "EX_BEST_FAIL_180": rf.get("MARKOUT_180_MEAN"),
        "EX_BEST_PASS_300": rp.get("MARKOUT_300_MEAN"),
        "EX_BEST_FAIL_300": rf.get("MARKOUT_300_MEAN"),
        "EX_BEST_STILL_IMPROVES": _gt(rp.get("MARKOUT_180_MEAN"), rf.get("MARKOUT_180_MEAN")) and _gt(
            rp.get("MARKOUT_300_MEAN"), rf.get("MARKOUT_300_MEAN")
        ),
        "TOP_SYMBOL": top,
        "DROP_TOP_PASS_180": sp.get("MARKOUT_180_MEAN"),
        "DROP_TOP_FAIL_180": sf.get("MARKOUT_180_MEAN"),
        "DROP_TOP_PASS_300": sp.get("MARKOUT_300_MEAN"),
        "DROP_TOP_FAIL_300": sf.get("MARKOUT_300_MEAN"),
        "DROP_TOP_STILL_IMPROVES": _gt(sp.get("MARKOUT_180_MEAN"), sf.get("MARKOUT_180_MEAN")) and _gt(
            sp.get("MARKOUT_300_MEAN"), sf.get("MARKOUT_300_MEAN")
        ),
        "EFFECT_180": (
            None
            if p.get("MARKOUT_180_MEAN") is None or f.get("MARKOUT_180_MEAN") is None
            else float(p["MARKOUT_180_MEAN"]) - float(f["MARKOUT_180_MEAN"])
        ),
        "EFFECT_300": (
            None
            if p.get("MARKOUT_300_MEAN") is None or f.get("MARKOUT_300_MEAN") is None
            else float(p["MARKOUT_300_MEAN"]) - float(f["MARKOUT_300_MEAN"])
        ),
        "day_rows": day_rows,
    }


def _best_axis_strength(gates: dict[str, dict[str, Any]], packs: dict[str, dict[str, Any]], order: tuple) -> tuple[Optional[str], float]:
    hits = [k for k in order if (gates.get(k) or {}).get("SUPPORTED")]
    if not hits:
        return None, 0.0
    best = hits[0]
    best_s = -1.0
    for k in hits:
        p = packs.get(k) or {}
        s180 = p.get("SPEARMAN_180")
        s300 = p.get("SPEARMAN_300")
        mag = (abs(float(s180)) if s180 is not None else 0.0) + (abs(float(s300)) if s300 is not None else 0.0)
        if mag > best_s:
            best_s = mag
            best = k
    return best, best_s


def pick_next(
    trend_stage: dict[str, Any],
    pb_stage: dict[str, Any],
    tq_gates: dict[str, dict[str, Any]],
    pq_gates: dict[str, dict[str, Any]],
    tq_packs: dict[str, dict[str, Any]],
    pq_packs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tq_hits = [k for k in TQ_PICK_ORDER if (tq_gates.get(k) or {}).get("SUPPORTED")]
    pq_hits = [k for k in PQ_PICK_ORDER if (pq_gates.get(k) or {}).get("SUPPORTED")]
    trend_ok = bool(trend_stage.get("STAGE_IMPROVES")) and bool(tq_hits)
    pb_ok = bool(pb_stage.get("STAGE_IMPROVES")) and bool(pq_hits)
    t_name = tq_hits[0] if tq_hits else "NONE"
    p_name = pq_hits[0] if pq_hits else "NONE"

    def _trend_win(reason: str) -> dict[str, Any]:
        return {
            "NEXT_COMPONENT": "TREND",
            "SUPPORTED_TREND_MECHANISM": t_name,
            "SUPPORTED_PULLBACK_MECHANISM": p_name if pb_ok or pq_hits else "NONE",
            "VERDICT": "SIMPLE_TECH_V6_NEXT_TREND",
            "PRIMARY_DEFICIENCY": "TREND_QUALITY_NOT_MEASURED",
            "NEXT": "V6_TREND_RULE_PRECOMMIT. Do not retune EMA 13/34. Do not change BB/RCI/Volume/Persistence. No EXIT.",
            "TIEBREAK": reason,
        }

    def _pb_win(reason: str) -> dict[str, Any]:
        return {
            "NEXT_COMPONENT": "PULLBACK",
            "SUPPORTED_TREND_MECHANISM": t_name if trend_ok or tq_hits else "NONE",
            "SUPPORTED_PULLBACK_MECHANISM": p_name,
            "VERDICT": "SIMPLE_TECH_V6_NEXT_PULLBACK",
            "PRIMARY_DEFICIENCY": "PULLBACK_QUALITY_NOT_MEASURED",
            "NEXT": "V6_PULLBACK_RULE_PRECOMMIT. Do not retune BB20/2sigma or EMA. Do not change RCI/Volume/Persistence. No EXIT.",
            "TIEBREAK": reason,
        }

    if trend_ok and not pb_ok:
        return _trend_win("trend_only")
    if pb_ok and not trend_ok:
        return _pb_win("pullback_only")
    if trend_ok and pb_ok:
        te180, te300 = trend_stage.get("EFFECT_180"), trend_stage.get("EFFECT_300")
        pe180, pe300 = pb_stage.get("EFFECT_180"), pb_stage.get("EFFECT_300")
        both_trend = _gt(te180, pe180) and _gt(te300, pe300)
        both_pb = _gt(pe180, te180) and _gt(pe300, te300)
        if both_trend:
            return _trend_win("greater_effect_both_horizons")
        if both_pb:
            return _pb_win("greater_effect_both_horizons")
        tsum = float(te180 or 0) + float(te300 or 0)
        psum = float(pe180 or 0) + float(pe300 or 0)
        if tsum > psum + 1e-12:
            return _trend_win("larger_effect_sum")
        if psum > tsum + 1e-12:
            return _pb_win("larger_effect_sum")
        tday = int(trend_stage.get("DAY_POS") or 0) - int(trend_stage.get("DAY_NEG") or 0)
        pday = int(pb_stage.get("DAY_POS") or 0) - int(pb_stage.get("DAY_NEG") or 0)
        if tday > pday:
            return _trend_win("greater_day_consistency")
        if pday > tday:
            return _pb_win("greater_day_consistency")
        _, ts = _best_axis_strength(tq_gates, tq_packs, TQ_PICK_ORDER)
        _, ps = _best_axis_strength(pq_gates, pq_packs, PQ_PICK_ORDER)
        if ts > ps + 1e-12:
            return _trend_win("stronger_quality_spearman")
        if ps > ts + 1e-12:
            return _pb_win("stronger_quality_spearman")
        return _pb_win("downstream_of_trend")
    return {
        "NEXT_COMPONENT": "NONE",
        "SUPPORTED_TREND_MECHANISM": t_name if tq_hits else "NONE",
        "SUPPORTED_PULLBACK_MECHANISM": p_name if pq_hits else "NONE",
        "VERDICT": "SIMPLE_TECH_V6_NO_STAGE_MECHANISM",
        "PRIMARY_DEFICIENCY": "TREND_AND_PULLBACK_STAGE_UNRESOLVED",
        "NEXT": "Architecture-level RCA. Do not tune EMA 13/34 or BB20/2sigma. Do not change RCI or add Persistence. Family stays open. No EXIT.",
        "TIEBREAK": "both_weak",
    }


def answers(trend_stage: dict[str, Any], pb_stage: dict[str, Any], tq_gates: dict[str, Any], pq_gates: dict[str, Any], decision: dict[str, Any], gvo: dict[str, Any]) -> dict[str, Any]:
    tq_hits = [k for k in TQ_PICK_ORDER if (tq_gates.get(k) or {}).get("SUPPORTED")]
    pq_hits = [k for k in PQ_PICK_ORDER if (pq_gates.get(k) or {}).get("SUPPORTED")]
    return {
        "Q1": bool(trend_stage.get("STAGE_IMPROVES")),
        "Q2": bool(pb_stage.get("STAGE_IMPROVES")),
        "Q3": tq_hits[0] if tq_hits else "NONE",
        "Q4": pq_hits[0] if pq_hits else "NONE",
        "Q5": decision.get("NEXT_COMPONENT"),
        "Q6": "GOOD6 diagnostic only; not a mechanism gate",
        "Q6_ALIGN_TQ": [k for k in TQ_PICK_ORDER if gvo.get(f"GOOD6_BETTER_{k}")],
        "Q6_ALIGN_PQ": [k for k in PQ_PICK_ORDER if gvo.get(f"GOOD6_BETTER_{k}")],
        "Q6_AXIS": "diagnostic_only",
    }
