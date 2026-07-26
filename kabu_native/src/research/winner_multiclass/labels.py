"""4-class labels for Winner Multiclass research.

Priority (mutually exclusive, no overlap):
  STOP > NoProgress > Winner > Normal
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np

from research.cost_aware_v2.dataset import TradeRow

EARLY_STOP_SEC = 300.0
WINNER_QUANTILE = 0.80
CLASS_ORDER = ("Winner", "STOP", "NoProgress", "Normal")
CLASS_TO_ID = {c: i for i, c in enumerate(CLASS_ORDER)}
# Priority for assignment
PRIORITY = ("STOP", "NoProgress", "Winner", "Normal")


@dataclass
class MulticlassRow:
    trade: TradeRow
    class_label: str
    class_reason: str
    exit_reason: str
    pnl_yen_100: float
    pnl_5bps: float
    holding_sec: float
    mfe: Optional[float]
    mae: Optional[float]
    winner_threshold: float
    is_top20_winner: bool  # raw flag before priority
    raw_stop: bool
    raw_np: bool


def winner_threshold(trades: Sequence[TradeRow], *, q: float = WINNER_QUANTILE) -> float:
    if not trades:
        return 0.0
    return float(np.quantile([float(t.pnl_yen) for t in trades], q))


def _is_stop(t: TradeRow) -> tuple[bool, str]:
    er = str(t.exit_reason or "")
    if t.is_stop or er == "stop_hit":
        return True, "exit_reason_stop_hit"
    if er.startswith("stop"):
        return True, f"exit_reason_{er}"
    if t.hold_sec <= EARLY_STOP_SEC and float(t.pnl_yen) < 0 and "stop" in er.lower():
        return True, "early_stop_negative_pnl"
    if t.hold_sec <= EARLY_STOP_SEC and float(t.pnl_yen) < 0 and er in (
        "structural_stop",
        "hard_stop",
        "loss_cut",
    ):
        return True, f"early_loss_cut:{er}"
    return False, ""


def _is_np(t: TradeRow) -> tuple[bool, str]:
    er = str(t.exit_reason or "").lower()
    if t.is_np or t.exit_reason == "no_progress_exit":
        return True, "no_progress_exit"
    if "no_progress" in er or "stagnation" in er or "stagnant" in er:
        return True, f"stagnation:{t.exit_reason}"
    return False, ""


def label_multiclass(
    trades: Sequence[TradeRow],
    *,
    q: float = WINNER_QUANTILE,
    winner_thr: Optional[float] = None,
) -> list[MulticlassRow]:
    thr = float(winner_thr) if winner_thr is not None else winner_threshold(trades, q=q)
    out: list[MulticlassRow] = []
    for t in trades:
        pnl = float(t.pnl_yen)
        raw_w = pnl >= thr
        raw_s, s_reason = _is_stop(t)
        raw_n, n_reason = _is_np(t)
        # Priority: STOP > NoProgress > Winner > Normal
        if raw_s:
            label, reason = "STOP", s_reason or "STOP"
        elif raw_n:
            label, reason = "NoProgress", n_reason or "NoProgress"
        elif raw_w:
            label, reason = "Winner", f"top20_pnl>={thr:.4f}"
        else:
            label, reason = "Normal", "residual"
        mfe = t.features.get("f_rolling_mfe")
        mae = t.features.get("f_rolling_mae")
        out.append(
            MulticlassRow(
                trade=t,
                class_label=label,
                class_reason=reason,
                exit_reason=str(t.exit_reason or ""),
                pnl_yen_100=pnl,
                pnl_5bps=float(t.pnl_5bps),
                holding_sec=float(t.hold_sec),
                mfe=float(mfe) if mfe is not None else None,
                mae=float(mae) if mae is not None else None,
                winner_threshold=thr,
                is_top20_winner=raw_w,
                raw_stop=raw_s,
                raw_np=raw_n,
            )
        )
    return out


def class_counts(rows: Sequence[MulticlassRow]) -> dict[str, int]:
    c = {k: 0 for k in CLASS_ORDER}
    for r in rows:
        c[r.class_label] = c.get(r.class_label, 0) + 1
    return c


def y_ids(rows: Sequence[MulticlassRow]) -> np.ndarray:
    return np.array([CLASS_TO_ID[r.class_label] for r in rows], dtype=int)
