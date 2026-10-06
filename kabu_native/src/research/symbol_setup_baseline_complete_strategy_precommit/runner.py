"""Deterministic rules for the later development run. This module does not read capture."""
from __future__ import annotations

import hashlib
import json
from typing import Any

RUNNER_ID = "SYMBOL_SETUP_BASELINE_COMPLETE_STRATEGY_REPLAY_V1"
EVENT_ORDER = (
    "FAIL_CLOSE_INVALID_DATA",
    "FINALIZE_COMPLETED_BAR",
    "UPDATE_INDICATORS",
    "EVALUATE_OPEN_POSITION_THESIS",
    "EMIT_THESIS_LOST",
    "EXECUTE_EXIT",
    "SLOT_RELEASE",
    "GENERATE_NEW_ENTRY_SIGNAL",
    "APPLY_SAME_SYMBOL / CAP",
    "CREATE_PENDING_ENTRY",
    "PROCESS_PENDING_FILL",
)
SHARES = 100
EXPLICIT_ADDITIONAL_COST_YEN = 0.0


def thesis_live(ema9: float, ema21: float, ema21_lag3: float) -> bool:
    return float(ema9) > float(ema21) and float(ema21) > float(ema21_lag3)


def loss_reason(ema9: float, ema21: float, ema21_lag3: float) -> str:
    cross = float(ema9) <= float(ema21)
    slope = float(ema21) <= float(ema21_lag3)
    if cross and slope:
        return "BOTH_TREND_COMPONENTS_LOST"
    if cross:
        return "FAST_SLOW_CROSS_LOSS"
    return "SLOW_TREND_SLOPE_LOSS"


def account(entry_px: float, exit_px: float) -> dict[str, float]:
    """Gross 100-share yen. Fees and an extra bps tax are not added."""
    gross = round((float(exit_px) - float(entry_px)) * float(SHARES), 4)
    return {
        "gross_pnl_yen": gross,
        "explicit_additional_cost_yen": float(EXPLICIT_ADDITIONAL_COST_YEN),
        "net_pnl_yen": gross,
    }


def trade_bps(entry_px: float, exit_px: float) -> float:
    return (float(exit_px) / float(entry_px) - 1.0) * 10000.0


def ledger_sha(rows: list[dict[str, Any]]) -> str:
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def profit_factor(nets: list[float]) -> float | None:
    wins = sum(x for x in nets if x > 0.0)
    losses = sum(-x for x in nets if x < 0.0)
    if losses == 0.0:
        return None if wins == 0.0 else float("inf")
    return float(wins) / float(losses)


def max_drawdown_yen(nets_in_exit_order: list[float]) -> float:
    """Most negative peak-to-trough of cumulative net yen. Zero when the curve never falls."""
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for net in nets_in_exit_order:
        equity += float(net)
        if equity > peak:
            peak = equity
        draw = equity - peak
        if draw < worst:
            worst = draw
    return float(worst)
