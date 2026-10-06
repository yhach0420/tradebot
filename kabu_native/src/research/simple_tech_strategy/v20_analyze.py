"""V20 frozen-stack portfolio construction, frequency, yen economics. No strategy retune. No new profit target."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from replay.pnl_yen import summarize_pnl_yen_100
from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.anchor_timing_robustness.metrics import maxdd
from research.simple_tech_entry_family.v4_analyze import _mean, _median
from research.simple_tech_entry_family.v13_analyze import _canon_num, set_hash
from research.simple_tech_exit_family.v14_analyze import _finite
from research.simple_tech_exit_family.v19_analyze import actual_exit_tuples, identity_hashes, trade_key
from research.simple_tech_strategy.v20_spec import (
    ACTUAL_EXIT_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    ELIGIBLE_DAY_N,
    PARITY_PF_TOL,
    PARITY_YEN_TOL,
    POSITION_CAP_IN_FROZEN_STACK,
    SAME_SYMBOL_SEMANTICS_IN_FROZEN_STACK,
    SHARES,
    V19_LOSS_N_EXPECTED,
    V19_PROFIT_FACTOR_YEN_100_EXPECTED,
    V19_REALIZED_MAX_DD_YEN_EXPECTED,
    V19_TOTAL_PNL_YEN_100_EXPECTED,
    V19_TRADE_N_EXPECTED,
    V19_WIN_N_EXPECTED,
)


def _close(a: Any, b: Any, tol: float) -> bool:
    try:
        return abs(float(a) - float(b)) <= float(tol)
    except (TypeError, ValueError):
        return False


def _int(v: Any, default: int = -1) -> int:
    if v is None:
        return int(default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _yen(v: Any) -> Optional[float]:
    return float(v) if _finite(v) else None


def occupancy_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[tuple[float, int, int, str]] = []
    for i, r in enumerate(rows):
        ft = r.get("fill_t")
        et = r.get("actual_exit_quote_time")
        if not _finite(ft) or not _finite(et):
            continue
        events.append((float(ft), 1, i, str(r.get("symbol") or "").replace(".T", "")))
        events.append((float(et), 0, i, str(r.get("symbol") or "").replace(".T", "")))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    open_idx: dict[int, dict[str, Any]] = {}
    open_sym: dict[str, int] = defaultdict(int)
    same_overlap_n = 0
    entry_while_n = 0
    max_n = 0
    peak_notional = 0.0
    timed: list[tuple[float, int, float]] = []
    last_t = None
    last_n = 0
    last_notional = 0.0
    active_occ_dt = 0.0
    active_occ_int = 0.0
    active_notional_int = 0.0

    def _notional() -> float:
        tot = 0.0
        for rec in open_idx.values():
            px = rec.get("fill_price")
            if _finite(px):
                tot += float(px) * float(SHARES)
        return tot

    for ts, kind, idx, sym in events:
        if last_t is not None and last_n > 0:
            dt = max(0.0, float(ts) - float(last_t))
            active_occ_dt += dt
            active_occ_int += dt * float(last_n)
            active_notional_int += dt * float(last_notional)
        if kind == 0:
            open_idx.pop(idx, None)
            if open_sym.get(sym, 0) > 0:
                open_sym[sym] -= 1
                if open_sym[sym] <= 0:
                    open_sym.pop(sym, None)
        else:
            if open_sym.get(sym, 0) > 0:
                entry_while_n += 1
                same_overlap_n += 1
            open_idx[idx] = rows[idx]
            open_sym[sym] = int(open_sym.get(sym, 0)) + 1
        n_open = len(open_idx)
        notion = _notional()
        max_n = max(max_n, n_open)
        peak_notional = max(peak_notional, notion)
        timed.append((float(ts), n_open, notion))
        last_t = float(ts)
        last_n = n_open
        last_notional = notion

    unresolved = bool(same_overlap_n > 0 and (not SAME_SYMBOL_SEMANTICS_IN_FROZEN_STACK))
    mean_conc = (active_occ_int / active_occ_dt) if active_occ_dt > 1e-12 else None
    mean_notional = (active_notional_int / active_occ_dt) if active_occ_dt > 1e-12 else None
    return {
        "MAX_CONCURRENT_POSITIONS": int(max_n),
        "MEAN_CONCURRENT_WHEN_ACTIVE": mean_conc,
        "SAME_SYMBOL_OVERLAP_N": int(same_overlap_n),
        "ENTRY_WHILE_SAME_SYMBOL_ALREADY_OPEN_N": int(entry_while_n),
        "PEAK_GROSS_NOTIONAL_YEN": float(peak_notional),
        "MEAN_GROSS_NOTIONAL_YEN": mean_notional,
        "SAME_SYMBOL_SEMANTICS_IN_FROZEN_STACK": bool(SAME_SYMBOL_SEMANTICS_IN_FROZEN_STACK),
        "POSITION_CAP_IN_FROZEN_STACK": bool(POSITION_CAP_IN_FROZEN_STACK),
        "PORTFOLIO_CONSTRUCTION_UNRESOLVED": bool(unresolved),
        "EVENT_N": len(events),
    }


def frequency_pack(rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    by: dict[str, int] = {d: 0 for d in days}
    for r in rows:
        d = str(r.get("date") or "")
        if d in by:
            by[d] += 1
    counts = [int(by[d]) for d in days]
    active = [c for c in counts if c > 0]
    n = len(rows)
    n_days = len(days) if days else ELIGIBLE_DAY_N
    return {
        "TRADE_N": n,
        "ELIGIBLE_DAY_N": n_days,
        "TRADES_PER_ELIGIBLE_DAY": (float(n) / float(n_days)) if n_days else None,
        "ACTIVE_DAY_N": len(active),
        "NO_TRADE_DAY_N": sum(1 for c in counts if c == 0),
        "TRADES_PER_ACTIVE_DAY": (float(n) / float(len(active))) if active else None,
        "DAILY_TRADE_COUNT_MIN": (min(counts) if counts else None),
        "DAILY_TRADE_COUNT_MEDIAN": _median(counts),
        "DAILY_TRADE_COUNT_MEAN": _mean(counts),
        "DAILY_TRADE_COUNT_MAX": (max(counts) if counts else None),
        "ZERO_TRADE_DAY_N": sum(1 for c in counts if c == 0),
        "ONE_TRADE_DAY_N": sum(1 for c in counts if c == 1),
        "TWO_TRADE_DAY_N": sum(1 for c in counts if c == 2),
        "THREE_PLUS_TRADE_DAY_N": sum(1 for c in counts if c >= 3),
        "daily_counts": [{"date": d, "trade_n": int(by[d])} for d in days],
    }


def daily_yen(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    by: dict[str, list[float]] = {d: [] for d in days}
    for r in rows:
        d = str(r.get("date") or "")
        if d in by and _finite(r.get("pnl_yen_100")):
            by[d].append(float(r["pnl_yen_100"]))
    out = []
    for d in days:
        xs = by[d]
        gp = sum(v for v in xs if v > 0)
        gl = abs(sum(v for v in xs if v < 0))
        tot = float(sum(xs)) if xs else 0.0
        out.append(
            {
                "date": d,
                "trade_n": len(xs),
                "net_pnl": tot,
                "gross_profit": float(gp),
                "gross_loss": float(gl),
            }
        )
    return out


def symbol_yen(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        s = str(r.get("symbol") or "").replace(".T", "")
        if _finite(r.get("pnl_yen_100")):
            by[s].append(float(r["pnl_yen_100"]))
    out = []
    for s, xs in by.items():
        gp = sum(v for v in xs if v > 0)
        gl = abs(sum(v for v in xs if v < 0))
        out.append(
            {
                "symbol": s,
                "trade_n": len(xs),
                "net_pnl": float(sum(xs)),
                "gross_profit": float(gp),
                "gross_loss": float(gl),
            }
        )
    out.sort(key=lambda r: (float(r["net_pnl"]), str(r["symbol"])), reverse=True)
    return out


def _share(part: float, total: float) -> Optional[float]:
    if abs(float(total)) < 1e-12:
        return None
    return float(part) / float(total)


def concentration_yen(rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    daily = daily_yen(rows, days)
    syms = symbol_yen(rows)
    total = float(sum(float(r.get("pnl_yen_100") or 0.0) for r in rows if _finite(r.get("pnl_yen_100"))))
    traded_days = [r for r in daily if int(r.get("trade_n") or 0) > 0]
    day_ord = sorted(traded_days, key=lambda r: float(r["net_pnl"]), reverse=True)
    top_day = day_ord[0] if day_ord else None
    top3_days = day_ord[:3]
    top_day_pnl = float(top_day["net_pnl"]) if top_day else 0.0
    top3_day_pnl = float(sum(float(r["net_pnl"]) for r in top3_days))
    top_sym = syms[0] if syms else None
    top3_sym = syms[:3]
    top_sym_pnl = float(top_sym["net_pnl"]) if top_sym else 0.0
    top3_sym_pnl = float(sum(float(r["net_pnl"]) for r in top3_sym))
    pos_days = [r for r in daily if float(r["net_pnl"]) > 1e-12]
    neg_days = [r for r in daily if float(r["net_pnl"]) < -1e-12]
    no_trade = [r for r in daily if int(r.get("trade_n") or 0) == 0]
    lodo = []
    for r in daily:
        lodo.append(total - float(r["net_pnl"]))
    best = max((float(r["net_pnl"]) for r in daily), default=None)
    worst = min((float(r["net_pnl"]) for r in daily), default=None)
    return {
        "TOTAL_PNL_YEN_100": total,
        "POSITIVE_DAY_N": len(pos_days),
        "NEGATIVE_DAY_N": len(neg_days),
        "NO_TRADE_DAY_N": len(no_trade),
        "BEST_DAY_PNL": best,
        "WORST_DAY_PNL": worst,
        "BEST_DAY": (top_day or {}).get("date"),
        "EX_BEST_DAY_TOTAL_PNL": (total - top_day_pnl) if top_day else total,
        "EX_TOP3_DAY_TOTAL_PNL": (total - top3_day_pnl) if top3_days else total,
        "LODO_MIN_TOTAL_PNL": (min(lodo) if lodo else None),
        "LODO_MEDIAN_TOTAL_PNL": _median(lodo),
        "TOP_SYMBOL": (top_sym or {}).get("symbol"),
        "TOP_SYMBOL_PNL_SHARE": _share(top_sym_pnl, total) if top_sym else None,
        "TOP3_SYMBOL_PNL_SHARE": _share(top3_sym_pnl, total) if top3_sym else None,
        "TOP_DAY_PNL_SHARE": _share(top_day_pnl, total) if top_day else None,
        "TOP3_DAY_PNL_SHARE": _share(top3_day_pnl, total) if top3_days else None,
        "DROP_TOP_SYMBOL_TOTAL_PNL": (total - top_sym_pnl) if top_sym else total,
        "DROP_TOP3_SYMBOL_TOTAL_PNL": (total - top3_sym_pnl) if top3_sym else total,
        "DROP_TOP_DAY_TOTAL_PNL": (total - top_day_pnl) if top_day else total,
        "DROP_TOP3_DAY_TOTAL_PNL": (total - top3_day_pnl) if top3_days else total,
        "daily": daily,
        "symbols": syms,
        "lodo": [{"exclude_date": d, "total_pnl": v} for d, v in zip(days, lodo)],
    }


def pnl_pack(rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    yen = [float(r["pnl_yen_100"]) for r in rows if _finite(r.get("pnl_yen_100"))]
    signs_win = sum(1 for v in yen if v > 1e-12)
    signs_loss = sum(1 for v in yen if v < -1e-12)
    n = len(yen)
    summ = summarize_pnl_yen_100(rows)
    daily = daily_yen(rows, days)
    daily_pnls = [float(r["net_pnl"]) for r in daily]
    n_days = len(days) if days else ELIGIBLE_DAY_N
    active_n = sum(1 for r in daily if int(r.get("trade_n") or 0) > 0)
    total = float(summ.get("total_pnl_yen_100") or 0.0)
    realized_dd = maxdd(rows, time_key="actual_exit_quote_time", pnl_key="pnl_yen_100") if yen else None
    return {
        "TRADE_N": n,
        "WIN_N": signs_win,
        "LOSS_N": signs_loss,
        "FLAT_N": n - signs_win - signs_loss,
        "WIN_RATE": (float(signs_win) / float(n)) if n else None,
        "TOTAL_PNL_YEN_100": summ.get("total_pnl_yen_100"),
        "AVG_PNL_PER_TRADE": summ.get("avg_pnl_yen_100"),
        "MEDIAN_PNL_PER_TRADE": _median(yen),
        "AVG_PNL_PER_ELIGIBLE_DAY": (total / float(n_days)) if n_days else None,
        "MEDIAN_DAILY_PNL": _median(daily_pnls),
        "GROSS_PROFIT": summ.get("gross_profit_yen_100"),
        "GROSS_LOSS": summ.get("gross_loss_yen_100"),
        "PROFIT_FACTOR": summ.get("profit_factor_yen_100"),
        "REALIZED_CLOSE_EQUITY_MAX_DD_YEN": realized_dd,
        "PNL_PER_ACTIVE_DAY": (total / float(active_n)) if active_n else None,
        "PRE_FEE_PNL": True,
        "FEE_MODEL": None,
    }


def stitch_mtm(day_points: list[tuple[str, list[dict[str, Any]]]], day_totals: dict[str, float]) -> dict[str, Any]:
    realized_before = 0.0
    eq = 0.0
    peak = 0.0
    dd = 0.0
    n_pts = 0
    limited = False
    for day, pts in day_points:
        if not pts:
            limited = True
        for p in pts:
            n_pts += 1
            eq = float(realized_before) + float(p.get("realized_today") or 0.0) + float(p.get("unrealized") or 0.0)
            peak = max(peak, eq)
            dd = min(dd, eq - peak)
        realized_before += float(day_totals.get(day) or 0.0)
        eq = realized_before
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        "MARK_TO_MARKET_MAX_DD_YEN": round(float(dd), 2) if n_pts else None,
        "MTM_POINT_N": n_pts,
        "MTM_COMPUTED": bool(n_pts > 0),
        "MTM_LIMITED": bool(limited and n_pts == 0),
    }


def efficiency_pack(pnl: dict[str, Any], occ: dict[str, Any], freq: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = float(pnl.get("TOTAL_PNL_YEN_100") or 0.0)
    dd = pnl.get("REALIZED_CLOSE_EQUITY_MAX_DD_YEN")
    peak = occ.get("PEAK_GROSS_NOTIONAL_YEN")
    mean_n = occ.get("MEAN_GROSS_NOTIONAL_YEN")
    entry_to = 0.0
    exit_to = 0.0
    for r in rows:
        if _finite(r.get("fill_price")):
            entry_to += float(r["fill_price"]) * float(SHARES)
        if _finite(r.get("exit_bid")):
            exit_to += float(r["exit_bid"]) * float(SHARES)
    net_to_dd = None
    if _finite(dd) and abs(float(dd)) > 1e-12:
        net_to_dd = float(total) / abs(float(dd))
    pnl_per_peak = (float(total) / float(peak)) if _finite(peak) and float(peak) > 1e-12 else None
    util = (float(mean_n) / float(peak)) if _finite(mean_n) and _finite(peak) and float(peak) > 1e-12 else None
    return {
        "NET_TO_MAX_DD": net_to_dd,
        "PNL_PER_TRADE": pnl.get("AVG_PNL_PER_TRADE"),
        "PNL_PER_ELIGIBLE_DAY": pnl.get("AVG_PNL_PER_ELIGIBLE_DAY"),
        "PNL_PER_ACTIVE_DAY": pnl.get("PNL_PER_ACTIVE_DAY"),
        "PNL_PER_PEAK_NOTIONAL": pnl_per_peak,
        "TURNOVER_YEN": float(entry_to + exit_to),
        "CAPITAL_UTILIZATION": util,
        "ANNUALIZED": False,
    }


def execution_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    miss_q = sum(1 for r in rows if r.get("entry_quote_missing") or r.get("exit_quote_missing"))
    miss_qty = sum(1 for r in rows if r.get("entry_qty_missing") or r.get("exit_qty_missing"))
    inv = sum(1 for r in rows if r.get("timestamp_inversion"))
    fut = sum(1 for r in rows if r.get("future_use"))
    qty_unresolved = miss_qty > 0 or miss_q > 0
    return {
        "MISSING_QUOTE_N": int(miss_q),
        "MISSING_QUANTITY_EVIDENCE_N": int(miss_qty),
        "TIMESTAMP_INVERSION_N": int(inv),
        "FUTURE_USE_N": int(fut),
        "EXECUTION_EVIDENCE_LIMITED": bool(qty_unresolved),
        "ENTRY_QTY_MISS_N": sum(1 for r in rows if r.get("entry_qty_missing")),
        "EXIT_QTY_MISS_N": sum(1 for r in rows if r.get("exit_qty_missing")),
        "SHARES": int(SHARES),
        "MIN_QTY": 100.0,
    }


def v19_identity_parity(rows: list[dict[str, Any]], official: list[dict[str, Any]]) -> dict[str, Any]:
    hashes = identity_hashes(rows)
    old = {trade_key(r): r for r in official}
    n = 0
    details = []
    for r in rows:
        key = trade_key(r)
        prev = old.get(key)
        if not prev:
            n += 1
            details.append({"key": key, "reason": "MISSING_IN_V19_OFFICIAL"})
            continue
        ok = (
            _close(r.get("fill_price"), prev.get("fill_price"), 1e-9)
            and _close(r.get("scheduled_exit_time"), prev.get("scheduled_exit_time"), 1e-6)
            and _close(r.get("actual_exit_quote_time"), prev.get("actual_exit_quote_time"), 1e-6)
            and _close(r.get("exit_bid"), prev.get("exit_bid"), 1e-9)
            and _close(r.get("pnl_yen_100"), prev.get("pnl_yen_100"), PARITY_YEN_TOL)
        )
        if not ok:
            n += 1
            details.append({"key": key, "reason": "FIELD_MISMATCH"})
    extra = 0
    keys = {trade_key(r) for r in rows}
    for r in official:
        if trade_key(r) not in keys:
            extra += 1
            details.append({"key": trade_key(r), "reason": "MISSING_IN_V20"})
    n += extra
    actual_ok = str(hashes.get("ACTUAL_EXIT_SET_HASH") or "") == ACTUAL_EXIT_SET_HASH_EXPECTED
    return {
        **hashes,
        "TRADE_BY_TRADE_V19_PARITY": bool(n == 0 and len(rows) == int(E4_FILLED_N_EXPECTED) and len(official) == int(E4_FILLED_N_EXPECTED)),
        "EXIT_MISMATCH_N": int(n),
        "ACTUAL_EXIT_SET_HASH_PARITY": bool(actual_ok),
        "details": details[:20],
    }


def numeric_lock(pnl: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "TRADE_N": _int(pnl.get("TRADE_N")) == int(V19_TRADE_N_EXPECTED),
        "WIN_N": _int(pnl.get("WIN_N")) == int(V19_WIN_N_EXPECTED),
        "LOSS_N": _int(pnl.get("LOSS_N")) == int(V19_LOSS_N_EXPECTED),
        "TOTAL_PNL": _close(pnl.get("TOTAL_PNL_YEN_100"), V19_TOTAL_PNL_YEN_100_EXPECTED, PARITY_YEN_TOL),
        "PF": _close(pnl.get("PROFIT_FACTOR"), V19_PROFIT_FACTOR_YEN_100_EXPECTED, PARITY_PF_TOL),
        "REALIZED_DD": _close(pnl.get("REALIZED_CLOSE_EQUITY_MAX_DD_YEN"), V19_REALIZED_MAX_DD_YEN_EXPECTED, PARITY_YEN_TOL),
    }
    return {"V19_NUMERIC_LOCK": all(checks.values()), "checks": checks}


def economics_gate(
    *,
    pnl: dict[str, Any],
    conc: dict[str, Any],
    occ: dict[str, Any],
    integrity_ok: bool,
) -> dict[str, Any]:
    total = pnl.get("TOTAL_PNL_YEN_100")
    pf = pnl.get("PROFIT_FACTOR")
    pos = _int(conc.get("POSITIVE_DAY_N"), 0)
    neg = _int(conc.get("NEGATIVE_DAY_N"), 0)
    checks = {
        "TOTAL_PNL_GT_0": _finite(total) and float(total) > 0.0,
        "PF_GT_1": _finite(pf) and float(pf) > 1.0,
        "POS_DAYS_GT_NEG_DAYS": pos > neg,
        "EX_BEST_DAY_TOTAL_PNL_GT_0": _finite(conc.get("EX_BEST_DAY_TOTAL_PNL")) and float(conc["EX_BEST_DAY_TOTAL_PNL"]) > 0.0,
        "EX_TOP3_DAY_TOTAL_PNL_GT_0": _finite(conc.get("EX_TOP3_DAY_TOTAL_PNL")) and float(conc["EX_TOP3_DAY_TOTAL_PNL"]) > 0.0,
        "DROP_TOP_SYMBOL_TOTAL_PNL_GT_0": _finite(conc.get("DROP_TOP_SYMBOL_TOTAL_PNL")) and float(conc["DROP_TOP_SYMBOL_TOTAL_PNL"]) > 0.0,
        "PORTFOLIO_CONSTRUCTION_RESOLVED": not bool(occ.get("PORTFOLIO_CONSTRUCTION_UNRESOLVED")),
        "INTEGRITY": bool(integrity_ok),
    }
    return {
        "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS": all(checks.values()),
        "checks": checks,
    }


def decision_case(
    *,
    identity_ok: bool,
    integrity_ok: bool,
    occ: dict[str, Any],
    pnl: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    total = pnl.get("TOTAL_PNL_YEN_100")
    if (not identity_ok) or (not integrity_ok):
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_V20_INVALID",
            "NEXT": "STOP. Integrity/identity failed. Do not freeze portfolio economics. Do not retune ENTRY/EXIT.",
            "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS": False,
            "FORWARD_OOS_ELIGIBLE": False,
            "PORTFOLIO_CONSTRUCTION_UNRESOLVED": bool(occ.get("PORTFOLIO_CONSTRUCTION_UNRESOLVED")),
        }
    if occ.get("PORTFOLIO_CONSTRUCTION_UNRESOLVED"):
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V20_PORTFOLIO_CONSTRUCTION_UNRESOLVED",
            "NEXT": "STOP. Same-symbol overlap exists and frozen stack has no merge/reject/pyramid semantics. Do not add a cap. Do not retune ENTRY/EXIT.",
            "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS": False,
            "FORWARD_OOS_ELIGIBLE": False,
            "PORTFOLIO_CONSTRUCTION_UNRESOLVED": True,
        }
    if (not _finite(total)) or float(total) <= 0.0:
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V20_PORTFOLIO_ECONOMICS_FAIL",
            "NEXT": "STOP. Frozen-stack portfolio economics <= 0. Leave the weak result. Do not retune ENTRY/EXIT.",
            "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS": False,
            "FORWARD_OOS_ELIGIBLE": False,
            "PORTFOLIO_CONSTRUCTION_UNRESOLVED": False,
        }
    if gate.get("DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS"):
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V20_FROZEN_PORTFOLIO_ECONOMICS_PASS",
            "NEXT": "STOP. Frozen ENTRY+EXIT development portfolio economics pass. FORWARD_OOS_ELIGIBLE=true. Do not retune ENTRY/EXIT. TRUE_OOS=false. STRATEGY_CERTIFIED=false.",
            "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS": True,
            "FORWARD_OOS_ELIGIBLE": True,
            "PORTFOLIO_CONSTRUCTION_UNRESOLVED": False,
        }
    return {
        "CASE": "B",
        "VERDICT": "SIMPLE_TECH_V20_PORTFOLIO_ECONOMICS_FRAGILE",
        "NEXT": "STOP. Net positive but development robustness failed. FORWARD_OOS_ELIGIBLE=false. Do not retune ENTRY/EXIT.",
        "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS": False,
        "FORWARD_OOS_ELIGIBLE": False,
        "PORTFOLIO_CONSTRUCTION_UNRESOLVED": False,
    }


def fill_input_tuples(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    out = []
    for r in rows:
        out.append(
            (
                str(r.get("date") or ""),
                str(r.get("symbol") or "").replace(".T", ""),
                _canon_num(r.get("fill_t")),
                _canon_num(r.get("fill_price")),
            )
        )
    return sorted(out)


def fill_input_hash(rows: list[dict[str, Any]]) -> str:
    return set_hash(fill_input_tuples(rows))
