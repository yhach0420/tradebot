"""Execution replay. Refuses to invent a latency distribution."""

from __future__ import annotations


def run_observed_latency_replay(latency_samples_ms: list[float]) -> dict:
    if not latency_samples_ms:
        return {
            "status": "NOT_COMPLETED",
            "reason": "NO_V12_OBSERVED_LATENCY_DISTRIBUTION",
            "control": "CURRENT_SAME_EVENT_ASK1_X1",
            "treatment": "OBSERVED_LATENCY_EXECUTION",
            "trade_n": None,
            "fill_n": None,
            "partial_fill_n": None,
            "unfilled_n": None,
            "fill_rate": None,
            "additional_execution_bps": None,
            "pnl_yen": None,
            "pf": None,
            "gross_profit": None,
            "gross_loss": None,
            "max_dd": None,
            "avg_trade": None,
            "median_trade": None,
            "note": "Baseline PnL was not haircut. No path was replayed.",
        }
    raise RuntimeError("OBSERVED_LATENCY_REPLAY_NOT_WIRED_TO_STRATEGY")
