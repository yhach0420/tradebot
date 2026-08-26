"""Write and freeze P4-1 precommit.json. SHA is the rule identity. No retune after SHA."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research.mid_hold_gate_p4_1 import (
    CANDIDATE_ID,
    CHECKPOINTS_SEC,
    DECISION_HORIZON_SEC,
    EXIT_REASON,
    GUARD_MONITOR_TO,
    TASK_LABEL,
)
from small_paper.v1r_exit_v2_contract import FROZEN_CONTINUATION, FROZEN_GUARD

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "mid_hold_gate_p4_1"
JST = timezone(timedelta(hours=9))


def contract_body() -> dict[str, Any]:
    return {
        "candidate_id": CANDIDATE_ID,
        "LABEL": TASK_LABEL,
        "NOT": ["OOS", "prospective", "robust", "strategy validation", "production approval"],
        "rule": {
            "id": CANDIDATE_ID,
            "meaning": (
                "After Early Guard has finished, a trade that has never printed an "
                "executable Bid above fill, and is still below fill at the evaluation "
                "event, is a Mid-Hold failure candidate."
            ),
            "current_bid_return": "Bid_now / fill_price - 1",
            "executable_MFE_since_fill": (
                "max valid Bid1 from fill through evaluation event / fill_price - 1"
            ),
            "gate_true": "current_bid_return < 0 AND executable_MFE_since_fill <= 0",
            "bid_source": "causal valid Bid1 only; existing Dual Lane freshness/validity",
            "fill_window": "fill through evaluation event inclusive; no future quote",
            "no_mae": True,
            "no_giveback": True,
            "no_last60": True,
            "no_imbalance": True,
            "no_symbol": True,
            "no_anchor": True,
            "no_top3_day": True,
            "no_time_dependent_threshold": True,
            "no_percentage_optimization": True,
            "candidates": 1,
        },
        "evaluation_checkpoints_sec": list(CHECKPOINTS_SEC),
        "evaluation_cadence": {
            "not_every_tick": True,
            "runtime_evaluation_event": (
                "first causal Dual Lane 0.5s on_tick at or after each checkpoint"
            ),
            "max_evaluations_per_checkpoint": 1,
            "time_is_trigger_not_exit_reason": True,
            "state_is_exit_reason": EXIT_REASON,
        },
        "state_semantics": {
            "current_bid": "last valid causal Bid1 with event_time <= evaluation event",
            "mfe": "max valid Bid1 fill→evaluation event",
            "validity": "existing Dual Lane Buy1: not special, qty>=MIN_BUY1_QTY, fresh<=BOARD_FRESH_SEC",
            "future_nearest": False,
        },
        "exit_semantics": {
            "reason": EXIT_REASON,
            "first_trigger": "first checkpoint among the 8 where gate_true; no further evaluation",
            "execution": "FIRST_VALID_EXECUTABLE_BUY1_AT_OR_AFTER_TRIGGER",
            "same_as": "existing Runtime Long EXIT executable Buy1",
            "do_not_pick_convenient_past_quote": True,
            "future_nearest": False,
            "once_armed_do_not_recheck_state": True,
        },
        "runtime_precedence": {
            "early_guard_sot": "existing Dual Lane / Arch E Early Guard lifecycle",
            "early_guard_id": FROZEN_GUARD.get("id"),
            "early_guard_monitor_to": GUARD_MONITOR_TO,
            "mid_hold_active_only_if": (
                "Early Guard finished on existing semantics AND position still OPEN"
            ),
            "no_compete_at_120_boundary": True,
            "early_guard_rule_unchanged": True,
            "mid_hold_active_before_600_decision_only": True,
            "same_event_as_600_decision": "existing 600_DECISION takes precedence; Mid-Hold not evaluated",
            "600_decision_unchanged": True,
            "extension_unchanged": True,
            "horizon_750_unchanged": True,
            "entry_unchanged": True,
            "clock_unchanged": True,
            "score_unchanged": True,
            "control_lane_unchanged": True,
        },
        "structural_boundary": {
            "current_bid_return_lt_0": True,
            "executable_MFE_since_fill_le_0": True,
            "HISTORICAL_CUTOFF_SEARCHED": False,
            "STRUCTURAL_ZERO_BOUNDARY_PRECOMMITTED": True,
            "note": "0 is a structural fill/progress boundary, not a searched cutoff.",
        },
        "frozen_existing": {
            "guard": dict(FROZEN_GUARD),
            "continuation": dict(FROZEN_CONTINUATION),
            "decision_horizon_sec": DECISION_HORIZON_SEC,
        },
        "falsification_not_validation": True,
        "runtime_adopt": False,
    }


def contract_sha(body: dict[str, Any]) -> str:
    blob = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_precommit() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    body = contract_body()
    sha = contract_sha(body)
    doc = {
        **body,
        "SHA": sha,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "note": "SHA covers contract_body only. Rule must not change after this SHA exists.",
    }
    path = OUT / "precommit.json"
    if path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("SHA") != sha:
            raise RuntimeError(
                f"P4_1_PRECOMMIT_SHA_DRIFT disk={old.get('SHA')} now={sha}. "
                "Rule change after SHA is forbidden."
            )
        return old
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def load_precommit() -> dict[str, Any]:
    path = OUT / "precommit.json"
    if not path.is_file():
        raise RuntimeError("P4_1_PRECOMMIT_MISSING")
    doc = json.loads(path.read_text(encoding="utf-8"))
    body = {k: v for k, v in doc.items() if k not in ("SHA", "generated_at", "note")}
    sha = contract_sha(body)
    if sha != doc.get("SHA"):
        raise RuntimeError("P4_1_PRECOMMIT_SHA_MISMATCH")
    return doc
