"""Trade-sequence / intraday / daily / portfolio drawdown (separated)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from research.price_flow_exit_integrity.trades import SimTrade


def _max_dd_from_equity(equity: Sequence[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)
    return round(max_dd, 2)


def trade_sequence_dd(trades: Sequence[SimTrade]) -> float:
    """Max DD on cumulative PnL ordered by exit time (trade sequence)."""
    xs = sorted(trades, key=lambda t: (t.day, t.exit_time, t.symbol))
    cum = 0.0
    eq = []
    for t in xs:
        cum += t.pnl_5bps
        eq.append(cum)
    return _max_dd_from_equity(eq)


def intraday_max_dd(trades: Sequence[SimTrade]) -> float:
    """Worst intra-day trade-sequence DD across days."""
    by_day: dict[str, list[SimTrade]] = defaultdict(list)
    for t in trades:
        by_day[t.day].append(t)
    worst = 0.0
    for _d, xs in by_day.items():
        dd = trade_sequence_dd(xs)
        worst = min(worst, dd)
    return round(worst, 2)


def daily_close_max_dd(trades: Sequence[SimTrade]) -> float:
    """Max DD on daily closed PnL series (NOT portfolio DD display)."""
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t.day] += t.pnl_5bps
    cum = 0.0
    eq = []
    for d in sorted(by_day):
        cum += by_day[d]
        eq.append(cum)
    return _max_dd_from_equity(eq)


def consecutive_loss_streak(trades: Sequence[SimTrade]) -> int:
    xs = sorted(trades, key=lambda t: (t.day, t.exit_time, t.symbol))
    best = cur = 0
    for t in xs:
        if t.pnl_5bps < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def max_open_loss(trades: Sequence[SimTrade]) -> float:
    """Worst single-trade pnl (open loss proxy on closed trades)."""
    if not trades:
        return 0.0
    return round(min(t.pnl_5bps for t in trades), 2)


def peak_gross_exposure(trades: Sequence[SimTrade], *, shares: int = 100) -> float:
    """Peak concurrent notional = sum(entry_price*shares) of overlapping opens."""
    events: list[tuple] = []
    for t in trades:
        notional = t.entry_price * shares
        events.append((t.entry_time, 1, notional))
        events.append((t.exit_time, -1, notional))
    events.sort(key=lambda e: (e[0], e[1]))
    cur = peak = 0.0
    for _ts, sgn, n in events:
        cur += sgn * n
        peak = max(peak, cur)
    return round(peak, 2)


def summarize_dd(trades: Sequence[SimTrade]) -> dict[str, Any]:
    ts_dd = trade_sequence_dd(trades)
    return {
        "trade_sequence_max_dd": ts_dd,
        "intraday_max_dd": intraday_max_dd(trades),
        "daily_close_max_dd": daily_close_max_dd(trades),
        "cap5_portfolio_max_dd": ts_dd,  # CAP=5 portfolio DD = trade-sequence under CAP filter
        "consecutive_loss": consecutive_loss_streak(trades),
        "max_open_loss": max_open_loss(trades),
        "peak_gross_exposure": peak_gross_exposure(trades),
        "note": "portfolio max DD uses trade-sequence under CAP=5; daily_close_max_dd is separate",
    }


def equity_curve_rows(trades: Sequence[SimTrade]) -> list[dict[str, Any]]:
    xs = sorted(trades, key=lambda t: (t.day, t.exit_time, t.symbol))
    cum = 0.0
    peak = 0.0
    rows = []
    for t in xs:
        cum += t.pnl_5bps
        peak = max(peak, cum)
        rows.append(
            {
                "day": t.day,
                "symbol": t.symbol,
                "exit_time": t.exit_time.isoformat(),
                "pnl_5bps": t.pnl_5bps,
                "cum_pnl": round(cum, 2),
                "dd_from_peak": round(cum - peak, 2),
                "exit_reason": t.exit_reason,
                "mode": t.mode,
            }
        )
    return rows
