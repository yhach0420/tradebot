"""Verdict + PASSIVE_ORDER_ADMISSION_V1 freeze."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import (
    ANCHOR_SHA,
    ENTRY_SHA,
    EXEC_SHA,
    OCCUPANCY_PROXY_600S,
    ORDER_ASC,
    POSITION_CAP,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_SENSITIVE,
    WAIT_SEC,
)


def decide_verdict(
    *,
    c2: dict[str, Any],
    admission: dict[str, Any],
    lodo: dict[str, Any],
    sensitivity: dict[str, Any],
) -> dict[str, Any]:
    gates = {
        "opp_w_ret600_gt0": (c2.get("opp_w_ret600") or 0) > 0,
        "pf_gt1": c2.get("pf_equiv_600") is not None and c2["pf_equiv_600"] > 1.0,
        "ss_balanced_gt0": (c2.get("ss_balanced_ret600") or 0) > 0,
        "positive_days_ge9": (c2.get("positive_days") or 0) >= 9 and (c2.get("n_days") or 0) >= 14,
        "lodo_majority": bool(lodo.get("majority_positive")),
        "no_severe_symbol_conc": not bool(c2.get("severe_symbol_concentration")),
        "hard_cap_violations_zero": int(admission.get("hard_cap_violations") or 0) == 0,
        "max_open_pending_le_cap": int(admission.get("max_open_plus_pending") or 0) <= POSITION_CAP,
    }
    failed = [k for k, v in gates.items() if not v]
    order_sens = bool(sensitivity.get("CAPACITY_ADMISSION_ORDER_SENSITIVE"))

    if failed:
        return {
            "verdict": VERDICT_FAIL,
            "freeze": False,
            "gates": gates,
            "reason": f"pre-fill gates failed: {failed}",
            "ordering_sensitive": order_sens,
        }
    if order_sens:
        return {
            "verdict": VERDICT_SENSITIVE,
            "freeze": False,
            "gates": gates,
            "reason": "primary gates pass but ASC/DESC/HASH sign instability - allocator needed",
            "ordering_sensitive": True,
        }
    return {
        "verdict": VERDICT_PASS,
        "freeze": True,
        "gates": gates,
        "reason": "pre-fill hard cap preserves positive ENTRY economics",
        "ordering_sensitive": False,
    }


def freeze_admission_manifest(*, admission: dict, decision: dict) -> dict[str, Any]:
    body = {
        "manifest_id": "PASSIVE_ORDER_ADMISSION_V1",
        "max_positions": POSITION_CAP,
        "pending_reserves_slot": True,
        "available_slot_formula": "available = max_positions - open_positions - reserved_pending_slots",
        "deterministic_ordering": ORDER_ASC,
        "ordering_note": "operational baseline; not performance-optimized",
        "pending_expiry_sec": WAIT_SEC,
        "duplicate_semantics": "no_overlap_replace - block if OPEN or PENDING same symbol",
        "occupancy_proxy_sec": OCCUPANCY_PROXY_600S,
        "occupancy_label": "OCCUPANCY_PROXY_600S",
        "no_future_ranking": True,
        "binds_entry_sha": ENTRY_SHA,
        "binds_anchor_sha": ANCHOR_SHA,
        "binds_execution_sha": EXEC_SHA,
        "runtime_reflect": False,
        "research_paper_only": True,
        "hard_cap_violations_in_research": admission.get("hard_cap_violations"),
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
