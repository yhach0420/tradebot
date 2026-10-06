"""Nominal latency versus the fill time the replay actually uses."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.latency_replay_integrity_holdtime_audit_v1.compare import compare


def _pf(yen: np.ndarray) -> Optional[float]:
    if yen.size == 0:
        return None
    profit = float(yen[yen > 0].sum())
    loss = float(yen[yen < 0].sum())
    if loss == 0.0:
        return None if profit == 0.0 else float("inf")
    return profit / abs(loss)


def _quant(values: np.ndarray) -> dict[str, Any]:
    if values.size == 0:
        return {"n": 0}
    out = {"n": int(values.size), "mean": float(values.mean()), "median": float(np.median(values)), "max": float(values.max())}
    for q in (50, 75, 90, 95, 99):
        out[f"p{q}"] = float(np.quantile(values, q / 100.0))
    return out


def _delay_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wait = np.asarray([float(row["fill_t"]) - float(row["target_t"]) for row in rows], dtype=float)
    actual = np.asarray([float(row["fill_t"]) - float(row["signal_t"]) for row in rows], dtype=float)
    slip = np.asarray([float(row["entry_slip_bps"]) for row in rows], dtype=float)
    yen = np.asarray([float(row["pnl_yen"]) for row in rows], dtype=float)
    bps = np.asarray([float(row["bps"]) for row in rows], dtype=float)
    fractions = {}
    for label, limit in (("1ms", 0.001), ("5ms", 0.005), ("10ms", 0.010), ("25ms", 0.025), ("50ms", 0.050), ("100ms", 0.100)):
        fractions[f"fill_within_{label}"] = None if wait.size == 0 else float(np.mean(wait <= limit + 1e-12))
    for label, limit in (("100ms", 0.100), ("500ms", 0.500), ("1s", 1.0)):
        fractions[f"fill_after_{label}"] = None if wait.size == 0 else float(np.mean(wait > limit))
    return {
        "n": int(yen.size),
        "target_wait": _quant(wait),
        "actual_signal_to_fill": _quant(actual),
        "fractions": fractions,
        "pnl": float(yen.sum()) if yen.size else 0.0,
        "pf": _pf(yen),
        "mean_bps": None if bps.size == 0 else float(bps.mean()),
        "median_bps": None if bps.size == 0 else float(np.median(bps)),
        "entry_slip_mean": None if slip.size == 0 else float(slip.mean()),
        "entry_slip_median": None if slip.size == 0 else float(np.median(slip)),
    }


def _causal(rows: list[dict[str, Any]], counts: dict[str, int]) -> dict[str, Any]:
    priced = [row for row in rows if row.get("pnl_yen") is not None and row.get("entry_fill_t") is not None]
    yen = np.asarray([float(row["pnl_yen"]) for row in priced], dtype=float)
    hold = np.asarray([float(row["exit_fill_t"]) - float(row["entry_fill_t"]) for row in priced if row.get("exit_fill_t") is not None], dtype=float)
    pending = np.asarray([float(row["entry_fill_t"]) - float(row["entry_signal_t"]) for row in priced if row.get("entry_signal_t") is not None], dtype=float)
    return {
        "trade_n": int(yen.size),
        "pnl": float(yen.sum()) if yen.size else 0.0,
        "pf": _pf(yen),
        "execution_hold": _quant(hold),
        "pending": _quant(pending),
        "pending_over": {str(limit): int(np.sum(pending > limit)) if pending.size else 0 for limit in (0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0)},
        "counts": counts,
    }


def build(scanned: dict[str, Any]) -> dict[str, Any]:
    decision = compare(scanned)
    fixed = {model: {str(ms): _delay_block(rows) for ms, rows in by_ms.items()} for model, by_ms in scanned["fixed"].items()}
    causal = {
        model: {str(ms): _causal(scanned["causal"][model][ms], scanned["causal_counts"][model][ms]) for ms in scanned["causal"][model]}
        for model in scanned["causal"]
    }
    forced = 0
    for row in scanned["zero"]:
        if row.get("exit_fill_t") is not None and row.get("exit_signal_t") is not None and float(row["exit_fill_t"]) + 1e-4 < float(row["exit_signal_t"]):
            forced += 1
    next100 = fixed["next"]["100"]
    asof100 = fixed["asof"]["100"]
    next10 = fixed["next"]["10"]
    asof10 = fixed["asof"]["10"]
    signs_differ = (next100["pnl"] < 0) != (asof100["pnl"] < 0)
    gap = abs(next100["pnl"] - asof100["pnl"]) / max(abs(next100["pnl"]), 1.0)
    material = bool(signs_differ or gap > 0.25)
    next_loses = next100["pnl"] < 0
    asof_profits = asof100["pnl"] > 0
    wait_p50 = (next10["target_wait"] or {}).get("p50")
    close_to_nominal = wait_p50 is not None and float(wait_p50) <= 0.010
    if next_loses and asof_profits:
        verdict = "NEXT_EVENT_LATENCY_MODEL_OVERSTATED_DECAY_V1"
    elif material:
        verdict = "LATENCY_EXECUTION_SEMANTICS_MATERIALLY_AFFECT_RESULT_V1"
    elif next_loses and asof100["pnl"] < 0 and close_to_nominal:
        verdict = "EVENT_TIME_ENTRY_PRICE_DECAY_CONFIRMED_V1"
    else:
        verdict = "LATENCY_EXECUTION_SEMANTICS_MATERIALLY_AFFECT_RESULT_V1"
    if not decision["trade_parity"]:
        verdict = "LATENCY_REPLAY_ZERO_PARITY_STILL_FAIL_V1"
        nxt = "RCA_NEXT_FIRST_DIVERGENCE"
    else:
        nxt = "STOP"
    decision["verdict"] = verdict
    decision["next"] = nxt
    decision["fill_semantic"] = "NEXT_EVENT"
    decision["fill_function"] = "research.event_time_impulse_v2_robustness_audit.latency._at"
    decision["capture_state_persistence"] = True
    decision["asof_reference"] = "research.simple_tech_entry_family.harvest._snap_at"
    decision["fixed"] = fixed
    decision["causal_models"] = causal
    decision["forced_last_bid_close_n"] = forced
    decision["previous_decay_final"] = False
    return decision
