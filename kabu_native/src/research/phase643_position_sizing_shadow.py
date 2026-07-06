"""
Phase643 — Position sizing shadow research (research only).

Compares position-sizing variants on Phase630 parity replay + Phase627+ live sessions.
No ENTRY/EXIT/PBv2/OR/YAML/runtime trading logic changes. Main line stays 100 shares.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase631_profit_source_attribution import (
    DAYS as PHASE630_DAYS,
    REPLAY_ROOT as PHASE630_REPLAY_ROOT,
    load_trades_for_day,
)
from research.phase634_pbv2_only_rise5_full_period import (
    discover_replayable_sessions,
    load_trades_for_session,
)
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.position_exposure_audit import price_band_label
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE643_VERDICT = "phase643_position_sizing_shadow_done"
PHASE643_FAIL = "phase643_position_sizing_shadow_failed"

MIN_LOT = 100
HARD_STOP_PCT = 1.20
HIGH_PRICE_THRESHOLD = 3000.0
LOW_PRICE_THRESHOLD = 500.0
CAPITAL_LEVELS: tuple[int, ...] = (1_000_000, 3_000_000, 5_000_000, 10_000_000)
PHASE627_LIVE_CUTOFF = "20260627"
PHASE630_DAY_KEYS = frozenset(d.replace("-", "") for d in PHASE630_DAYS)

# Shadow3 — configurable score → shares map (future YAML hook)
PBV2_SCORE_SHARES: dict[int, int] = {
    3: 100,
    4: 200,
    5: 300,
}
PBV2_SCORE_DEFAULT_SHARES = 100

VARIANTS: tuple[tuple[str, str, str, str], ...] = (
    ("A", "fixed_100", "Baseline", "100 shares fixed"),
    ("B", "equity_10pct", "Shadow1", "Equity 10%"),
    ("B", "equity_20pct", "Shadow1", "Equity 20%"),
    ("B", "equity_30pct", "Shadow1", "Equity 30%"),
    ("B", "equity_40pct", "Shadow1", "Equity 40%"),
    ("B", "equity_50pct", "Shadow1", "Equity 50%"),
    ("C", "risk_0.25pct", "Shadow2", "Risk 0.25% per trade"),
    ("C", "risk_0.50pct", "Shadow2", "Risk 0.50% per trade"),
    ("C", "risk_0.75pct", "Shadow2", "Risk 0.75% per trade"),
    ("C", "risk_1.00pct", "Shadow2", "Risk 1.00% per trade"),
    ("D", "pbv2_score_linked", "Shadow3", "PBv2 score 3/4/5 → 100/200/300"),
    ("E", "liquidity_tv_band", "Shadow4", "TradingValue band → 100/200/300"),
    ("E", "liquidity_turnover_band", "Shadow4", "Turnover band → 100/200/300"),
    ("E", "liquidity_update_freq", "Shadow4", "Board update freq → 100/200/300"),
)

VARIANT_COMPARISON_FIELDS = [
    "variant_id",
    "variant_key",
    "shadow_group",
    "variant_label",
    "initial_equity_yen",
    "executed_trades",
    "capital_skip_count",
    "total_pnl_yen",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen",
    "max_drawdown_yen",
    "sharpe_ratio",
    "avg_position_yen",
    "avg_capital_utilization_pct",
    "delta_pnl_vs_fixed_100",
    "delta_pf_vs_fixed_100",
    "delta_maxdd_vs_fixed_100",
    "high_price_pnl_yen",
    "low_price_pnl_yen",
    "entry_count_delta_vs_fixed_100",
]

DAILY_BREAKDOWN_FIELDS = [
    "day",
    "session_kind",
    "entry_pool",
    "variant_key",
    "initial_equity_yen",
    "executed_trades",
    "capital_skip_count",
    "total_pnl_yen",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen",
]

SYMBOL_BREAKDOWN_FIELDS = [
    "symbol",
    "price_band",
    "price_tier",
    "variant_key",
    "initial_equity_yen",
    "executed_trades",
    "capital_skip_count",
    "total_pnl_yen",
    "profit_factor",
    "win_rate",
    "pnl_share_pct",
]

EQUITY_CURVE_FIELDS = [
    "day",
    "variant_key",
    "initial_equity_yen",
    "equity_yen",
    "daily_pnl_yen",
    "drawdown_yen",
    "executed_trades_cum",
    "capital_skip_cum",
]

SKIP_ANALYSIS_FIELDS = [
    "variant_key",
    "initial_equity_yen",
    "skip_reason",
    "skip_count",
    "skipped_pnl_yen_100",
    "avg_entry_price",
    "high_price_skip_count",
    "low_price_skip_count",
]


@dataclass
class LiquidityTertiles:
    tv_lo: float = 50_000_000.0
    tv_hi: float = 200_000_000.0
    turnover_lo: float = 0.5
    turnover_hi: float = 2.0
    update_lo: float = 5.0
    update_hi: float = 15.0


def _num(v: Any) -> float:
    return _float(v) or 0.0


def _session_kind(entry_time: Any) -> str:
    dt = _parse_ts(str(entry_time or ""))
    if dt is None:
        return "UNKNOWN"
    return "AM" if dt.hour < 12 or (dt.hour == 12 and dt.minute < 25) else "PM"


def _price_tier(entry_price: float) -> str:
    if entry_price >= HIGH_PRICE_THRESHOLD:
        return "high_price"
    if entry_price <= LOW_PRICE_THRESHOLD:
        return "low_price"
    return "mid_price"


def _tertile(values: Sequence[float]) -> tuple[float, float]:
    xs = sorted(v for v in values if v > 0)
    if len(xs) < 6:
        return (xs[len(xs) // 3] if xs else 0.0, xs[(2 * len(xs)) // 3] if xs else 0.0)
    n = len(xs)
    return (xs[n // 3], xs[(2 * n) // 3])


def build_liquidity_tertiles(trades: Sequence[Mapping[str, Any]]) -> LiquidityTertiles:
    tvs = [_num(t.get("trading_value")) for t in trades if _num(t.get("trading_value")) > 0]
    turns = [_num(t.get("turnover_proxy")) for t in trades if _num(t.get("turnover_proxy")) > 0]
    updates = [
        _num(t.get("update_count_before_entry"))
        for t in trades
        if _num(t.get("update_count_before_entry")) > 0
    ]
    tv_lo, tv_hi = _tertile(tvs)
    to_lo, to_hi = _tertile(turns)
    up_lo, up_hi = _tertile(updates)
    return LiquidityTertiles(
        tv_lo=tv_lo or 50_000_000.0,
        tv_hi=tv_hi or 200_000_000.0,
        turnover_lo=to_lo or 0.5,
        turnover_hi=to_hi or 2.0,
        update_lo=up_lo or 5.0,
        update_hi=up_hi or 15.0,
    )


def _tier_shares(value: float, lo: float, hi: float) -> int:
    if value <= 0:
        return MIN_LOT
    if value < lo:
        return MIN_LOT
    if value < hi:
        return 200
    return 300


def _pbv2_target_shares(score: Any) -> int:
    s = int(_num(score)) if score not in (None, "") else 0
    if s >= 5:
        return PBV2_SCORE_SHARES.get(5, 300)
    return PBV2_SCORE_SHARES.get(s, PBV2_SCORE_DEFAULT_SHARES)


def _round_down_lots(shares_raw: float) -> int:
    if shares_raw < MIN_LOT:
        return 0
    return int(math.floor(shares_raw / MIN_LOT) * MIN_LOT)


def compute_variant_shares(
    variant_key: str,
    *,
    equity: float,
    entry_price: float,
    trade: Mapping[str, Any],
    liquidity: LiquidityTertiles,
) -> tuple[int, Optional[str]]:
    if entry_price <= 0 or equity <= 0:
        return 0, "invalid_price"

    if variant_key == "fixed_100":
        target = MIN_LOT
    elif variant_key.startswith("equity_"):
        pct = int(variant_key.replace("equity_", "").replace("pct", "")) / 100.0
        budget = equity * pct
        target = _round_down_lots(budget / entry_price)
        if target < MIN_LOT:
            return 0, "below_min_lot"
    elif variant_key.startswith("risk_"):
        risk_pct = float(variant_key.replace("risk_", "").replace("pct", "")) / 100.0
        risk_yen = equity * risk_pct
        stop_yen_per_share = entry_price * (HARD_STOP_PCT / 100.0)
        if stop_yen_per_share <= 0:
            return 0, "invalid_stop"
        target = _round_down_lots(risk_yen / stop_yen_per_share)
        if target < MIN_LOT:
            return 0, "risk_size_below_min_lot"
    elif variant_key == "pbv2_score_linked":
        target = _pbv2_target_shares(trade.get("entry_expectancy_score_v2"))
    elif variant_key == "liquidity_tv_band":
        target = _tier_shares(_num(trade.get("trading_value")), liquidity.tv_lo, liquidity.tv_hi)
    elif variant_key == "liquidity_turnover_band":
        target = _tier_shares(_num(trade.get("turnover_proxy")), liquidity.turnover_lo, liquidity.turnover_hi)
    elif variant_key == "liquidity_update_freq":
        target = _tier_shares(
            _num(trade.get("update_count_before_entry")), liquidity.update_lo, liquidity.update_hi
        )
    else:
        target = MIN_LOT

    pos_val = entry_price * target
    if pos_val > equity:
        affordable = _round_down_lots(equity / entry_price)
        if affordable < MIN_LOT:
            return 0, "insufficient_equity"
        return affordable, "capped_by_equity"
    return target, None


def _trade_key(t: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(t.get("day") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))


def _enrich_trade_prices(trades: list[dict[str, Any]], day_dir: Path) -> None:
    """Phase631 rows omit entry_price; re-attach from events join."""
    events_fp = day_dir / "small_paper_events.jsonl"
    if not events_fp.is_file():
        return
    accepted: dict[tuple[Any, Any], dict[str, Any]] = {}
    exits: dict[tuple[Any, Any], dict[str, Any]] = {}
    for line in events_fp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (e.get("symbol"), e.get("entry_time") or e.get("message_index"))
        if e.get("event_type") == "accepted":
            accepted[key] = e
        elif e.get("event_type") == "observer_exit":
            exits[key] = e
    for t in trades:
        key = (t.get("symbol"), t.get("entry_time"))
        acc = accepted.get(key) or {}
        ex = exits.get(key) or {}
        ep = _float(ex.get("entry_price") or acc.get("entry_price") or acc.get("current_price"))
        if ep and ep > 0:
            t["entry_price"] = round(ep, 4)
        xp = _float(ex.get("exit_price") or ex.get("current_price"))
        if xp and xp > 0:
            t["exit_price"] = round(xp, 4)


def load_all_phase643_trades(
    *,
    native_root: Path,
    include_live: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"phase630_days": list(PHASE630_DAYS), "live_sessions": []}

    replay_root = native_root / "results" / "small_paper" / "_phase630" / "current"
    if not replay_root.is_dir():
        replay_root = PHASE630_REPLAY_ROOT

    for day in PHASE630_DAYS:
        day_key = day.replace("-", "")
        day_dir = replay_root / day_key
        if day_dir.is_dir():
            day_trades = load_trades_for_day(day_dir, day)
            _enrich_trade_prices(day_trades, day_dir)
            trades.extend(day_trades)

    seen = {_trade_key(t) for t in trades}

    if include_live:
        live_root = native_root / "results" / "small_paper"
        for sess in discover_replayable_sessions(live_root):
            day_key = str(sess.get("day_key") or "")
            if day_key in PHASE630_DAY_KEYS:
                continue
            if day_key < PHASE627_LIVE_CUTOFF:
                continue
            sess_dir = Path(str(sess.get("session_dir") or ""))
            if not sess_dir.is_dir():
                continue
            day = str(sess.get("day") or "")
            for t in load_trades_for_session(sess_dir, day):
                key = _trade_key(t)
                if key in seen:
                    continue
                seen.add(key)
                t = dict(t)
                _enrich_trade_prices([t], sess_dir)
                t["_source"] = "live_session"
                t["session"] = sess.get("session")
                trades.append(t)
            meta["live_sessions"].append(
                {"day": day, "session": sess.get("session"), "trade_count": sess.get("trade_count")}
            )

    for t in trades:
        ep = _num(t.get("entry_price"))
        if ep <= 0:
            ep = _num(t.get("current_price"))
        if ep > 0:
            t["entry_price"] = round(ep, 4)
        t.setdefault("_source", "phase630_replay")
        t["session_kind"] = _session_kind(t.get("entry_time"))
        t["price_band"] = price_band_label(_num(t.get("entry_price")))
        t["price_tier"] = _price_tier(_num(t.get("entry_price")))

    trades = [t for t in trades if _num(t.get("entry_price")) > 0]
    trades.sort(key=lambda r: _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST))
    meta["trade_count"] = len(trades)
    meta["replay_trade_count"] = sum(1 for t in trades if t.get("_source") == "phase630_replay")
    meta["live_trade_count"] = sum(1 for t in trades if t.get("_source") == "live_session")
    return trades, meta


def _sharpe_from_pnls(pnls: Sequence[float]) -> float:
    if len(pnls) < 2:
        return 0.0
    mu = statistics.fmean(pnls)
    sd = statistics.pstdev(pnls)
    if sd <= 1e-12:
        return 0.0
    return round(mu / sd * math.sqrt(len(pnls)), 4)


def simulate_variant(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant_key: str,
    initial_equity: int,
    liquidity: LiquidityTertiles,
) -> dict[str, Any]:
    equity = float(initial_equity)
    peak = equity
    max_dd = 0.0
    executed = 0
    skips = 0
    skip_rows: list[dict[str, Any]] = []
    skip_reasons: dict[str, int] = {}
    pnls: list[float] = []
    pos_vals: list[float] = []
    utilizations: list[float] = []
    daily_pnl: dict[str, float] = {}
    high_price_pnl = 0.0
    low_price_pnl = 0.0

    for t in trades:
        ep = _num(t.get("entry_price"))
        shares, skip_reason = compute_variant_shares(
            variant_key,
            equity=equity,
            entry_price=ep,
            trade=t,
            liquidity=liquidity,
        )
        pnl100 = _num(t.get("pnl_yen_100"))
        day = str(t.get("day") or "")[:10]

        if shares < MIN_LOT:
            skips += 1
            reason = skip_reason or "skipped"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            skip_rows.append(
                {
                    "day": day,
                    "symbol": t.get("symbol"),
                    "entry_price": ep,
                    "skip_reason": reason,
                    "pnl_yen_100": pnl100,
                    "price_tier": t.get("price_tier"),
                }
            )
            continue

        pos_val = ep * shares
        pnl = round(pnl100 * shares / MIN_LOT, 2)
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        executed += 1
        pnls.append(pnl)
        pos_vals.append(pos_val)
        utilizations.append(pos_val / float(initial_equity) if initial_equity > 0 else 0.0)
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
        tier = str(t.get("price_tier") or "")
        if tier == "high_price":
            high_price_pnl += pnl
        elif tier == "low_price":
            low_price_pnl += pnl

    return {
        "variant_key": variant_key,
        "initial_equity_yen": initial_equity,
        "executed_trades": executed,
        "capital_skip_count": skips,
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
        "avg_pnl_yen": round(statistics.fmean(pnls), 2) if pnls else 0.0,
        "max_drawdown_yen": round(max_dd, 2),
        "sharpe_ratio": _sharpe_from_pnls(pnls),
        "avg_position_yen": round(statistics.fmean(pos_vals), 2) if pos_vals else 0.0,
        "avg_capital_utilization_pct": round(statistics.fmean(utilizations) * 100.0, 4) if utilizations else 0.0,
        "high_price_pnl_yen": round(high_price_pnl, 2),
        "low_price_pnl_yen": round(low_price_pnl, 2),
        "_pnls": pnls,
        "_skip_rows": skip_rows,
        "_skip_reasons": skip_reasons,
        "_daily_pnl": daily_pnl,
    }


def _daily_breakdown_rows(
    trades: Sequence[Mapping[str, Any]],
    sim: Mapping[str, Any],
    *,
    liquidity: LiquidityTertiles,
) -> list[dict[str, Any]]:
    variant_key = str(sim.get("variant_key") or "")
    equity = int(sim.get("initial_equity_yen") or 0)
    skip_set = {
        (str(r.get("day")), str(r.get("symbol")), _num(r.get("entry_price")))
        for r in sim.get("_skip_rows") or []
    }
    buckets: dict[tuple[str, str, str], list[float]] = {}
    skip_buckets: dict[tuple[str, str, str], int] = {}

    for t in trades:
        day = str(t.get("day") or "")[:10]
        kind = str(t.get("session_kind") or "UNKNOWN")
        pool = str(t.get("entry_pool") or "PBV2")
        key = (day, kind, pool)
        ep = _num(t.get("entry_price"))
        sk = (day, str(t.get("symbol")), ep)
        if sk in skip_set:
            skip_buckets[key] = skip_buckets.get(key, 0) + 1
            continue
        shares, _ = compute_variant_shares(
            variant_key, equity=equity, entry_price=ep, trade=t, liquidity=liquidity
        )
        if shares < MIN_LOT:
            skip_buckets[key] = skip_buckets.get(key, 0) + 1
            continue
        pnl = _num(t.get("pnl_yen_100")) * shares / MIN_LOT
        buckets.setdefault(key, []).append(pnl)

    rows: list[dict[str, Any]] = []
    keys = sorted(set(buckets.keys()) | set(skip_buckets.keys()))
    for day, kind, pool in keys:
        pnls = buckets.get((day, kind, pool)) or []
        rows.append(
            {
                "day": day,
                "session_kind": kind,
                "entry_pool": pool,
                "variant_key": variant_key,
                "initial_equity_yen": equity,
                "executed_trades": len(pnls),
                "capital_skip_count": skip_buckets.get((day, kind, pool), 0),
                "total_pnl_yen": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "avg_pnl_yen": round(statistics.fmean(pnls), 2) if pnls else 0.0,
            }
        )
    return rows


def _symbol_breakdown_rows(
    trades: Sequence[Mapping[str, Any]],
    sim: Mapping[str, Any],
    *,
    liquidity: LiquidityTertiles,
) -> list[dict[str, Any]]:
    variant_key = str(sim.get("variant_key") or "")
    equity = int(sim.get("initial_equity_yen") or 0)
    skip_set = {
        (str(r.get("day")), str(r.get("symbol")), _num(r.get("entry_price")))
        for r in sim.get("_skip_rows") or []
    }
    by_sym: dict[str, list[float]] = {}
    skip_by_sym: dict[str, int] = {}
    meta_by_sym: dict[str, dict[str, str]] = {}

    for t in trades:
        sym = str(t.get("symbol") or "")
        ep = _num(t.get("entry_price"))
        sk = (str(t.get("day")), sym, ep)
        meta_by_sym[sym] = {
            "price_band": str(t.get("price_band") or ""),
            "price_tier": str(t.get("price_tier") or ""),
        }
        if sk in skip_set:
            skip_by_sym[sym] = skip_by_sym.get(sym, 0) + 1
            continue
        shares, _ = compute_variant_shares(
            variant_key, equity=equity, entry_price=ep, trade=t, liquidity=liquidity
        )
        if shares < MIN_LOT:
            skip_by_sym[sym] = skip_by_sym.get(sym, 0) + 1
            continue
        pnl = _num(t.get("pnl_yen_100")) * shares / MIN_LOT
        by_sym.setdefault(sym, []).append(pnl)

    total = sum(sum(v) for v in by_sym.values()) or 0.0
    rows: list[dict[str, Any]] = []
    for sym in sorted(by_sym.keys()):
        pnls = by_sym[sym]
        pnl_sum = sum(pnls)
        meta = meta_by_sym.get(sym, {})
        rows.append(
            {
                "symbol": sym,
                "price_band": meta.get("price_band", ""),
                "price_tier": meta.get("price_tier", ""),
                "variant_key": variant_key,
                "initial_equity_yen": equity,
                "executed_trades": len(pnls),
                "capital_skip_count": skip_by_sym.get(sym, 0),
                "total_pnl_yen": round(pnl_sum, 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "pnl_share_pct": round(pnl_sum / total * 100.0, 2) if total else 0.0,
            }
        )
    return rows


def _equity_curve_rows(sim: Mapping[str, Any]) -> list[dict[str, Any]]:
    variant_key = str(sim.get("variant_key") or "")
    initial = int(sim.get("initial_equity_yen") or 0)
    daily = dict(sim.get("_daily_pnl") or {})
    cum_eq = float(initial)
    peak = cum_eq
    exec_cum = int(sim.get("executed_trades") or 0)
    skip_cum = int(sim.get("capital_skip_count") or 0)
    rows: list[dict[str, Any]] = []
    for day in sorted(daily.keys()):
        dpnl = daily[day]
        cum_eq += dpnl
        peak = max(peak, cum_eq)
        rows.append(
            {
                "day": day,
                "variant_key": variant_key,
                "initial_equity_yen": initial,
                "equity_yen": round(cum_eq, 2),
                "daily_pnl_yen": round(dpnl, 2),
                "drawdown_yen": round(peak - cum_eq, 2),
                "executed_trades_cum": exec_cum,
                "capital_skip_cum": skip_cum,
            }
        )
    return rows


def _skip_analysis_rows(sim: Mapping[str, Any]) -> list[dict[str, Any]]:
    variant_key = str(sim.get("variant_key") or "")
    equity = int(sim.get("initial_equity_yen") or 0)
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for row in sim.get("_skip_rows") or []:
        reason = str(row.get("skip_reason") or "skipped")
        by_reason.setdefault(reason, []).append(dict(row))
    out: list[dict[str, Any]] = []
    for reason, items in sorted(by_reason.items()):
        prices = [_num(r.get("entry_price")) for r in items]
        out.append(
            {
                "variant_key": variant_key,
                "initial_equity_yen": equity,
                "skip_reason": reason,
                "skip_count": len(items),
                "skipped_pnl_yen_100": round(sum(_num(r.get("pnl_yen_100")) for r in items), 2),
                "avg_entry_price": round(statistics.fmean(prices), 2) if prices else 0.0,
                "high_price_skip_count": sum(
                    1 for r in items if str(r.get("price_tier")) == "high_price"
                ),
                "low_price_skip_count": sum(
                    1 for r in items if str(r.get("price_tier")) == "low_price"
                ),
            }
        )
    return out


def _variant_meta() -> dict[str, tuple[str, str, str]]:
    return {vk: (vid, grp, lbl) for vid, vk, grp, lbl in VARIANTS}


def _attach_deltas(summary_rows: list[dict[str, Any]]) -> None:
    fixed = {
        (int(r["initial_equity_yen"]), r["variant_key"]): r
        for r in summary_rows
        if r.get("variant_key") == "fixed_100"
    }
    for r in summary_rows:
        f = fixed.get((int(r["initial_equity_yen"]), "fixed_100"), {})
        r["delta_pnl_vs_fixed_100"] = round(_num(r.get("total_pnl_yen")) - _num(f.get("total_pnl_yen")), 2)
        r["delta_pf_vs_fixed_100"] = round(_num(r.get("profit_factor")) - _num(f.get("profit_factor")), 4)
        r["delta_maxdd_vs_fixed_100"] = round(
            _num(r.get("max_drawdown_yen")) - _num(f.get("max_drawdown_yen")), 2
        )
        r["entry_count_delta_vs_fixed_100"] = int(r.get("executed_trades") or 0) - int(
            f.get("executed_trades") or 0
        )


def _symbol_concentration(symbol_rows: Sequence[Mapping[str, Any]]) -> float:
    if not symbol_rows:
        return 0.0
    total = sum(abs(_num(r.get("total_pnl_yen"))) for r in symbol_rows) or 1.0
    top = max(abs(_num(r.get("total_pnl_yen"))) for r in symbol_rows)
    return round(top / total * 100.0, 2)


def _mandatory_answers(
    *,
    summary_rows: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    liquidity: LiquidityTertiles,
    symbol_rows_by_variant: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    meta = _variant_meta()

    def _candidates(equity: int) -> list[dict[str, Any]]:
        return [dict(r) for r in summary_rows if int(r.get("initial_equity_yen") or 0) == equity]

    eq_primary = 3_000_000
    pool = _candidates(eq_primary)
    fixed = next((r for r in pool if r.get("variant_key") == "fixed_100"), {})
    non_fixed = [r for r in pool if r.get("variant_key") != "fixed_100"]

    best_pnl = max(pool, key=lambda r: _num(r.get("total_pnl_yen")), default={})
    best_pf = max(pool, key=lambda r: _num(r.get("profit_factor")), default={})
    best_dd = min(pool, key=lambda r: _num(r.get("max_drawdown_yen")), default={})

    hp_dep_fixed = _num(fixed.get("high_price_pnl_yen"))
    hp_dep_best = max(non_fixed, key=lambda r: _num(r.get("high_price_pnl_yen")), default={})
    hp_improved = _num(hp_dep_best.get("high_price_pnl_yen")) > hp_dep_fixed * 1.05

    dd_shallower = any(
        _num(r.get("max_drawdown_yen")) < _num(fixed.get("max_drawdown_yen")) for r in non_fixed
    )
    profit_higher = any(
        _num(r.get("total_pnl_yen")) > _num(fixed.get("total_pnl_yen")) for r in non_fixed
    )
    entry_reduced = any(
        int(r.get("executed_trades") or 0) < int(fixed.get("executed_trades") or 0) for r in non_fixed
    )
    winrate_changed = any(
        abs(_num(r.get("win_rate")) - _num(fixed.get("win_rate"))) >= 0.005 for r in non_fixed
    )

    conc_fixed = _symbol_concentration(symbol_rows_by_variant.get("fixed_100|3000000", []))
    conc_candidates = [
        (_symbol_concentration(symbol_rows_by_variant.get(f"{r.get('variant_key')}|3000000", [])), r)
        for r in non_fixed
    ]
    conc_worse = any(c[0] > conc_fixed + 5 for c in conc_candidates if c[1])

    min_lot_ok = all(
        int(r.get("capital_skip_count") or 0) <= int(fixed.get("capital_skip_count") or 0) + 500
        for r in non_fixed
    )

    beats_fixed = [
        r
        for r in non_fixed
        if _num(r.get("total_pnl_yen")) > _num(fixed.get("total_pnl_yen"))
        and _num(r.get("profit_factor")) >= _num(fixed.get("profit_factor")) * 0.98
    ]
    exceeds_fixed = bool(beats_fixed)
    runtime_candidate = [
        r
        for r in beats_fixed
        if _num(r.get("max_drawdown_yen")) <= _num(fixed.get("max_drawdown_yen")) * 1.15
        and int(r.get("capital_skip_count") or 0) <= int(fixed.get("executed_trades") or 0) * 0.05 + 50
    ]

    continue_shadow = sorted(
        non_fixed,
        key=lambda r: (
            _num(r.get("total_pnl_yen")) - _num(fixed.get("total_pnl_yen")),
            _num(r.get("profit_factor")) - _num(fixed.get("profit_factor")),
            -_num(r.get("max_drawdown_yen")),
        ),
        reverse=True,
    )[:3]

    return {
        "1_highest_profit_variant": best_pnl.get("variant_key"),
        "1_highest_profit_yen": best_pnl.get("total_pnl_yen"),
        "1_highest_profit_equity_yen": best_pnl.get("initial_equity_yen"),
        "2_best_profit_factor_variant": best_pf.get("variant_key"),
        "2_best_profit_factor": best_pf.get("profit_factor"),
        "3_lowest_max_dd_variant": best_dd.get("variant_key"),
        "3_lowest_max_dd_yen": best_dd.get("max_drawdown_yen"),
        "4_operationally_feasible": min_lot_ok and all(
            int(r.get("capital_skip_count") or 0) < int(fixed.get("executed_trades") or 1) * 0.25
            for r in runtime_candidate or non_fixed[:1]
        ),
        "4_min_lot_constraint_ok": min_lot_ok,
        "5_beats_fixed_100_pnl": exceeds_fixed,
        "5_beating_variants": [r.get("variant_key") for r in beats_fixed],
        "6_mainline_candidate": [r.get("variant_key") for r in runtime_candidate],
        "6_mainline_candidate_detail": [
            {
                "variant_key": r.get("variant_key"),
                "shadow_group": meta.get(str(r.get("variant_key")), ("", "", ""))[1],
                "total_pnl_yen": r.get("total_pnl_yen"),
                "profit_factor": r.get("profit_factor"),
                "max_drawdown_yen": r.get("max_drawdown_yen"),
                "capital_skip_count": r.get("capital_skip_count"),
            }
            for r in runtime_candidate
        ],
        "7_continue_shadow_variants": [r.get("variant_key") for r in continue_shadow],
        "analysis_high_price_dependency_improved": hp_improved,
        "analysis_dd_shallower_than_fixed": dd_shallower,
        "analysis_profit_higher_than_fixed": profit_higher,
        "analysis_entry_count_reduced": entry_reduced,
        "analysis_winrate_changed": winrate_changed,
        "analysis_symbol_concentration_worse": conc_worse,
        "analysis_min_lot_feasible": min_lot_ok,
        "reference_fixed_100_3M": {
            k: fixed.get(k)
            for k in (
                "total_pnl_yen",
                "profit_factor",
                "max_drawdown_yen",
                "win_rate",
                "executed_trades",
                "capital_skip_count",
                "high_price_pnl_yen",
                "low_price_pnl_yen",
            )
        },
        "liquidity_tertiles": liquidity.__dict__,
        "trade_count": len(trades),
    }


@dataclass
class Phase643Job:
    native_root: Path
    report_dir: Optional[Path] = None
    include_live: bool = True

    def run(self) -> dict[str, Any]:
        native = self.native_root.resolve()
        trades, load_meta = load_all_phase643_trades(native_root=native, include_live=self.include_live)
        if not trades:
            raise RuntimeError("No trades loaded for Phase643")

        liquidity = build_liquidity_tertiles(trades)
        vmeta = _variant_meta()
        sims: list[dict[str, Any]] = []
        summary_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        symbol_rows: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        skip_rows: list[dict[str, Any]] = []
        symbol_rows_by_variant: dict[str, list[dict[str, Any]]] = {}

        variant_keys = [vk for _vid, vk, _grp, _lbl in VARIANTS]
        for equity in CAPITAL_LEVELS:
            for variant_key in variant_keys:
                sim = simulate_variant(
                    trades, variant_key=variant_key, initial_equity=equity, liquidity=liquidity
                )
                sims.append(sim)
                vid, grp, lbl = vmeta.get(variant_key, ("?", "?", variant_key))
                row = {
                    "variant_id": vid,
                    "variant_key": variant_key,
                    "shadow_group": grp,
                    "variant_label": lbl,
                    **{k: sim.get(k) for k in sim if not k.startswith("_")},
                }
                summary_rows.append(row)
                daily_rows.extend(_daily_breakdown_rows(trades, sim, liquidity=liquidity))
                sym = _symbol_breakdown_rows(trades, sim, liquidity=liquidity)
                symbol_rows.extend(sym)
                symbol_rows_by_variant[f"{variant_key}|{equity}"] = sym
                equity_rows.extend(_equity_curve_rows(sim))
                skip_rows.extend(_skip_analysis_rows(sim))

        _attach_deltas(summary_rows)

        answers = _mandatory_answers(
            summary_rows=summary_rows,
            trades=trades,
            liquidity=liquidity,
            symbol_rows_by_variant=symbol_rows_by_variant,
        )

        all_pass = len(trades) >= 100 and len(summary_rows) == len(CAPITAL_LEVELS) * len(variant_keys)
        return {
            "verdict": PHASE643_VERDICT if all_pass else PHASE643_FAIL,
            "generated_at": _now_iso(),
            "load_meta": load_meta,
            "mandatory_answers": answers,
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "symbol_rows": symbol_rows,
            "equity_rows": equity_rows,
            "skip_rows": skip_rows,
            "liquidity_tertiles": liquidity.__dict__,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out_dir = self.report_dir or (
            self.native_root / "results" / "reports" / "phase643_position_sizing_shadow"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        _write_csv(out_dir / "phase643_variant_comparison.csv", VARIANT_COMPARISON_FIELDS, result["summary_rows"])
        paths["variant_comparison"] = out_dir / "phase643_variant_comparison.csv"
        _write_csv(out_dir / "phase643_daily_breakdown.csv", DAILY_BREAKDOWN_FIELDS, result["daily_rows"])
        paths["daily_breakdown"] = out_dir / "phase643_daily_breakdown.csv"
        _write_csv(out_dir / "phase643_symbol_breakdown.csv", SYMBOL_BREAKDOWN_FIELDS, result["symbol_rows"])
        paths["symbol_breakdown"] = out_dir / "phase643_symbol_breakdown.csv"
        _write_csv(out_dir / "phase643_equity_curve.csv", EQUITY_CURVE_FIELDS, result["equity_rows"])
        paths["equity_curve"] = out_dir / "phase643_equity_curve.csv"
        _write_csv(out_dir / "phase643_skip_analysis.csv", SKIP_ANALYSIS_FIELDS, result["skip_rows"])
        paths["skip_analysis"] = out_dir / "phase643_skip_analysis.csv"

        report = {
            "phase": "643",
            "verdict": result.get("verdict"),
            "generated_at": result.get("generated_at"),
            "mandatory_answers": result.get("mandatory_answers"),
            "load_meta": result.get("load_meta"),
            "liquidity_tertiles": result.get("liquidity_tertiles"),
            "variant_count": len({r.get("variant_key") for r in result.get("summary_rows") or []}),
            "equity_levels": list(CAPITAL_LEVELS),
            "artifacts": {k: str(v) for k, v in paths.items()},
            "constraints": {
                "mainline_lot": MIN_LOT,
                "no_entry_exit_change": True,
                "no_real_orders": True,
                "research_only": True,
            },
        }
        report_fp = out_dir / "phase643_report.json"
        report_fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"] = report_fp
        return paths


def main() -> int:
    here = Path(__file__).resolve()
    native = here.parents[2]
    job = Phase643Job(native_root=native)
    result = job.run()
    paths = job.write_outputs(result)
    print(json.dumps({"verdict": result.get("verdict"), "paths": {k: str(v) for k, v in paths.items()}}, indent=2))
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2))
    return 0 if result.get("verdict") == PHASE643_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
