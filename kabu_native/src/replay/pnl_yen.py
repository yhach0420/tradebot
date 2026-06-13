"""
100-share yen PnL helpers for replay / expectancy evaluation (no ENTRY/EXIT wiring).
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trade_side(trade: Any) -> str:
    side = getattr(trade, "side", None)
    if side is None and isinstance(trade, Mapping):
        side = trade.get("side")
    return str(side or "long").strip().lower()


def compute_pnl_yen_100(
    entry_price: float,
    exit_price: float,
    *,
    side: str = "long",
) -> float:
    """Gross yen PnL for 100 shares (fees/tax excluded)."""
    diff = (float(exit_price) - float(entry_price)) * 100.0
    if _trade_side({"side": side}) in ("short", "sell"):
        return -diff
    return diff


def trade_pnl_yen_100(trade: Any) -> Optional[float]:
    existing = getattr(trade, "pnl_yen_100", None)
    if existing is None and isinstance(trade, Mapping):
        existing = trade.get("pnl_yen_100")
    if existing is not None:
        return float(existing)

    entry = getattr(trade, "entry_price", None)
    exit_p = getattr(trade, "exit_price", None)
    if isinstance(trade, Mapping):
        entry = entry if entry is not None else trade.get("entry_price")
        exit_p = exit_p if exit_p is not None else trade.get("exit_price")

    entry_f = _as_float(entry)
    exit_f = _as_float(exit_p)
    if entry_f is None or exit_f is None:
        return None
    return compute_pnl_yen_100(entry_f, exit_f, side=_trade_side(trade))


def format_pnl_yen_100_display(yen: float) -> str:
    rounded = int(round(float(yen)))
    if rounded >= 0:
        return f"+{rounded:,}円(100株)"
    return f"{rounded:,}円(100株)"


def format_summary_avg_pnl_yen_100(avg_yen: Optional[float]) -> Optional[str]:
    if avg_yen is None:
        return None
    rounded = int(round(float(avg_yen)))
    return f"{rounded:,}円/取引(100株)"


def format_summary_profit_factor_yen(pf: Optional[float] | str) -> str:
    if pf is None:
        return "—"
    if str(pf).lower() == "inf":
        return "inf"
    return f"{float(pf):.3f}"


def format_summary_total_pnl_line(
    total_pnl_pct: float,
    total_pnl_yen_100: Optional[float] = None,
) -> str:
    sign = "+" if float(total_pnl_pct) >= 0 else ""
    pct_part = f"{sign}{float(total_pnl_pct):.2f}%"
    if total_pnl_yen_100 is None:
        return f"最終損益: {pct_part}"
    return f"最終損益: {pct_part} / {format_pnl_yen_100_display(total_pnl_yen_100)}"


def format_exit_pnl_line(pnl_pct: float, pnl_yen_100: Optional[float] = None) -> str:
    sign = "+" if float(pnl_pct) >= 0 else ""
    pct_part = f"{sign}{float(pnl_pct):.2f}%"
    if pnl_yen_100 is None:
        return f"損益: {pct_part}"
    return f"損益: {pct_part} / {format_pnl_yen_100_display(pnl_yen_100)}"


def resolve_pnl_yen_100(
    *,
    entry_price: Any,
    exit_price: Any,
    side: str = "long",
    pnl_yen_100: Any = None,
) -> Optional[float]:
    explicit = _as_float(pnl_yen_100)
    if explicit is not None:
        return explicit
    entry_f = _as_float(entry_price)
    exit_f = _as_float(exit_price)
    if entry_f is None or exit_f is None or entry_f <= 0:
        return None
    return compute_pnl_yen_100(entry_f, exit_f, side=side)


def enrich_trade_pnl_yen(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    yen = trade_pnl_yen_100(out)
    if yen is not None:
        out["pnl_yen_100"] = round(yen, 2)
    return out


def summarize_pnl_yen_100(trades: Sequence[Any]) -> dict[str, Any]:
    values = [v for v in (trade_pnl_yen_100(t) for t in trades) if v is not None]
    if not values:
        return {
            "total_pnl_yen_100": 0.0,
            "avg_pnl_yen_100": None,
            "gross_profit_yen_100": 0.0,
            "gross_loss_yen_100": 0.0,
            "profit_factor_yen_100": None,
            "max_win_yen_100": None,
            "max_loss_yen_100": None,
        }

    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor: float | str | None = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = "inf"
    else:
        profit_factor = None

    pf_out: float | str | None
    if isinstance(profit_factor, str):
        pf_out = profit_factor
    elif profit_factor is not None:
        pf_out = round(profit_factor, 4)
    else:
        pf_out = None

    return {
        "total_pnl_yen_100": round(sum(values), 2),
        "avg_pnl_yen_100": round(statistics.mean(values), 2),
        "gross_profit_yen_100": round(gross_profit, 2),
        "gross_loss_yen_100": round(gross_loss, 2),
        "profit_factor_yen_100": pf_out,
        "max_win_yen_100": round(max(values), 2),
        "max_loss_yen_100": round(min(values), 2),
    }
