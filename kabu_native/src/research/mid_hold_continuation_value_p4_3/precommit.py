"""Write and freeze P4-3 precommit.json. SHA is the diagnostic identity. No change after SHA."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research.mid_hold_continuation_value_p4_3 import (
    CLASS_DATA,
    CLASS_MIXED,
    CLASS_NEG,
    CLASS_NOT_ACTIONABLE,
    CLASS_NOT_HARM,
    DOCUMENT_ID,
    EXIT_CHECKPOINTS_SEC,
    FALSE_RECOVERY_KNOWN_ID,
    FLAT_YEN,
    P4_1_WINNER_IDS,
    P4_2_ADVERSE_DRAW,
    P4_2_ADVERSE_LOSS,
    P4_2_ADVERSE_N,
    P4_2_ADVERSE_WIN,
    PRIMARY_CHECKPOINTS,
    SECONDARY_CHECKPOINTS,
    STATE_CHECKPOINTS_SEC,
    STATE_NON_RECOVERING,
    STATE_RECOVERING,
    TASK_LABEL,
)
from small_paper.v1r_live_dual_lane import BOARD_FRESH_SEC, MIN_BUY1_QTY

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "mid_hold_continuation_value_p4_3"
JST = timezone(timedelta(hours=9))


def contract_body() -> dict[str, Any]:
    return {
        "task": "P4-3",
        "ANALYSIS_ID": "P4_3_MID_HOLD_CONTINUATION_VALUE",
        "DOCUMENT_ID": DOCUMENT_ID,
        "LABEL": TASK_LABEL,
        "NOT": [
            "OOS",
            "prospective",
            "robust",
            "strategy validation",
            "new EXIT validation",
            "production approval",
        ],
        "cohort": {
            "id": "P4_2_PRIMARY_ADVERSE120",
            "definition": "P4-0 eligible 600-survivor AND bid_return_from_fill at 120s < 0",
            "same_as": "P4-2 PRIMARY_ADVERSE120",
            "n": P4_2_ADVERSE_N,
            "WIN": P4_2_ADVERSE_WIN,
            "LOSS": P4_2_ADVERSE_LOSS,
            "DRAW": P4_2_ADVERSE_DRAW,
            "new_cohort_forbidden": True,
        },
        "checkpoints": {
            "state_sec": list(STATE_CHECKPOINTS_SEC),
            "exit_sec": list(EXIT_CHECKPOINTS_SEC),
            "primary_sec": list(PRIMARY_CHECKPOINTS),
            "secondary_sec": list(SECONDARY_CHECKPOINTS),
            "checkpoint_addition_forbidden": True,
            "best_checkpoint_selection_forbidden": True,
        },
        "state_definitions": {
            "descriptor": "delta_bid_120_to_t = bid_return_t - bid_return_120",
            "bid_return": "Bid / fill_price - 1 from P4-0/P4-2 checkpoint_state",
            "RECOVERING_STATE": "delta_bid_120_to_t > 0",
            "NON_RECOVERING_STATE": "delta_bid_120_to_t <= 0",
            "zero_meaning": "structural equality with 120s bid_return, not a searched percent cutoff",
            "causal": "event_time <= checkpoint evaluation time only; future-nearest forbidden",
            "future_win_loss_not_in_state": True,
            "no_mae_mfe_giveback_board_compound": True,
            "no_persistence_count": True,
            "no_percentage_threshold": True,
            "HISTORICAL_PERCENT_CUTOFF_SEARCHED": False,
            "STRUCTURAL_ZERO_BOUNDARY_PRECOMMITTED": True,
        },
        "execution_semantics": {
            "kind": "LOCAL_COUNTERFACTUAL",
            "not": "FULL_STATE_MACHINE_REPLAY",
            "trigger_time": "fill_time + checkpoint_sec (decision time; not last-known Bid before checkpoint)",
            "execution": "FIRST_VALID_EXECUTABLE_BUY1_AT_OR_AFTER_TRIGGER",
            "same_as": "existing Runtime Long EXIT executable Buy1 validity",
            "validity": {
                "not_special": True,
                "qty_ge": MIN_BUY1_QTY,
                "fresh_le_sec": BOARD_FRESH_SEC,
            },
            "board_source": "P3REngine full_bufs (same as P4-0/P4-2)",
            "do_not_use_convenient_past_bid": True,
            "do_not_use_price_after_canonical_exit": True,
            "t_until": "min(canonical_exit_time, session_end)",
            "no_fixed_exec_window_sec": True,
            "special_skips_wait_next": True,
            "future_nearest": False,
        },
        "continuation_value_definition": {
            "checkpoint_exit_pnl_yen_100": "(execution_bid - fill_price) * 100",
            "canonical_final_pnl_yen_100": "frozen canonical trade pnl_yen_100",
            "continuation_value_yen_100": (
                "canonical_final_pnl_yen_100 - checkpoint_exit_pnl_yen_100"
            ),
            "gt_0": "waiting until existing EXIT was better",
            "lt_0": "hypothetical checkpoint EXIT was better",
            "flat_yen_band": FLAT_YEN,
            "flat_meaning": "abs(continuation_value) < 0.51 is ledger rounding band, not a searched cutoff",
        },
        "raw_market_continuation": {
            "checkpoint_to_600_bid_return": (
                "600_DECISION first-valid Buy1 at or after fill+600, "
                "not after canonical exit, divided by checkpoint execution_bid, minus 1"
            ),
            "checkpoint_to_canonical_exit_bid_return": (
                "canonical_exit_price / checkpoint execution_bid - 1"
            ),
            "possible_rows_only": True,
        },
        "primary_comparisons": [
            "RECOVERING_STATE vs NON_RECOVERING_STATE continuation_value at each checkpoint",
            "NON_RECOVERING actionability: wait vs EXIT NOW",
            "RECOVERING continuation_value (does recovery have wait value)",
            "EXIT_AT_600 vs EXTEND_TO_750 stratification",
            "FINAL_WIN / FINAL_LOSS stratification (labels only, not state inputs)",
            "REST11 vs ALL",
            "day-level median sign for NON_RECOVERING",
            "leave-one-day-out descriptive continuation stats",
        ],
        "p4_1_winners": list(P4_1_WINNER_IDS),
        "false_recovery_known_case": FALSE_RECOVERY_KNOWN_ID,
        "forbidden": {
            "new_gate": True,
            "best_checkpoint": True,
            "percentage_threshold_search": True,
            "compound_score": True,
            "persistence_n": True,
            "portfolio_slot_release_replay": True,
            "runtime_change": True,
            "p4_1_v1_retune": True,
        },
        "verdict_criteria": {
            "primary_checkpoints": list(PRIMARY_CHECKPOINTS),
            "A": {
                "id": CLASS_NEG,
                "iff": (
                    "ALL NON_RECOVERING median continuation_value < 0 on at least 3 of 4 "
                    "primary checkpoints AND REST11 same direction on at least 3 of 4"
                ),
            },
            "B": {
                "id": CLASS_NOT_HARM,
                "iff": (
                    "ALL or REST11 NON_RECOVERING median continuation_value >= 0 "
                    "on at least 3 of 4 primary checkpoints"
                ),
            },
            "C": {"id": CLASS_MIXED, "iff": "neither A nor B"},
            "override": {
                "id": CLASS_NOT_ACTIONABLE,
                "iff": (
                    "A would fire OR mixed-with-negative-mass, AND at >= 3 of 4 primary "
                    "checkpoints yen_destroyed_on_NON_RECOVERING_WINs "
                    "(sum of continuation_value where FINAL_WIN and continuation_value > 0) "
                    ">= yen_saved_on_NON_RECOVERING_LOSSes "
                    "(sum of -continuation_value where FINAL_LOSS and continuation_value < 0) "
                    "AND at least one TOP20 or EXTEND canonical WIN is in NON_RECOVERING "
                    "at those offset checkpoints. Applies only when negative continuation "
                    "benefit exists to offset. Does not apply when B fires."
                ),
            },
            "integrity": {"id": CLASS_DATA},
            "note": "Mechanism consistency criteria. Not strategy thresholds. Not Gate adoption.",
        },
        "state_names": {
            "RECOVERING_STATE": STATE_RECOVERING,
            "NON_RECOVERING_STATE": STATE_NON_RECOVERING,
        },
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
        "note": "SHA covers contract_body only. Contract must not change after this SHA exists.",
    }
    path = OUT / "precommit.json"
    if path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("SHA") != sha:
            raise RuntimeError(
                f"P4_3_PRECOMMIT_SHA_DRIFT disk={old.get('SHA')} now={sha}. "
                "Contract change after SHA is forbidden."
            )
        return old
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def load_precommit() -> dict[str, Any]:
    path = OUT / "precommit.json"
    if not path.is_file():
        raise RuntimeError("P4_3_PRECOMMIT_MISSING")
    doc = json.loads(path.read_text(encoding="utf-8"))
    body = {k: v for k, v in doc.items() if k not in ("SHA", "generated_at", "note")}
    sha = contract_sha(body)
    if sha != doc.get("SHA"):
        raise RuntimeError("P4_3_PRECOMMIT_SHA_MISMATCH")
    return doc
