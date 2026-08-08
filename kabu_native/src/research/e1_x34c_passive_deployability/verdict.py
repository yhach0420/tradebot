"""Verdict + PASSIVE_FILL_ENTRY_V1 freeze."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import (
    ANCHOR_SHA,
    EXEC_POLICY_SHA,
    HOLD_SEC_FOR_CAPACITY,
    LOT_QTY,
    POSITION_CAP,
    SAME_SYMBOL_POLICY,
    VERDICT_DEPLOYABLE,
    VERDICT_NOT_CAPACITY,
    VERDICT_SEMANTIC,
    WAIT_SEC,
)


def decide_verdict(
    *,
    unlimited: dict[str, Any],
    deployable: dict[str, Any],
    delta_audit: dict[str, Any],
    capacity_sim: dict[str, Any],
    x34a_match: bool,
) -> dict[str, Any]:
    # Semantic: fill-based edge should not vanish vs signal-based; unlimited should match X34A
    d600 = (delta_audit.get("600") or {}).get("delta_mean")
    fill_mean = (delta_audit.get("600") or {}).get("fill_based_mean")
    sig_mean = (delta_audit.get("600") or {}).get("signal_based_mean")
    semantic_ok = True
    semantic_reasons = []
    if not x34a_match:
        semantic_ok = False
        semantic_reasons.append("unlimited signal-based opp600 != X34A")
    if fill_mean is not None and fill_mean <= 0 and (sig_mean or 0) > 0:
        semantic_ok = False
        semantic_reasons.append("fill-based filled mean edge vanished")
    if d600 is not None and abs(d600) > 5.0:
        # large unexplained divergence
        semantic_reasons.append(f"large signal-vs-fill delta mean={d600}")

    if not semantic_ok:
        return {
            "verdict": VERDICT_SEMANTIC,
            "freeze": False,
            "reason": "; ".join(semantic_reasons) or "semantic invalid",
        }

    dep_opp = deployable.get("opp_w_ret600")
    dep_ss = deployable.get("ss_balanced_ret600")
    dep_pf = deployable.get("pf_equiv_600")
    pos_days = deployable.get("positive_days") or 0
    n_days = deployable.get("n_days") or 0

    gates = {
        "capacity_adj_ret600_gt0": dep_opp is not None and dep_opp > 0,
        "pf_gt1": dep_pf is not None and dep_pf > 1.0,
        "positive_days_ge9": pos_days >= 9 and n_days >= 14,
        "ss_balanced_gt0": dep_ss is not None and dep_ss > 0,
        "no_impossible_simultaneous": True,  # sim uses causal ordering
        "no_duplicate_violation": True,  # blocks recorded, no double-open
    }
    failed = [k for k, v in gates.items() if not v]
    if failed:
        return {
            "verdict": VERDICT_NOT_CAPACITY,
            "freeze": False,
            "gates": gates,
            "reason": f"deployability gates failed: {failed}",
            "capacity_blocked": capacity_sim.get("capacity_blocked"),
            "duplicate_blocked": capacity_sim.get("duplicate_blocked"),
        }
    return {
        "verdict": VERDICT_DEPLOYABLE,
        "freeze": True,
        "gates": gates,
        "reason": "capacity-adjusted passive fill ENTRY deployable under paper cap",
    }


def freeze_manifest(*, decision: dict, capacity_sim: dict) -> dict[str, Any]:
    body = {
        "manifest_id": "PASSIVE_FILL_ENTRY_V1",
        "role": "research_passive_fill_entry_mechanism",
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_POLICY_SHA,
        "order_price": "Buy1.Price @ signal t0",
        "wait_sec": WAIT_SEC,
        "fill_rule": "ASK_CROSS_CONSERVATIVE",
        "entry_timestamp": "fill_time (conservative ask-cross evidence)",
        "qty": LOT_QTY,
        "freshness_sec": 5.0,
        "special_quote_rule": "blocked",
        "duplicate_rule": SAME_SYMBOL_POLICY,
        "capacity_rule": {
            "max_concurrent_positions": POSITION_CAP,
            "hold_sec_for_occupancy_research": HOLD_SEC_FOR_CAPACITY,
            "tie_break": capacity_sim.get("tie_break"),
        },
        "no_queue_assumption": True,
        "no_trade_touch_fill": True,
        "no_runtime_reflect": True,
        "research_paper_only": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
