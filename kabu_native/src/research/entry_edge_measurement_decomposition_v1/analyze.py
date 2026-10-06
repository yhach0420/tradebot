"""Pack MID / ASK_MID / ASK_BID / spread. No threshold search. No candidate PASS from lift."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.entry_edge_measurement_decomposition_v1 import HORIZONS_SEC
from research.entry_edge_measurement_decomposition_v1.harvest import AUDIT

CASE_A = "ENTRY_MEASUREMENT_SPREAD_DOMINATED"
CASE_B = "ENTRY_MEASUREMENT_SIGNAL_ALPHA_NEGATIVE"
CASE_C = "ENTRY_MEASUREMENT_MIXED"
CASE_E = "ENTRY_MEASUREMENT_INTEGRITY_FAILED"


def _num(v: Any) -> Optional[float]:
    if v is None or not isinstance(v, (int, float)):
        return None
    x = float(v)
    return x if x == x else None


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [_num(x) for x in xs]
    vs = [x for x in vs if x is not None]
    if not vs:
        return None
    return float(np.mean(vs))


def _median(xs: list[Any]) -> Optional[float]:
    vs = [_num(x) for x in xs]
    vs = [x for x in vs if x is not None]
    if not vs:
        return None
    return float(np.median(vs))


def _pct(xs: list[Any], q: float) -> Optional[float]:
    vs = [_num(x) for x in xs]
    vs = [x for x in vs if x is not None]
    if not vs:
        return None
    return float(np.percentile(vs, q))


def _vals(rows: list[dict[str, Any]], key: str) -> list[Any]:
    return [r.get(key) for r in rows]


def leakage_n() -> dict[str, int]:
    keys = (
        "LOCKED_HOLDOUT_READ_N",
        "STRESS_READ_N",
        "FUTURE_DATA_N",
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
        "SIGNAL_KEY_MISMATCH_N",
        "ASK_BID_PARITY_FAIL_N",
        "SPLIT_LEAKAGE_N",
        "RULE_CHANGE_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def integrity_ok(extra: dict[str, Any] | None = None) -> bool:
    leak = leakage_n()
    if extra:
        for k, v in extra.items():
            leak[k] = int(leak.get(k) or 0) + int(v or 0)
    return all(int(v) == 0 for v in leak.values())


def _day_signs(daily: list[dict[str, Any]], key: str) -> dict[str, int]:
    pos = neg = zero = 0
    for r in daily:
        v = _num(r.get(key))
        if v is None:
            continue
        if v > 1e-12:
            pos += 1
        elif v < -1e-12:
            neg += 1
        else:
            zero += 1
    return {"positive_day_n": pos, "negative_day_n": neg, "zero_day_n": zero}


def _ex_best(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    byd: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = _num(r.get(key))
        if v is None:
            continue
        byd[str(r.get("date") or "")].append(v)
    if not byd:
        return None
    day_mean = {d: float(np.mean(vs)) for d, vs in byd.items()}
    best = max(day_mean.keys(), key=lambda d: day_mean[d])
    rest = [v for d, vs in byd.items() if d != best for v in vs]
    return _mean(rest)


def _drop_top(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[str(r.get("symbol") or "")] += 1
    if not counts:
        return None
    top = max(counts.keys(), key=lambda s: (counts[s], s))
    rest = [r.get(key) for r in rows if str(r.get("symbol") or "") != top]
    return _mean(rest)


def pack_pop(rows: list[dict[str, Any]], *, days: list[str], label: str, signal_n: int | None = None) -> dict[str, Any]:
    all_rows = list(rows)
    exe = [r for r in all_rows if r.get("eligible")]
    daily = []
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in exe:
        by[str(r.get("date") or "")].append(r)
    for d in days:
        xs = by.get(d) or []
        rec: dict[str, Any] = {"date": d, "N": len(xs)}
        for h in HORIZONS_SEC:
            rec[f"MEAN_MID_{h}"] = _mean(_vals(xs, f"MID_{h}"))
            rec[f"MEAN_ASK_BID_{h}"] = _mean(_vals(xs, f"ASK_BID_{h}"))
        daily.append(rec)
    sp = _vals(exe, "spread0")
    out: dict[str, Any] = {
        "label": label,
        "SIGNAL_N": int(signal_n) if signal_n is not None else len(all_rows),
        "N": len(exe),
        "daily": daily,
        "SPREAD0_MEAN": _mean(sp),
        "SPREAD0_MEDIAN": _median(sp),
        "SPREAD0_P25": _pct(sp, 25),
        "SPREAD0_P50": _pct(sp, 50),
        "SPREAD0_P75": _pct(sp, 75),
        "SPREAD0_P90": _pct(sp, 90),
        "ENTRY_HALF_SPREAD_MEAN": _mean(_vals(exe, "entry_hs")),
    }
    for h in HORIZONS_SEC:
        out[f"MEAN_MID_{h}"] = _mean(_vals(exe, f"MID_{h}"))
        out[f"MEDIAN_MID_{h}"] = _median(_vals(exe, f"MID_{h}"))
        out[f"MEAN_ASK_MID_{h}"] = _mean(_vals(exe, f"ASK_MID_{h}"))
        out[f"MEDIAN_ASK_MID_{h}"] = _median(_vals(exe, f"ASK_MID_{h}"))
        out[f"MEAN_ASK_BID_{h}"] = _mean(_vals(exe, f"ASK_BID_{h}"))
        out[f"MEDIAN_ASK_BID_{h}"] = _median(_vals(exe, f"ASK_BID_{h}"))
        out[f"MEAN_EXIT_HS_{h}"] = _mean(_vals(exe, f"EXIT_HS_{h}"))
        out[f"MEAN_RESIDUAL_{h}"] = _mean(_vals(exe, f"RESIDUAL_{h}"))
        out[f"MEDIAN_RESIDUAL_{h}"] = _median(_vals(exe, f"RESIDUAL_{h}"))
        out[f"MID_DAY_SIGNS_{h}"] = _day_signs(daily, f"MEAN_MID_{h}")
        out[f"ASK_BID_DAY_SIGNS_{h}"] = _day_signs(daily, f"MEAN_ASK_BID_{h}")
        out[f"EX_BEST_MID_{h}"] = _ex_best(exe, f"MID_{h}")
        out[f"DROP_TOP_MID_{h}"] = _drop_top(exe, f"MID_{h}")
        out[f"EX_BEST_ASK_BID_{h}"] = _ex_best(exe, f"ASK_BID_{h}")
        out[f"DROP_TOP_ASK_BID_{h}"] = _drop_top(exe, f"ASK_BID_{h}")
    return out


def lift(family: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HORIZONS_SEC:
        fm = family.get(f"MEAN_MID_{h}")
        bm = base.get(f"MEAN_MID_{h}")
        fa = family.get(f"MEAN_ASK_BID_{h}")
        ba = base.get(f"MEAN_ASK_BID_{h}")
        out[f"MID_ALPHA_LIFT_{h}"] = (float(fm) - float(bm)) if fm is not None and bm is not None else None
        out[f"ASK_BID_LIFT_{h}"] = (float(fa) - float(ba)) if fa is not None and ba is not None else None
    return out


def spread_quartiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exe = [r for r in rows if r.get("eligible") and _num(r.get("spread0")) is not None]
    if len(exe) < 4:
        return [{"empty": True, "reason": "N_LT_4"}]
    spreads = np.asarray([float(r["spread0"]) for r in exe], dtype=float)
    edges = np.percentile(spreads, [0, 25, 50, 75, 100])
    out = []
    for qi, (lo, hi, name) in enumerate(
        ((edges[0], edges[1], "Q1"), (edges[1], edges[2], "Q2"), (edges[2], edges[3], "Q3"), (edges[3], edges[4], "Q4")),
        start=1,
    ):
        if qi == 1:
            xs = [r for r in exe if float(r["spread0"]) <= float(hi) + 1e-18]
        elif qi == 4:
            xs = [r for r in exe if float(r["spread0"]) > float(lo) - 1e-18]
        else:
            xs = [r for r in exe if float(lo) < float(r["spread0"]) <= float(hi) + 1e-18]
        out.append(
            {
                "quartile": name,
                "lo": float(lo),
                "hi": float(hi),
                "N": len(xs),
                "MEAN_MID_180": _mean(_vals(xs, "MID_180")),
                "MEAN_MID_300": _mean(_vals(xs, "MID_300")),
                "MEAN_ASK_BID_180": _mean(_vals(xs, "ASK_BID_180")),
                "MEAN_ASK_BID_300": _mean(_vals(xs, "ASK_BID_300")),
                "SPREAD0_MEAN": _mean(_vals(xs, "spread0")),
            }
        )
    return out


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 0.0


def _le0(v: Any) -> bool:
    return v is not None and float(v) <= 0.0


def _days_neg(p: dict[str, Any], key: str) -> bool:
    d = dict(p.get(key) or {})
    return int(d.get("positive_day_n") or 0) <= int(d.get("negative_day_n") or 0)


def _lift_clear(lift_d: dict[str, Any]) -> bool:
    return _gt0(lift_d.get("MID_ALPHA_LIFT_180")) and _gt0(lift_d.get("MID_ALPHA_LIFT_300"))


def _raw_mid_pos(p: dict[str, Any]) -> bool:
    return _gt0(p.get("MEAN_MID_180")) and _gt0(p.get("MEAN_MID_300"))


def _raw_mid_nonpos(p: dict[str, Any]) -> bool:
    return _le0(p.get("MEAN_MID_180")) and _le0(p.get("MEAN_MID_300"))


def _ask_bid_neg(p: dict[str, Any]) -> bool:
    return _le0(p.get("MEAN_ASK_BID_180")) and _le0(p.get("MEAN_ASK_BID_300"))


def _spread_explains(p: dict[str, Any]) -> bool:
    if not _ask_bid_neg(p):
        return False
    rt = None
    e = p.get("ENTRY_HALF_SPREAD_MEAN")
    x180 = p.get("MEAN_EXIT_HS_180")
    x300 = p.get("MEAN_EXIT_HS_300")
    if e is not None and x180 is not None and x300 is not None:
        rt = float(e) + 0.5 * (float(x180) + float(x300))
    ab = p.get("MEAN_ASK_BID_180")
    midv = p.get("MEAN_MID_180")
    if ab is None or rt is None or midv is None:
        return False
    approx = float(midv) - float(rt)
    return abs(float(ab) - approx) <= max(5.0, 0.35 * abs(rt))


def decide(
    *,
    integrity: bool,
    breakout: dict[str, Any],
    vwap: dict[str, Any],
    lift_b: dict[str, Any],
    lift_v: dict[str, Any],
) -> dict[str, Any]:
    if not integrity:
        return {
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": "STOP. DO_NOT_INTERPRET_ECONOMICS.",
            "NEXT_EVALUATION_PROTOCOL": None,
            "NEXT_FAMILY": None,
            "REVIVE_EXISTING_FAMILIES": False,
        }
    fams = [
        ("BREAKOUT", breakout, lift_b),
        ("VWAP", vwap, lift_v),
    ]
    spread_dom = False
    for _name, p, lf in fams:
        pred = _raw_mid_pos(p) or _lift_clear(lf)
        if pred and _ask_bid_neg(p) and _spread_explains(p):
            spread_dom = True
            break
    both_no_alpha = True
    for _name, p, lf in fams:
        if not _raw_mid_nonpos(p):
            both_no_alpha = False
            break
        if not (_days_neg(p, "MID_DAY_SIGNS_180") and _days_neg(p, "MID_DAY_SIGNS_300")):
            both_no_alpha = False
            break
        if _lift_clear(lf):
            both_no_alpha = False
            break
    if spread_dom:
        return {
            "CASE": "A",
            "VERDICT": CASE_A,
            "NEXT": "Split discovery: Phase 1 MID predictive edge, Phase 2 real executable execution. Do not revive BREAKOUT or VWAP_RECLAIM as candidates.",
            "NEXT_EVALUATION_PROTOCOL": "PHASE1_MID_PREDICTIVE_THEN_PHASE2_EXECUTABLE",
            "NEXT_FAMILY": "FAILED_BREAKDOWN_RECLAIM",
            "REVIVE_EXISTING_FAMILIES": False,
        }
    if both_no_alpha:
        return {
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": "NEW ENTRY FAMILY #3 FAILED_BREAKDOWN_RECLAIM. Store MID alpha and ASK/BID economics separately. Not a retune of Breakout or VWAP reclaim.",
            "NEXT_EVALUATION_PROTOCOL": "STORE_MID_AND_ASK_BID_SEPARATELY",
            "NEXT_FAMILY": "FAILED_BREAKDOWN_RECLAIM",
            "REVIVE_EXISTING_FAMILIES": False,
        }
    return {
        "CASE": "C",
        "VERDICT": CASE_C,
        "NEXT": "Fix evaluation protocol to predictive alpha vs execution economics, then NEW ENTRY FAMILY #3 FAILED_BREAKDOWN_RECLAIM.",
        "NEXT_EVALUATION_PROTOCOL": "SPLIT_PREDICTIVE_VS_EXECUTION_THEN_FAMILY3",
        "NEXT_FAMILY": "FAILED_BREAKDOWN_RECLAIM",
        "REVIVE_EXISTING_FAMILIES": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    b = dict(report.get("breakout") or {})
    v = dict(report.get("vwap") or {})
    u = dict(report.get("unconditional") or {})
    lb = dict(report.get("lift_breakout") or {})
    lv = dict(report.get("lift_vwap") or {})
    d = dict(report.get("decision") or {})

    def triple(p: dict[str, Any], prefix: str) -> dict[str, Any]:
        return {h: p.get(f"{prefix}_{h}") for h in (60, 180, 300)}

    return {
        "1_why_measurement_audit_was_required": "Two different ENTRY families both printed about -20bps Ask1→Bid on DEVELOPMENT. Separate price/signal alpha (MID) from spread/immediate-cross cost before Family #3.",
        "2_Breakout_reused_signal_n": b.get("SIGNAL_N"),
        "3_VWAP_reused_signal_n": v.get("SIGNAL_N"),
        "4_unconditional_n": u.get("N"),
        "5_Breakout_spread_mean_median_p75": {
            "mean": b.get("SPREAD0_MEAN"),
            "median": b.get("SPREAD0_MEDIAN"),
            "p75": b.get("SPREAD0_P75"),
        },
        "6_Breakout_MID60_180_300": triple(b, "MEAN_MID"),
        "7_Breakout_ASK_MID60_180_300": triple(b, "MEAN_ASK_MID"),
        "8_Breakout_ASK_BID60_180_300": triple(b, "MEAN_ASK_BID"),
        "9_Breakout_MID_day_signs": {h: b.get(f"MID_DAY_SIGNS_{h}") for h in (180, 300)},
        "10_Breakout_MID_ex_best": {h: b.get(f"EX_BEST_MID_{h}") for h in (180, 300)},
        "11_Breakout_MID_drop_top_symbol": {h: b.get(f"DROP_TOP_MID_{h}") for h in (180, 300)},
        "12_Breakout_lift_vs_baseline": lb,
        "13_VWAP_spread_mean_median_p75": {
            "mean": v.get("SPREAD0_MEAN"),
            "median": v.get("SPREAD0_MEDIAN"),
            "p75": v.get("SPREAD0_P75"),
        },
        "14_VWAP_MID60_180_300": triple(v, "MEAN_MID"),
        "15_VWAP_ASK_MID60_180_300": triple(v, "MEAN_ASK_MID"),
        "16_VWAP_ASK_BID60_180_300": triple(v, "MEAN_ASK_BID"),
        "17_VWAP_MID_day_signs": {h: v.get(f"MID_DAY_SIGNS_{h}") for h in (180, 300)},
        "18_VWAP_MID_ex_best": {h: v.get(f"EX_BEST_MID_{h}") for h in (180, 300)},
        "19_VWAP_MID_drop_top_symbol": {h: v.get(f"DROP_TOP_MID_{h}") for h in (180, 300)},
        "20_VWAP_lift_vs_baseline": lv,
        "21_unconditional_MID60_180_300": triple(u, "MEAN_MID"),
        "22_unconditional_ASK_BID60_180_300": triple(u, "MEAN_ASK_BID"),
        "23_unconditional_spread": {
            "mean": u.get("SPREAD0_MEAN"),
            "median": u.get("SPREAD0_MEDIAN"),
            "p75": u.get("SPREAD0_P75"),
        },
        "24_decomposition_residual": {
            "BREAKOUT": {h: b.get(f"MEAN_RESIDUAL_{h}") for h in (60, 180, 300)},
            "VWAP": {h: v.get(f"MEAN_RESIDUAL_{h}") for h in (60, 180, 300)},
            "UNCONDITIONAL": {h: u.get(f"MEAN_RESIDUAL_{h}") for h in (60, 180, 300)},
        },
        "25_spread_quartile_results": report.get("quartiles") or {},
        "26_was_minus_20bps_mostly_spread": report.get("interpretation", {}).get("mostly_spread"),
        "27_did_either_family_have_raw_directional_alpha": report.get("interpretation", {}).get("raw_directional_alpha"),
        "28_immediate_cross_economics_positive": report.get("interpretation", {}).get("ask_bid_positive"),
        "29_predictive_edge_positive": report.get("interpretation", {}).get("predictive_edge_positive"),
        "30_verdict": d.get("VERDICT"),
        "31_next_evaluation_protocol": d.get("NEXT_EVALUATION_PROTOCOL"),
        "32_next_family": d.get("NEXT_FAMILY"),
        "33_Holdout_read": bool(int((report.get("leakage") or {}).get("LOCKED_HOLDOUT_READ_N") or 0)),
        "34_Stress_read": bool(int((report.get("leakage") or {}).get("STRESS_READ_N") or 0)),
        "35_future_used": bool(int((report.get("leakage") or {}).get("FUTURE_DATA_N") or 0)),
        "36_Runtime_changed": False,
        "37_submit_cancel_live": "0/0/0",
    }


def interpretation(breakout: dict[str, Any], vwap: dict[str, Any], lift_b: dict[str, Any], lift_v: dict[str, Any]) -> dict[str, Any]:
    mid_pos = _raw_mid_pos(breakout) or _raw_mid_pos(vwap)
    lift_pos = _lift_clear(lift_b) or _lift_clear(lift_v)
    ask_pos = _gt0(breakout.get("MEAN_ASK_BID_180")) or _gt0(breakout.get("MEAN_ASK_BID_300")) or _gt0(vwap.get("MEAN_ASK_BID_180")) or _gt0(vwap.get("MEAN_ASK_BID_300"))
    sp_b = breakout.get("SPREAD0_MEAN")
    ab_b = breakout.get("MEAN_ASK_BID_180")
    mostly = False
    if sp_b is not None and ab_b is not None and float(ab_b) < 0:
        # round-trip ~ spread0 if exit half ≈ entry half
        mostly = abs(abs(float(ab_b)) - float(sp_b)) <= max(8.0, 0.5 * abs(float(sp_b))) or _spread_explains(breakout) or _spread_explains(vwap)
    return {
        "mostly_spread": bool(mostly),
        "raw_directional_alpha": bool(mid_pos),
        "ask_bid_positive": bool(ask_pos),
        "predictive_edge_positive": bool(mid_pos or lift_pos),
        "note": "MID is predictive alpha only. Not tradable PnL. Lift is not a candidate PASS.",
    }
