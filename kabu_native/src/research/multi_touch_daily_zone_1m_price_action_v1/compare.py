"""Path comparisons: multi-touch vs PDH vs single-touch vs nested transitions. Not PnL ranking."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.multi_touch_daily_zone_1m_price_action_v1 import EVAL_BLOCKS, MIN_EVENT_N, SEP_PP


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def path_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"event_n": 0, "day_n": 0, "symbol_n": 0}
    n = len(rows)
    cont = sum(1 for r in rows if r.get("continuation"))
    rev = sum(1 for r in rows if r.get("reversal"))
    stall = sum(1 for r in rows if r.get("stall"))
    path_n = max(cont + rev + stall, 1)
    mfe = [float(r["mfe_bps"]) for r in rows if _finite(r.get("mfe_bps"))]
    mae = [float(r["mae_bps"]) for r in rows if _finite(r.get("mae_bps"))]
    return {
        "event_n": n,
        "day_n": len({str(r.get("date")) for r in rows}),
        "symbol_n": len({str(r.get("symbol")) for r in rows}),
        "continuation_n": cont,
        "reversal_n": rev,
        "stall_n": stall,
        "continuation_rate": cont / path_n if (cont + rev + stall) else None,
        "reversal_rate": rev / path_n if (cont + rev + stall) else None,
        "stall_rate": stall / path_n if (cont + rev + stall) else None,
        "favorable_first_rate": sum(1 for r in rows if r.get("favorable_first")) / n,
        "median_mfe": float(np.median(mfe)) if mfe else None,
        "median_mae": float(np.median(mae)) if mae else None,
        "mean_vol_rel": float(np.nanmean([float(r["vol_rel20"]) for r in rows if _finite(r.get("vol_rel20"))])) if any(_finite(r.get("vol_rel20")) for r in rows) else None,
        "mean_va_rel": float(np.nanmean([float(r["va_rel20"]) for r in rows if _finite(r.get("va_rel20"))])) if any(_finite(r.get("va_rel20")) for r in rows) else None,
        "mean_bars_inside": float(np.nanmean([float(r["bars_inside"]) for r in rows if _finite(r.get("bars_inside"))])) if any(_finite(r.get("bars_inside")) for r in rows) else None,
    }


def _eval(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allow = set(EVAL_BLOCKS)
    return [r for r in rows if str(r.get("block") or "") in allow]


def _sep(a: dict[str, Any], b: dict[str, Any]) -> tuple[float | None, bool]:
    ca, cb = a.get("continuation_rate"), b.get("continuation_rate")
    if ca is None or cb is None:
        return None, False
    gap = abs(float(ca) - float(cb))
    ok = gap >= SEP_PP and min(int(a.get("event_n") or 0), int(b.get("event_n") or 0)) >= MIN_EVENT_N
    return gap, ok


def run_comparisons(hits: list[dict[str, Any]]) -> dict[str, Any]:
    h = _eval(hits)
    pdh = path_stats([r for r in h if r.get("event_kind") == "PDH_BREAK_ABOVE"])
    mt_break = path_stats([r for r in h if r.get("event_kind") == "BREAK_ABOVE_ZONE" and not r.get("control_single_touch") and r.get("role") == "RESISTANCE"])
    single_break = path_stats([r for r in h if r.get("event_kind") == "BREAK_ABOVE_ZONE" and r.get("control_single_touch")])
    mt_accept = path_stats([r for r in h if r.get("event_kind") == "ACCEPT2_ABOVE" and not r.get("control_single_touch")])
    mt_retest = path_stats([r for r in h if r.get("event_kind") == "RETEST_HOLD" and not r.get("control_single_touch")])
    t2 = path_stats([r for r in h if r.get("event_kind") == "BREAK_ABOVE_ZONE" and r.get("touch_bucket") == "2"])
    t3 = path_stats([r for r in h if r.get("event_kind") == "BREAK_ABOVE_ZONE" and r.get("touch_bucket") == "3"])
    t4 = path_stats([r for r in h if r.get("event_kind") == "BREAK_ABOVE_ZONE" and r.get("touch_bucket") == "4+"])
    flip = path_stats([r for r in h if r.get("event_kind") == "RESISTANCE_TO_SUPPORT_FLIP"])
    hi_vol = path_stats([r for r in h if r.get("event_kind") == "BREAK_ABOVE_ZONE" and not r.get("control_single_touch") and _finite(r.get("vol_rel20")) and float(r["vol_rel20"]) >= 1.5])
    lo_vol = path_stats([r for r in h if r.get("event_kind") == "BREAK_ABOVE_ZONE" and not r.get("control_single_touch") and _finite(r.get("vol_rel20")) and float(r["vol_rel20"]) < 1.5])
    pairs = []
    for name, a, b in [
        ("MULTI_TOUCH_BREAK_VS_PDH", mt_break, pdh),
        ("MULTI_TOUCH_VS_SINGLE_TOUCH_BREAK", mt_break, single_break),
        ("BREAK_VS_ACCEPT", mt_break, mt_accept),
        ("BREAK_VS_RETEST_HOLD", mt_break, mt_retest),
        ("PDH_VS_RETEST_HOLD", pdh, mt_retest),
        ("TOUCH2_VS_TOUCH3_BREAK", t2, t3),
        ("TOUCH3_VS_TOUCH4_BREAK", t3, t4),
        ("HIVOL_VS_LOVOL_BREAK", hi_vol, lo_vol),
    ]:
        gap, improved = _sep(a, b)
        pairs.append({"id": name, "a": a, "b": b, "continuation_rate_gap": gap, "path_separation_improved": improved})
    monotonic = None
    rates = [x.get("continuation_rate") for x in (t2, t3, t4) if x.get("continuation_rate") is not None]
    if len(rates) >= 3:
        monotonic = bool(rates[0] < rates[1] < rates[2] or rates[0] > rates[1] > rates[2])
    return {
        "pairs": pairs,
        "pdh": pdh,
        "mt_break": mt_break,
        "mt_accept": mt_accept,
        "mt_retest": mt_retest,
        "single_break": single_break,
        "touch2": t2,
        "touch3": t3,
        "touch4": t4,
        "flip": flip,
        "hi_vol": hi_vol,
        "lo_vol": lo_vol,
        "touch_count_monotonic": monotonic,
        "any_path_separation": any(p["path_separation_improved"] for p in pairs),
    }


def participation_split(hits: list[dict[str, Any]]) -> dict[str, Any]:
    h = [
        r
        for r in _eval(hits)
        if r.get("event_kind") == "BREAK_ABOVE_ZONE" and not r.get("control_single_touch")
    ]
    return {
        "n": len(h),
        "mean_vol_rel": path_stats(h).get("mean_vol_rel"),
        "mean_va_rel": path_stats(h).get("mean_va_rel"),
        "mean_bars_inside": path_stats(h).get("mean_bars_inside"),
        "hi_vol": path_stats([r for r in h if _finite(r.get("vol_rel20")) and float(r["vol_rel20"]) >= 1.5]),
        "lo_vol": path_stats([r for r in h if _finite(r.get("vol_rel20")) and float(r["vol_rel20"]) < 1.5]),
    }
