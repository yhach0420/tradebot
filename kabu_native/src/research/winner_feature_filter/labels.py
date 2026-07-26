"""Outcome labels for Winner Feature Filter research."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from research.cost_aware_v2.dataset import TradeRow

EARLY_STOP_SEC = 300.0
WINNER_QUANTILE = 0.80  # top 20%


@dataclass
class LabeledTrade:
    trade: TradeRow
    cohort: str  # Winner | STOP | NoProgress | Normal
    is_winner: bool
    is_stop: bool
    is_np: bool
    is_normal: bool
    pnl_yen: float
    winner_threshold: float


def winner_threshold(trades: Sequence[TradeRow], *, q: float = WINNER_QUANTILE) -> float:
    if not trades:
        return 0.0
    pnls = np.array([float(t.pnl_yen) for t in trades], dtype=float)
    return float(np.quantile(pnls, q))


def label_trades(trades: Sequence[TradeRow], *, q: float = WINNER_QUANTILE) -> list[LabeledTrade]:
    thr = winner_threshold(trades, q=q)
    out: list[LabeledTrade] = []
    for t in trades:
        pnl = float(t.pnl_yen)
        is_winner = pnl >= thr
        is_stop = bool(t.is_stop) or (t.exit_reason == "stop_hit") or (
            str(t.exit_reason).startswith("stop") and t.hold_sec <= EARLY_STOP_SEC
        )
        # Early hard-cut: stop within EARLY_STOP_SEC counted as STOP even if reason differs
        if (not is_stop) and t.hold_sec <= EARLY_STOP_SEC and pnl < 0 and "stop" in str(t.exit_reason).lower():
            is_stop = True
        is_np = bool(t.is_np) or t.exit_reason == "no_progress_exit"
        # Priority: Winner > STOP > NoProgress > Normal (mutually exclusive for cohort reporting)
        if is_winner:
            cohort = "Winner"
        elif is_stop:
            cohort = "STOP"
        elif is_np:
            cohort = "NoProgress"
        else:
            cohort = "Normal"
        out.append(
            LabeledTrade(
                trade=t,
                cohort=cohort,
                is_winner=is_winner,
                is_stop=is_stop and not is_winner,
                is_np=is_np and not is_winner and not is_stop,
                is_normal=cohort == "Normal",
                pnl_yen=pnl,
                winner_threshold=thr,
            )
        )
    return out


def cohort_counts(rows: Sequence[LabeledTrade]) -> dict[str, int]:
    c = {"Winner": 0, "STOP": 0, "NoProgress": 0, "Normal": 0}
    for r in rows:
        c[r.cohort] = c.get(r.cohort, 0) + 1
    return c
