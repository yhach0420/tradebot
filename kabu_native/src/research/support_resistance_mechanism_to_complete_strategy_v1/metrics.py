"""Complete-strategy economics. Descriptive gates. No threshold optimization."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.support_resistance_mechanism_to_complete_strategy_v1 import (
    CATASTROPHIC_MEDIAN_YEN,
    COHERENCE_BLOCKS,
    SHARES,
    X1_STRESS_BPS,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _pf(pnls: list[float]) -> float | None:
    gp = sum(p for p in pnls if p > 0)
    gl = sum(p for p in pnls if p < 0)
    if gl < 0:
        return float(gp / abs(gl))
    if pnls and gp > 0:
        return 999.0
    return None


def _maxdd(daily: list[tuple[str, float]]) -> dict[str, Any]:
    peak = 0.0
    eq = 0.0
    mdd = 0.0
    trough_day = None
    peak_day = None
    cur_peak_day = None
    for day, pnl in daily:
        eq += float(pnl)
        if eq > peak:
            peak = eq
            cur_peak_day = day
        dd = peak - eq
        if dd > mdd:
            mdd = dd
            trough_day = day
            peak_day = cur_peak_day
    return {"maxDD_yen": float(mdd), "maxDD_peak_day": peak_day, "maxDD_trough_day": trough_day}


def _session_of(t: str) -> str:
    return "AM" if str(t) < "12:00" else "PM"


def summarize(trades: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    rows = [t for t in trades if t.get("ok")]
    x0 = [float(t["gross_yen"]) for t in rows]
    x1 = [float(t["stress_yen"]) for t in rows]
    by_day: dict[str, float] = defaultdict(float)
    by_day_x1: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for t in rows:
        by_day[str(t["date"])] += float(t["gross_yen"])
        by_day_x1[str(t["date"])] += float(t["stress_yen"])
        by_sym[str(t["symbol"])] += float(t["gross_yen"])
    daily = sorted(by_day.items())
    dd = _maxdd(daily)
    pos_days = [v for _, v in daily if v > 0]
    notionals = [float(t["entry_price"]) * float(SHARES) for t in rows]
    be = (sum(x0) / sum(notionals) * 10_000.0) if notionals and sum(notionals) > 0 else None
    holds = [int(t.get("hold_min") or 0) for t in rows]
    reasons = defaultdict(int)
    attrib = defaultdict(int)
    for t in rows:
        reasons[str(t.get("exit_reason") or "")] += 1
        attrib[str(t.get("attribution") or "")] += 1

    def _slice(pred) -> dict[str, Any]:
        sub = [t for t in rows if pred(t)]
        sx0 = [float(t["gross_yen"]) for t in sub]
        sx1 = [float(t["stress_yen"]) for t in sub]
        return {
            "n": len(sub),
            "gross_yen": float(sum(sx0)),
            "stress_yen": float(sum(sx1)),
            "pf_gross": _pf(sx0),
            "pf_stress": _pf(sx1),
            "median_trade": float(median(sx0)) if sx0 else None,
        }

    blocks = {b: _slice(lambda t, bb=b: str(t.get("block") or "") == bb) for b in ("D1", "D2", "D3", "D4")}
    long = _slice(lambda t: str(t.get("side") or "") == "LONG")
    short = _slice(lambda t: str(t.get("side") or "") == "SHORT")
    sup = _slice(lambda t: str(t.get("zone_role") or "") == "SUPPORT")
    res = _slice(lambda t: str(t.get("zone_role") or "") == "RESISTANCE")
    am = _slice(lambda t: _session_of(str(t.get("entry_t") or "")) == "AM")
    pm = _slice(lambda t: _session_of(str(t.get("entry_t") or "")) == "PM")
    tgt_avail = _slice(lambda t: t.get("target_price") is not None)
    no_tgt = _slice(lambda t: t.get("target_price") is None)

    ranked = sorted(rows, key=lambda t: float(t["gross_yen"]), reverse=True)
    n = len(ranked)
    topk = {}
    for k in (1, 3, 5, 10):
        topk[f"top{k}_yen"] = float(sum(float(t["gross_yen"]) for t in ranked[:k])) if n else 0.0
        topk[f"top{k}_share"] = (topk[f"top{k}_yen"] / sum(x0)) if x0 and sum(x0) else None
    n1 = max(1, int(round(0.01 * n))) if n else 0
    n5 = max(1, int(round(0.05 * n))) if n else 0
    topk["top1pct_yen"] = float(sum(float(t["gross_yen"]) for t in ranked[:n1])) if n else 0.0
    topk["top5pct_yen"] = float(sum(float(t["gross_yen"]) for t in ranked[:n5])) if n else 0.0
    top_sym = max(by_sym.items(), key=lambda kv: kv[1]) if by_sym else (None, 0.0)
    top3_sym = sorted(by_sym.items(), key=lambda kv: kv[1], reverse=True)[:3]
    best_day = max(daily, key=lambda kv: kv[1]) if daily else (None, 0.0)
    worst_day = min(daily, key=lambda kv: kv[1]) if daily else (None, 0.0)
    top3_days = sorted(daily, key=lambda kv: kv[1], reverse=True)[:3]

    def _removed(drop: set[str] | None = None, drop_trades: list[dict[str, Any]] | None = None, drop_days: set[str] | None = None) -> dict[str, Any]:
        sub = rows
        if drop:
            sub = [t for t in sub if str(t.get("symbol")) not in drop]
        if drop_days:
            sub = [t for t in sub if str(t.get("date")) not in drop_days]
        if drop_trades:
            ids = {id(t) for t in drop_trades}
            sub = [t for t in sub if id(t) not in ids]
        sx0 = [float(t["gross_yen"]) for t in sub]
        sx1 = [float(t["stress_yen"]) for t in sub]
        return {
            "n": len(sub),
            "gross_yen": float(sum(sx0)),
            "stress_yen": float(sum(sx1)),
            "pf_gross": _pf(sx0),
            "pf_stress": _pf(sx1),
            "median_trade": float(median(sx0)) if sx0 else None,
        }

    conc = {
        **topk,
        "top_symbol": top_sym[0],
        "top_symbol_yen": float(top_sym[1]),
        "top3_symbols": [{"symbol": s, "yen": float(v)} for s, v in top3_sym],
        "best_day": best_day[0],
        "best_day_yen": float(best_day[1]),
        "worst_day": worst_day[0],
        "worst_day_yen": float(worst_day[1]),
        "top3_days": [{"date": d, "yen": float(v)} for d, v in top3_days],
        "top1_trade_removed": _removed(drop_trades=ranked[:1]),
        "top5_trades_removed": _removed(drop_trades=ranked[:5]),
        "top5pct_winners_removed": _removed(drop_trades=ranked[:n5]),
        "best_day_removed": _removed(drop_days={str(best_day[0])} if best_day[0] else set()),
        "top_symbol_removed": _removed(drop={str(top_sym[0])} if top_sym[0] else set()),
    }

    mfe = [float(t["mfe_bps"]) for t in rows if _finite(t.get("mfe_bps"))]
    mae = [float(t["mae_bps"]) for t in rows if _finite(t.get("mae_bps"))]
    gb = [float(t["mfe_realized_giveback_bps"]) for t in rows if _finite(t.get("mfe_realized_giveback_bps"))]

    block_signs = []
    for b in COHERENCE_BLOCKS:
        v = blocks[b].get("stress_yen")
        if blocks[b]["n"] > 0 and v is not None:
            block_signs.append(float(v))
    collapse = False
    if len(block_signs) >= 2:
        collapse = not (all(v > 0 for v in block_signs) or all(v < 0 for v in block_signs))

    median_x0 = float(median(x0)) if x0 else None
    pf_s = _pf(x1)
    pf_g = _pf(x0)
    stress_sum = float(sum(x1))
    dependent = False
    if rows:
        t1 = conc["top1_trade_removed"]["stress_yen"]
        td = conc["best_day_removed"]["stress_yen"]
        ts = conc["top_symbol_removed"]["stress_yen"]
        if stress_sum > 0 and (t1 <= 0 or td <= 0 or ts <= 0):
            dependent = True

    coherent = bool(
        rows
        and stress_sum > 0
        and pf_s is not None
        and pf_s > 1
        and median_x0 is not None
        and median_x0 > float(CATASTROPHIC_MEDIAN_YEN)
        and not collapse
        and not dependent
    )

    days_n = len(daily)
    return {
        "label": label,
        "trades": len(rows),
        "gross_yen": float(sum(x0)),
        "stress_yen": stress_sum,
        "pf_gross": pf_g,
        "pf_stress": pf_s,
        "win_rate": (sum(1 for p in x0 if p > 0) / len(x0)) if x0 else None,
        "average_trade": (sum(x0) / len(x0)) if x0 else None,
        "median_trade": median_x0,
        "maxDD_yen": dd["maxDD_yen"],
        "maxDD": dd,
        "positive_day_rate": (len(pos_days) / days_n) if days_n else None,
        "median_daily_pnl": float(median([v for _, v in daily])) if daily else None,
        "best_day": best_day[0],
        "best_day_yen": float(best_day[1]),
        "worst_day": worst_day[0],
        "worst_day_yen": float(worst_day[1]),
        "break_even_cost_bps": be,
        "x1_stress_bps": float(X1_STRESS_BPS),
        "target_exit_n": int(reasons.get("structural_target") or 0),
        "invalidation_exit_n": int(reasons.get("invalidation") or 0),
        "session_close_n": int(reasons.get("session_close") or 0),
        "exit_reasons": dict(reasons),
        "attribution": dict(attrib),
        "average_hold": (sum(holds) / len(holds)) if holds else None,
        "median_hold": float(median(holds)) if holds else None,
        "AM": am,
        "PM": pm,
        "LONG": long,
        "SHORT": short,
        "SUPPORT": sup,
        "RESISTANCE": res,
        "target_available": tgt_avail,
        "no_target": no_tgt,
        "blocks": blocks,
        "concentration": conc,
        "mfe_mean": (sum(mfe) / len(mfe)) if mfe else None,
        "mae_mean": (sum(mae) / len(mae)) if mae else None,
        "giveback_mean": (sum(gb) / len(gb)) if gb else None,
        "block_sign_collapse": bool(collapse),
        "single_name_or_day_or_trade_dependent": bool(dependent),
        "coherent": bool(coherent),
        "daily": [{"date": d, "gross_yen": float(v), "stress_yen": float(by_day_x1[d])} for d, v in daily],
        "short_label": "HISTORICAL_SHORT_RESEARCH_ONLY",
    }


def tie_fragile(primary: dict[str, Any], alts: list[dict[str, Any]], *, rel: float) -> dict[str, Any]:
    base = float(primary.get("stress_yen") or 0.0)
    flag = False
    rows = []
    for a in alts:
        alt = float(a.get("stress_yen") or 0.0)
        sign_flip = (base > 0 and alt <= 0) or (base < 0 and alt >= 0)
        denom = max(1.0, abs(base))
        rel_chg = abs(alt - base) / denom
        frag = bool(sign_flip or rel_chg > float(rel))
        flag = flag or frag
        rows.append(
            {
                "order_name": a.get("order_name"),
                "stress_yen": alt,
                "pf_stress": a.get("pf_stress"),
                "trades": a.get("trades"),
                "sign_flip": sign_flip,
                "rel_change": rel_chg,
                "fragile_vs_primary": frag,
            }
        )
    return {"CAP_TIE_ORDER_FRAGILE": bool(flag), "comparisons": rows, "primary_stress_yen": base}
