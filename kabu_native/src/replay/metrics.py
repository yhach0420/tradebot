"""
Replay metrics aggregation (trades, daily, symbol, aggregate).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from replay.pnl_yen import enrich_trade_pnl_yen, summarize_pnl_yen_100


def trades_to_rows(trades: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        base = t.to_row() if hasattr(t, "to_row") else dict(t)
        trade_date = getattr(t, "trade_date", None) or base.get("trade_date", "")
        rows.append(enrich_trade_pnl_yen({**base, "trade_date": trade_date}))
    return rows


def _pnls(trades: Sequence[Any]) -> list[float]:
    out: list[float] = []
    for t in trades:
        p = getattr(t, "pnl_pct", None)
        if p is None and isinstance(t, Mapping):
            p = t.get("pnl_pct")
        if p is not None:
            out.append(float(p))
    return out


def _summary_block(trades: Sequence[Any]) -> dict[str, Any]:
    pnls = _pnls(trades)
    n = len(trades)
    if n == 0:
        return {
            "trades": 0,
            "win_rate": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "median_pnl_pct": None,
            "max_loss_pct": None,
            "avg_loss_pct": None,
            "profit_factor": None,
            "total_pnl_yen_100": 0.0,
            "avg_pnl_yen_100": None,
            "gross_profit_yen_100": 0.0,
            "gross_loss_yen_100": 0.0,
            "profit_factor_yen_100": None,
            "max_win_yen_100": None,
            "max_loss_yen_100": None,
            "exit_reason_counts": {},
        }

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor: float | None = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = None
    else:
        profit_factor = None

    exit_counts: Counter[str] = Counter()
    for t in trades:
        reason = getattr(t, "exit_reason", None)
        if reason is None and isinstance(t, Mapping):
            reason = t.get("exit_reason")
        exit_counts[str(reason or "unknown")] += 1

    yen_block = summarize_pnl_yen_100(trades)

    return {
        "trades": n,
        "win_rate": len(wins) / n if n else None,
        "total_pnl_pct": sum(pnls),
        "avg_pnl_pct": statistics.mean(pnls),
        "median_pnl_pct": statistics.median(pnls),
        "max_loss_pct": min(pnls),
        "avg_loss_pct": statistics.mean(losses) if losses else None,
        "profit_factor": profit_factor,
        **yen_block,
        "exit_reason_counts": dict(exit_counts),
    }


def daily_summaries(trades: Sequence[Any]) -> list[dict[str, Any]]:
    by_day: dict[str, list[Any]] = defaultdict(list)
    for t in trades:
        d = getattr(t, "trade_date", None)
        if d is None and hasattr(t, "to_row"):
            d = ""
        if not d:
            entry = getattr(t, "entry_time", None)
            if entry is not None:
                d = entry.date().isoformat() if hasattr(entry, "date") else str(entry)[:10]
        by_day[str(d or "unknown")].append(t)

    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        block = _summary_block(by_day[day])
        rows.append({"trade_date": day, **block})
    return rows


def symbol_summaries(trades: Sequence[Any]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[Any]] = defaultdict(list)
    for t in trades:
        sym = getattr(t, "symbol", None)
        if sym is None and isinstance(t, Mapping):
            sym = t.get("symbol")
        by_sym[str(sym or "unknown")].append(t)

    rows: list[dict[str, Any]] = []
    for sym in sorted(by_sym):
        block = _summary_block(by_sym[sym])
        rows.append({"symbol": sym, **block})
    return rows


def aggregate_summary(
    trades: Sequence[Any],
    *,
    meta: Mapping[str, Any] | None = None,
    skipped: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    block = _summary_block(trades)
    if meta:
        block = {**dict(meta), **block}
    block["daily"] = daily_summaries(trades)
    block["by_symbol"] = symbol_summaries(trades)
    block["skipped_inputs_count"] = len(skipped or [])
    if skipped:
        block["skipped_inputs"] = list(skipped)
    return block


def flatten_exit_reason_counts(aggregate: Mapping[str, Any]) -> list[dict[str, Any]]:
    counts = aggregate.get("exit_reason_counts") or {}
    rows = []
    for reason, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        rows.append({"exit_reason": reason, "count": count})
    return rows
