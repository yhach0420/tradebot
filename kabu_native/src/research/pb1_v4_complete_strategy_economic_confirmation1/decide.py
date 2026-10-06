"""Primary economic gate. Invariant failure blocks the economic verdict."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_complete_strategy_economic_confirmation1 import (
    CASE_FAIL,
    CASE_INVALID,
    CASE_PASS,
    NEXT_CONF2,
    NEXT_DECOMP,
    NEXT_STOP,
)
from research.pb1_v4_complete_strategy_economic_confirmation1.metrics import failure_decomposition


def decide(*, invariants: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    if not invariants.get("ok"):
        return {
            "VERDICT": CASE_INVALID,
            "NEXT": NEXT_STOP,
            "ECONOMIC_EDGE_CONCENTRATED": dict(metrics.get("concentration") or {}).get("ECONOMIC_EDGE_CONCENTRATED"),
            "primary_gate": metrics.get("primary_gate"),
            "invariants_ok": False,
            "economic_verdict_issued": False,
            "reason": invariants.get("reason") or "invalid_replay",
        }
    gate = dict(metrics.get("primary_gate") or {})
    conc = dict(metrics.get("concentration") or {})
    concentrated = bool(conc.get("ECONOMIC_EDGE_CONCENTRATED"))
    passed = bool(gate.get("net_pnl_yen_gt_0")) and bool(gate.get("profit_factor_gt_1")) and bool(gate.get("mean_net_pnl_per_trade_gt_0"))
    if passed:
        return {
            "VERDICT": CASE_PASS,
            "NEXT": NEXT_CONF2,
            "ECONOMIC_EDGE_CONCENTRATED": concentrated,
            "primary_gate": gate,
            "invariants_ok": True,
            "economic_verdict_issued": True,
            "reason": None,
        }
    return {
        "VERDICT": CASE_FAIL,
        "NEXT": NEXT_DECOMP,
        "ECONOMIC_EDGE_CONCENTRATED": concentrated,
        "primary_gate": gate,
        "invariants_ok": True,
        "economic_verdict_issued": True,
        "reason": "primary_economic_gate_failed",
        "decomposition": failure_decomposition(metrics),
        "do_not_retune_entry": True,
        "do_not_open_frozen_validation_economic": True,
    }
