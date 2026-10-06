"""Causal execution-shadow checks. No V12 paper session is attached."""

from __future__ import annotations


def build_shadow_observation(
    *,
    signal_uid: str,
    symbol: str,
    signal_t: float,
    signal_ask1: float,
    signal_ask1_qty: int,
    signal_bid1: float,
    decision_complete_t: float,
    hypothetical_submit_ready_t: float,
    board_events: list[dict],
) -> dict:
    """Use only board events at or before hypothetical_submit_ready_t."""
    if hypothetical_submit_ready_t < signal_t:
        raise ValueError("submit_ready before signal")
    causal = [row for row in board_events if float(row["t"]) <= float(hypothetical_submit_ready_t)]
    if not causal:
        raise ValueError("no causal board")
    latest = max(causal, key=lambda row: float(row["t"]))
    ready_ask = float(latest["ask1"])
    ready_bid = float(latest["bid1"])
    drift = ready_ask - float(signal_ask1)
    mid_signal = (float(signal_ask1) + float(signal_bid1)) / 2.0
    bps = (drift / float(signal_ask1) * 10000.0) if signal_ask1 else 0.0
    return {
        "signal_uid": signal_uid,
        "symbol": symbol,
        "signal_t": float(signal_t),
        "signal_ask1": float(signal_ask1),
        "signal_ask1_qty": int(signal_ask1_qty),
        "signal_bid1": float(signal_bid1),
        "decision_complete_t": float(decision_complete_t),
        "hypothetical_submit_ready_t": float(hypothetical_submit_ready_t),
        "submit_ready_bid1": ready_bid,
        "submit_ready_ask1": ready_ask,
        "submit_ready_ask1_qty": int(latest["ask1_qty"]),
        "signal_to_ready_ms": (float(hypothetical_submit_ready_t) - float(signal_t)) * 1000.0,
        "ask_drift_yen": drift,
        "ask_drift_bps": bps,
        "spread_at_signal": float(signal_ask1) - float(signal_bid1),
        "spread_at_ready": ready_ask - ready_bid,
        "mid_at_signal": mid_signal,
        "future_events_used": 0,
    }


def v12_observed_shadow() -> dict:
    return {
        "status": "NOT_COLLECTED",
        "reason": "V12_FULL_DAY_PAPER_NOT_RUN",
        "n": 0,
        "latency_ms": [],
        "ask_drift_yen": [],
        "ask_drift_bps": [],
    }
