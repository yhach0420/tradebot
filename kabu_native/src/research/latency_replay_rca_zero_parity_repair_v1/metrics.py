"""Parity gate, then price-only and causal latency splits."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.latency_replay_integrity_holdtime_audit_v1.compare import compare

MISSING = (
    ("6525", "20260722", "PM", "SESSION_FLAT"),
    ("6997", "20260723", "AM", "SESSION_FLAT"),
    ("5074", "20260727", "AM", "SESSION_FLAT"),
    ("6941", "20260731", "PM", "SESSION_FLAT"),
    ("4022", "20260806", "PM", "SESSION_FLAT"),
    ("4461", "20260807", "AM", "SESSION_FLAT"),
    ("6590", "20260810", "PM", "SESSION_FLAT"),
    ("6941", "20260812", "AM", "SESSION_FLAT"),
    ("6273", "20260812", "PM", "IMPULSE_EXHAUSTED"),
    ("2737", "20260828", "AM", "SESSION_FLAT"),
    ("3110", "20260902", "AM", "SESSION_FLAT"),
)


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    yen = np.asarray([float(row["pnl_yen"]) for row in rows], dtype=float)
    if yen.size == 0:
        return None
    profit = float(yen[yen > 0].sum())
    loss = float(yen[yen < 0].sum())
    if loss == 0.0:
        return None if profit == 0.0 else float("inf")
    return profit / abs(loss)


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    yen = np.asarray([float(row["pnl_yen"]) for row in rows], dtype=float)
    bps = np.asarray([float(row["bps"]) for row in rows if row.get("bps") is not None], dtype=float)
    entry = np.asarray([float(row["entry_slip_bps"]) for row in rows if row.get("entry_slip_bps") is not None], dtype=float)
    exit_ = np.asarray([float(row["exit_slip_bps"]) for row in rows if row.get("exit_slip_bps") is not None], dtype=float)
    return {
        "n": int(yen.size),
        "pnl": float(yen.sum()) if yen.size else 0.0,
        "pf": _pf(rows),
        "mean_bps": None if bps.size == 0 else float(bps.mean()),
        "median_bps": None if bps.size == 0 else float(np.median(bps)),
        "entry_slip_bps": None if entry.size == 0 else float(entry.mean()),
        "exit_slip_bps": None if exit_.size == 0 else float(exit_.mean()),
    }


def _holds(rows: list[dict[str, Any]], exit_key: str, entry_key: str) -> np.ndarray:
    out = []
    for row in rows:
        if row.get("pnl_yen") is None or row.get(exit_key) is None or row.get(entry_key) is None:
            continue
        out.append(float(row[exit_key]) - float(row[entry_key]))
    return np.asarray(out, dtype=float)


def _quant(values: np.ndarray) -> dict[str, Any]:
    if values.size == 0:
        return {"n": 0}
    out = {"n": int(values.size), "mean": float(values.mean()), "median": float(np.median(values)), "max": float(values.max())}
    for q in (10, 25, 50, 75, 90, 95, 99):
        out[f"p{q}"] = float(np.quantile(values, q / 100.0))
    return out


def _delays(rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for row in rows:
        if row.get("exit_fill_t") is None or row.get("exit_signal_t") is None:
            continue
        delay = float(row["exit_fill_t"]) - float(row["exit_signal_t"])
        if delay > 1e-6:
            items.append({
                "symbol": row["symbol"],
                "date": row["date"],
                "reason": row.get("reason"),
                "exit_signal_t": row["exit_signal_t"],
                "exit_fill_t": row["exit_fill_t"],
                "delay": delay,
                "class": row.get("deferral_class") or "EXPECTED_NO_EXECUTABLE_BID",
            })
    delays = np.asarray([item["delay"] for item in items], dtype=float)
    edges = (0.0, 0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0)
    counts = {f">{edge if edge else 0}": int(np.sum(delays > edge)) if delays.size else 0 for edge in edges}
    top = sorted(items, key=lambda item: item["delay"], reverse=True)[:20]
    return {"counts": counts, "max": None if delays.size == 0 else float(delays.max()), "top": top, "n": int(delays.size)}


def build(scanned: dict[str, Any]) -> dict[str, Any]:
    decision = compare(scanned)
    base_hold = np.sort(_holds(scanned["baseline"], "exit_t", "entry_t"))
    zero_hold = np.sort(_holds(scanned["zero"], "exit_fill_t", "entry_fill_t"))
    hold_parity = bool(base_hold.size == zero_hold.size == 11902 and np.max(np.abs(base_hold - zero_hold)) <= 1e-4)
    decision["hold_parity"] = hold_parity
    decision["baseline_execution_quant"] = _quant(base_hold)
    decision["zero_execution_quant"] = _quant(zero_hold)
    decision["exit_delays"] = _delays([row for row in scanned["zero"] if row.get("pnl_yen") is not None])
    decision["repair"] = {
        "applied": True,
        "session_flat": "latency 0 uses frozen _last_bid, the last executable bid inside the session",
        "thesis_without_bid": "a thesis exit with no bid at or after the signal keeps that reason and closes with _last_bid",
        "latency_above_zero": "a target past the session boundary is not filled outside the cash session",
        "frozen_v2_mutated": False,
    }
    decision["missing_rca"] = [
        {"symbol": sym, "date": day, "session": session, "baseline_reason": reason, "root_cause": "SESSION_BOUNDARY_LAST_BID" if reason == "SESSION_FLAT" else "THESIS_WITHOUT_LATER_BID_THEN_LAST_BID"}
        for sym, day, session, reason in MISSING
    ]
    if not decision["trade_parity"] or not hold_parity:
        decision["verdict"] = "LATENCY_REPLAY_ZERO_PARITY_STILL_FAIL_V1"
        decision["next"] = "RCA_NEXT_FIRST_DIVERGENCE"
        decision["later_latency_interpreted"] = False
        decision["strategy_verdict_valid"] = False
        return decision
    fixed = {}
    for ms, modes in scanned["fixed"].items():
        fixed[str(ms)] = {mode: _econ(rows) for mode, rows in modes.items()}
    causal = {}
    for ms, rows in scanned["causal"].items():
        priced = [row for row in rows if row.get("pnl_yen") is not None]
        yen = np.asarray([float(row["pnl_yen"]) for row in priced], dtype=float)
        causal[str(ms)] = {
            "trade_n": int(yen.size),
            "pnl": float(yen.sum()) if yen.size else 0.0,
            "pf": _pf(priced),
            "execution_hold": _quant(_holds(priced, "exit_fill_t", "entry_fill_t")),
            "counts": scanned["causal_counts"][ms],
        }
    both100 = fixed["100"]["both"]
    full100 = causal["100"]
    price_decay = both100["pnl"] < 0
    state_attrition = full100["pnl"] < 0 and both100["pnl"] >= 0
    both_decay = both100["pnl"] < 0 and full100["pnl"] < 0
    if both_decay:
        label = "LATENCY_PRICE_AND_STATE_MACHINE_DECAY"
    elif state_attrition:
        label = "LATENCY_STATE_MACHINE_ATTRITION"
    elif price_decay:
        label = "LATENCY_PRICE_DECAY"
    else:
        label = "LATENCY_REPLAY_PARITY_REPAIRED_NO_100MS_DECAY"
    decision["verdict"] = label if decision["trade_parity"] else decision["verdict"]
    decision["next"] = "STOP"
    decision["fixed"] = fixed
    decision["causal"] = causal
    decision["price_decay_100"] = bool(price_decay or both_decay)
    decision["state_attrition_100"] = bool(state_attrition or both_decay)
    decision["later_latency_interpreted"] = True
    decision["strategy_verdict_valid"] = False
    decision["previous_zero_latency_edge_only_valid"] = False
    return decision
