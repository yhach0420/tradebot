"""V21 notional vs bps attribution. 1M normalize is diagnostic only. No quartile gate. No sizing search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from replay.pnl_yen import summarize_pnl_yen_100
from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.anchor_timing_robustness.metrics import maxdd
from research.simple_tech_entry_family.v3_analyze import concentration, day_rows, day_sign_counts
from research.simple_tech_entry_family.v4_analyze import _mean, _median, spearman
from research.simple_tech_entry_family.v6_pullback_analyze import drop_symbol_mean, symbol_pack
from research.simple_tech_entry_family.v9_analyze import drop_top3_mean
from research.simple_tech_exit_family.v14_analyze import _finite, _gt0
from research.simple_tech_strategy.v20_analyze import concentration_yen, pnl_pack
from research.simple_tech_strategy.v21_spec import (
    CV_NOTIONAL_MIN,
    E4_FILLED_N_EXPECTED,
    HYPOTHETICAL_NOTIONAL_YEN,
    MAX_MEDIAN_RATIO_MIN,
    PARITY_ABS_TOL,
    PARITY_PF_TOL,
    PARITY_YEN_TOL,
    SPEARMAN_ABS_PNL_MIN,
    V18_MEAN_BPS_EXPECTED,
    V18_MEDIAN_BPS_EXPECTED,
    V20_DD_EXPECTED,
    V20_EX_BEST_DAY_EXPECTED,
    V20_EX_TOP3_DAY_EXPECTED,
    V20_PF_EXPECTED,
    V20_TOTAL_PNL_YEN_100_EXPECTED,
)


def _close(a: Any, b: Any, tol: float) -> bool:
    try:
        return abs(float(a) - float(b)) <= float(tol)
    except (TypeError, ValueError):
        return False


def pearson(xs: list[Any], ys: list[Any]) -> Optional[float]:
    pairs = [(float(a), float(b)) for a, b in zip(xs, ys) if _finite(a) and _finite(b)]
    if len(pairs) < 5:
        return None
    x = np.asarray([p[0] for p in pairs], dtype=float)
    y = np.asarray([p[1] for p in pairs], dtype=float)
    if float(np.std(x)) < 1e-12 or float(np.std(y)) < 1e-12:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def notional_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [float(r["entry_notional_yen"]) for r in rows if _finite(r.get("entry_notional_yen"))]
    if not xs:
        return {"N": 0}
    arr = np.asarray(xs, dtype=float)
    total = float(np.sum(arr))
    ordered = sorted(xs, reverse=True)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if len(xs) > 1 else 0.0
    med = float(np.median(arr))
    mx = float(np.max(arr))
    return {
        "N": len(xs),
        "min": float(np.min(arr)),
        "p25": float(np.percentile(arr, 25)),
        "median": med,
        "p75": float(np.percentile(arr, 75)),
        "max": mx,
        "mean": mean,
        "CV": (std / mean) if abs(mean) > 1e-12 else None,
        "max_median_ratio": (mx / med) if abs(med) > 1e-12 else None,
        "TOP1_NOTIONAL_SHARE": (ordered[0] / total) if total > 1e-12 and ordered else None,
        "TOP3_NOTIONAL_SHARE": (sum(ordered[:3]) / total) if total > 1e-12 else None,
        "TOP5_NOTIONAL_SHARE": (sum(ordered[:5]) / total) if total > 1e-12 else None,
        "SUM": total,
    }


def corr_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = [r.get("entry_notional_yen") for r in rows]
    yen = [r.get("pnl_yen_100") for r in rows]
    ab = [r.get("absolute_pnl_yen") for r in rows]
    bps = [r.get("pnl_bps") for r in rows]
    return {
        "CORR_NOTIONAL_PNL_YEN": {"pearson": pearson(n, yen), "spearman": spearman(n, yen)},
        "CORR_NOTIONAL_ABS_PNL": {"pearson": pearson(n, ab), "spearman": spearman(n, ab)},
        "CORR_NOTIONAL_BPS": {"pearson": pearson(n, bps), "spearman": spearman(n, bps)},
        "N": len(rows),
    }


def notional_quartiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable = [r for r in rows if _finite(r.get("entry_notional_yen"))]
    if len(usable) < 4:
        return []
    vals = np.asarray([float(r["entry_notional_yen"]) for r in usable], dtype=float)
    edges = np.unique(np.percentile(vals, [0, 25, 50, 75, 100]))
    if int(edges.size) < 3:
        return []
    bins = np.digitize(vals, edges[1:-1], right=True) + 1
    grouped: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
    for r, b in zip(usable, bins):
        grouped[int(min(max(int(b), 1), 4))].append(r)
    out = []
    for q in (1, 2, 3, 4):
        grp = grouped[q]
        yen = [float(r["pnl_yen_100"]) for r in grp if _finite(r.get("pnl_yen_100"))]
        gp = sum(v for v in yen if v > 0)
        gl = abs(sum(v for v in yen if v < 0))
        out.append(
            {
                "quartile": f"Q{q}",
                "trade_n": len(grp),
                "notional_min": min((float(r["entry_notional_yen"]) for r in grp), default=None),
                "notional_max": max((float(r["entry_notional_yen"]) for r in grp), default=None),
                "mean_bps": _mean([r.get("pnl_bps") for r in grp]),
                "median_bps": _median([r.get("pnl_bps") for r in grp]),
                "total_pnl_yen_100": float(sum(yen)) if yen else 0.0,
                "mean_pnl_yen_100": _mean(yen),
                "gross_profit": float(gp),
                "gross_loss": float(gl),
            }
        )
    return out


def bps_robustness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for r in rows:
        r["markout_180"] = r.get("pnl_bps")
    bps = [float(r["pnl_bps"]) for r in rows if _finite(r.get("pnl_bps"))]
    daily = day_rows(rows, list(ELIGIBLE_DAYS))
    signs = day_sign_counts(daily, "MARKOUT180_MEAN")
    conc = concentration(rows, "markout_180") if rows else {}
    top = symbol_pack(rows, "markout_180").get("TOP_SYMBOL") if rows else None
    drop = drop_symbol_mean(rows, str(top or ""), "markout_180") if rows else None
    drop3 = drop_top3_mean(rows, "markout_180") if rows else None
    mean_bps = _mean(bps)
    median_bps = _median(bps)
    pos = int(signs.get("POSITIVE_DAY_N") or 0)
    neg = int(signs.get("NEGATIVE_DAY_N") or 0)
    ok = bool(
        _gt0(mean_bps)
        and _gt0(median_bps)
        and pos > neg
        and _gt0(conc.get("EX_BEST_DAY_MARKOUT"))
        and _gt0(conc.get("EX_TOP3_DAY_MARKOUT"))
        and _gt0(drop)
    )
    return {
        "MEAN_BPS": mean_bps,
        "MEDIAN_BPS": median_bps,
        "POSITIVE_DAY_N": pos,
        "NEGATIVE_DAY_N": neg,
        "EX_BEST_DAY": conc.get("EX_BEST_DAY_MARKOUT"),
        "EX_TOP3_DAY": conc.get("EX_TOP3_DAY_MARKOUT"),
        "DROP_TOP_SYMBOL": drop,
        "DROP_TOP3_SYMBOL": drop3,
        "TOP_SYMBOL": top,
        "BPS_ROBUSTNESS_POSITIVE": ok,
        "MEAN_BPS_LOCK": _close(mean_bps, V18_MEAN_BPS_EXPECTED, PARITY_ABS_TOL),
        "MEDIAN_BPS_LOCK": _close(median_bps, V18_MEDIAN_BPS_EXPECTED, PARITY_ABS_TOL),
    }


def day_attribution(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r.get("date") or "")].append(r)
    out = []
    for d in days:
        xs = by.get(d) or []
        notionals = [float(r["entry_notional_yen"]) for r in xs if _finite(r.get("entry_notional_yen"))]
        yen = [float(r["pnl_yen_100"]) for r in xs if _finite(r.get("pnl_yen_100"))]
        bps = [float(r["pnl_bps"]) for r in xs if _finite(r.get("pnl_bps"))]
        out.append(
            {
                "date": d,
                "trade_n": len(xs),
                "gross_entry_notional": float(sum(notionals)) if notionals else 0.0,
                "mean_trade_notional": _mean(notionals),
                "net_bps_equal_trade_weight": _mean(bps),
                "net_pnl_yen_100": float(sum(yen)) if yen else 0.0,
            }
        )
    return out


def _day_decomp(day_row: dict[str, Any], overall_median_notional: Optional[float], overall_mean_bps: Optional[float]) -> dict[str, Any]:
    mean_n = day_row.get("mean_trade_notional")
    mean_bps = day_row.get("net_bps_equal_trade_weight")
    yen = day_row.get("net_pnl_yen_100")
    high_notional = bool(_finite(mean_n) and _finite(overall_median_notional) and float(mean_n) > float(overall_median_notional))
    high_bps = bool(_finite(mean_bps) and _finite(overall_mean_bps) and float(mean_bps) > float(overall_mean_bps))
    if high_bps and (not high_notional):
        driver = "HIGH_BPS"
    elif high_notional and (not high_bps):
        driver = "HIGH_NOTIONAL"
    elif high_bps and high_notional:
        driver = "BOTH"
    else:
        driver = "NEITHER_ABOVE_CENTER"
    return {
        **day_row,
        "high_notional_vs_median": high_notional,
        "high_bps_vs_mean": high_bps,
        "driver": driver,
        "net_pnl_yen_100": yen,
    }


def symbol_attribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r.get("symbol") or "").replace(".T", "")].append(r)
    out = []
    for s, xs in by.items():
        bps = [float(r["pnl_bps"]) for r in xs if _finite(r.get("pnl_bps"))]
        yen = [float(r["pnl_yen_100"]) for r in xs if _finite(r.get("pnl_yen_100"))]
        notionals = [float(r["entry_notional_yen"]) for r in xs if _finite(r.get("entry_notional_yen"))]
        out.append(
            {
                "symbol": s,
                "trade_n": len(xs),
                "mean_entry_notional": _mean(notionals),
                "mean_bps": _mean(bps),
                "net_bps": float(sum(bps)) if bps else None,
                "net_pnl_yen_100": float(sum(yen)) if yen else 0.0,
            }
        )
    out.sort(key=lambda r: (float(r["net_pnl_yen_100"]), str(r["symbol"])), reverse=True)
    return out


def _sym_decomp(sym_row: dict[str, Any], median_notional: Optional[float], mean_bps: Optional[float]) -> dict[str, Any]:
    high_px = bool(
        _finite(sym_row.get("mean_entry_notional"))
        and _finite(median_notional)
        and float(sym_row["mean_entry_notional"]) > float(median_notional)
    )
    high_edge = bool(_finite(sym_row.get("mean_bps")) and _finite(mean_bps) and float(sym_row["mean_bps"]) > float(mean_bps))
    if high_edge and (not high_px):
        driver = "HIGH_DIRECTIONAL_EDGE"
    elif high_px and (not high_edge):
        driver = "HIGH_STOCK_PRICE"
    elif high_edge and high_px:
        driver = "BOTH"
    else:
        driver = "NEITHER_ABOVE_CENTER"
    return {**sym_row, "high_notional_vs_median": high_px, "high_bps_vs_mean": high_edge, "driver": driver}


def as_normalized_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        rec["pnl_yen_100"] = r.get("NORMALIZED_PNL_1M")
        out.append(rec)
    return out


def normalized_pack(rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    norm_rows = as_normalized_rows(rows)
    pnl = pnl_pack(norm_rows, days)
    conc = concentration_yen(norm_rows, days)
    return {
        "TOTAL": pnl.get("TOTAL_PNL_YEN_100"),
        "PF": pnl.get("PROFIT_FACTOR"),
        "WIN_N": pnl.get("WIN_N"),
        "LOSS_N": pnl.get("LOSS_N"),
        "POSITIVE_DAY_N": conc.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N": conc.get("NEGATIVE_DAY_N"),
        "EX_BEST_DAY_TOTAL_PNL": conc.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_TOP3_DAY_TOTAL_PNL": conc.get("EX_TOP3_DAY_TOTAL_PNL"),
        "DROP_TOP_SYMBOL_TOTAL_PNL": conc.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "DROP_TOP3_SYMBOL_TOTAL_PNL": conc.get("DROP_TOP3_SYMBOL_TOTAL_PNL"),
        "MAX_DD": maxdd(norm_rows, time_key="actual_exit_quote_time", pnl_key="pnl_yen_100"),
        "BEST_DAY": conc.get("BEST_DAY"),
        "TOP_SYMBOL": conc.get("TOP_SYMBOL"),
        "IS_STRATEGY_RESULT": False,
        "UNIT_NOTIONAL_YEN": float(HYPOTHETICAL_NOTIONAL_YEN),
        "daily": conc.get("daily"),
        "symbols": conc.get("symbols"),
    }


def fixed100_lock(pnl: dict[str, Any], conc: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "TOTAL": _close(pnl.get("TOTAL_PNL_YEN_100"), V20_TOTAL_PNL_YEN_100_EXPECTED, PARITY_YEN_TOL),
        "PF": _close(pnl.get("PROFIT_FACTOR"), V20_PF_EXPECTED, PARITY_PF_TOL),
        "EX_BEST": _close(conc.get("EX_BEST_DAY_TOTAL_PNL"), V20_EX_BEST_DAY_EXPECTED, PARITY_YEN_TOL),
        "EX_TOP3": _close(conc.get("EX_TOP3_DAY_TOTAL_PNL"), V20_EX_TOP3_DAY_EXPECTED, PARITY_YEN_TOL),
        "DD": _close(pnl.get("REALIZED_CLOSE_EQUITY_MAX_DD_YEN"), V20_DD_EXPECTED, PARITY_YEN_TOL),
        "TRADE_N": int(pnl.get("TRADE_N") or 0) == int(E4_FILLED_N_EXPECTED),
    }
    return {"V20_FIXED100_LOCK": all(checks.values()), "checks": checks}


def notional_explains(dist: dict[str, Any], corrs: dict[str, Any]) -> dict[str, Any]:
    cv = dist.get("CV")
    ratio = dist.get("max_median_ratio")
    sp_abs = (corrs.get("CORR_NOTIONAL_ABS_PNL") or {}).get("spearman")
    sp_bps = (corrs.get("CORR_NOTIONAL_BPS") or {}).get("spearman")
    dispersion = bool((_finite(cv) and float(cv) >= float(CV_NOTIONAL_MIN)) or (_finite(ratio) and float(ratio) >= float(MAX_MEDIAN_RATIO_MIN)))
    abs_ok = bool(_finite(sp_abs) and abs(float(sp_abs)) >= float(SPEARMAN_ABS_PNL_MIN))
    stronger_than_bps = bool(_finite(sp_abs) and ((not _finite(sp_bps)) or abs(float(sp_abs)) > abs(float(sp_bps))))
    return {
        "NOTIONAL_DISPERSION_MATERIAL": dispersion,
        "NOTIONAL_ABS_PNL_COUPLING": abs_ok,
        "ABS_PNL_COUPLING_STRONGER_THAN_BPS": stronger_than_bps,
        "NOTIONAL_EXPLAINS_FIXED100": bool(dispersion and abs_ok and stronger_than_bps),
        "thresholds": {
            "CV_NOTIONAL_MIN": float(CV_NOTIONAL_MIN),
            "MAX_MEDIAN_RATIO_MIN": float(MAX_MEDIAN_RATIO_MIN),
            "SPEARMAN_ABS_PNL_MIN": float(SPEARMAN_ABS_PNL_MIN),
        },
    }


def norm_improves(norm: dict[str, Any]) -> dict[str, Any]:
    ex_best = norm.get("EX_BEST_DAY_TOTAL_PNL")
    ex_top3 = norm.get("EX_TOP3_DAY_TOTAL_PNL")
    drop = norm.get("DROP_TOP_SYMBOL_TOTAL_PNL")
    pos = int(norm.get("POSITIVE_DAY_N") or 0)
    neg = int(norm.get("NEGATIVE_DAY_N") or 0)
    total = norm.get("TOTAL")
    pf = norm.get("PF")
    failed_gates_ok = bool(_gt0(ex_best) and _gt0(ex_top3))
    full = bool(_gt0(total) and _finite(pf) and float(pf) > 1.0 and pos > neg and _gt0(ex_best) and _gt0(ex_top3) and _gt0(drop))
    return {
        "NORM_IMPROVES_FAILED_YEN_GATES": failed_gates_ok,
        "NORM_FULL_DEVELOPMENT_GATE": full,
        "EX_BEST_GT_0": _gt0(ex_best),
        "EX_TOP3_GT_0": _gt0(ex_top3),
        "DROP_TOP_SYMBOL_GT_0": _gt0(drop),
    }


def decision_case(
    *,
    integrity_ok: bool,
    bps: dict[str, Any],
    explain: dict[str, Any],
    improve: dict[str, Any],
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V21_INVALID",
            "PRIMARY_FRAGILITY_SOURCE": None,
            "NEXT": "STOP. Integrity/identity failed. Do not adopt sizing. Do not mutate V20.",
        }
    bps_ok = bool(bps.get("BPS_ROBUSTNESS_POSITIVE"))
    notional_ok = bool(explain.get("NOTIONAL_EXPLAINS_FIXED100"))
    norm_ok = bool(improve.get("NORM_IMPROVES_FAILED_YEN_GATES"))
    if bps_ok and norm_ok and notional_ok:
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V21_FIXED_SHARE_SIZING_DOMINANT_FRAGILITY",
            "PRIMARY_FRAGILITY_SOURCE": "FIXED_SHARE_NOTIONAL_WEIGHTING",
            "NEXT": "STOP. Position sizing family may be precommitted on a later run. 1M normalize is not a strategy. Do not retune ENTRY/EXIT. V20 official unchanged. TRUE_OOS=false.",
        }
    if (not bps_ok or not norm_ok) and (not notional_ok):
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V21_UNDERLYING_EDGE_FRAGILE",
            "PRIMARY_FRAGILITY_SOURCE": "UNDERLYING_STRATEGY_CONCENTRATION",
            "NEXT": "STOP. ENTRY/EXIT not retuned. Simple Tech frozen strategy line is a close candidate. 1M is not a strategy. V20 official unchanged. TRUE_OOS=false.",
        }
    return {
        "CASE": "C",
        "VERDICT": "SIMPLE_TECH_V21_MIXED_PORTFOLIO_FRAGILITY",
        "PRIMARY_FRAGILITY_SOURCE": "MIXED_SIGNAL_AND_SIZING_CONCENTRATION",
        "NEXT": "STOP. Mixed fragility. Do not adopt sizing. Do not retune ENTRY/EXIT. V20 official unchanged. TRUE_OOS=false. FORWARD_OOS_ELIGIBLE=false.",
    }
