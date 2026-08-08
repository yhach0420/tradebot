"""Prospective precommit artifact + daily integrity rules (preflight only)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import (
    ANCHOR_SHA,
    CHECKPOINTS,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    FEATURE_ORDER,
    HISTORICAL_LAST,
    HORIZON_SEC,
    LOT_QTY,
    MODEL_ARTIFACT_SHA,
    POSITION_CAP,
    PROSPECTIVE_FROM,
    SOURCE_X36_RUN,
    TRAINING_PANEL_SHA,
    V1R_SHA,
    WAIT_SEC,
)

JST = ZoneInfo("Asia/Tokyo")


def build_precommit(*, model_identity: dict, creation_ts: str | None = None) -> dict[str, Any]:
    ts = creation_ts or datetime.now(JST).isoformat()
    body = {
        "manifest_id": "PROSPECTIVE_PRECOMMIT_V1",
        "full_strategy_manifest": "PASSIVE_FIXED600_FULL_STRATEGY_V1R",
        "full_strategy_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "training_panel_sha": TRAINING_PANEL_SHA,
        "historical_performance_sot": SOURCE_X36_RUN,
        "anchor_sha": ANCHOR_SHA,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "prospective_start_boundary": PROSPECTIVE_FROM,
        "historical_last_inclusive": HISTORICAL_LAST,
        "no_retune_declaration": (
            "Prospective results must not change coefficients, scaler, features, "
            "allocator, ENTRY, wait, fill contract, cap, duplicate rule, EXIT600, "
            "bid lookup, or anchor clocks. Any change ends this prospective series."
        ),
        "daily_integrity_rules": [
            "strategy_sha_exact",
            "model_artifact_sha_exact",
            "feature_order_exact",
            "scaler_exact",
            "coefficients_exact",
            "entry_sha_exact",
            "exit_sha_exact",
            "cap_eq_5",
            "no_hard_cap_violation",
            "no_live_order",
            "no_strategy_mutation",
        ],
        "invalid_day_label": "PROSPECTIVE_DAY_INVALID",
        "no_retroactive_reeval": True,
        "evaluation_checkpoints": CHECKPOINTS,
        "metric_definitions": {
            "total_pnl_yen": "sum realized_pnl_yen over day/series",
            "opp_bps_per_signal": "mean opportunity bps over original signals (unfilled/blocked=0)",
            "bps_per_fill": "mean FIXED600 executable bps over accepted fills",
            "pf": "sum positive opp / abs sum negative opp",
            "ss_balanced": "mean of (date,symbol,session) mean opp",
            "day_balanced": "mean of day mean opp",
            "same_as_historical14": True,
        },
        "allocator": {
            "family": "A1_FILL",
            "feature_order": list(FEATURE_ORDER),
            "cohort_topk": True,
            "tie_break": "symbol_ascending",
            "no_refit": True,
        },
        "capacity": {
            "position_cap": POSITION_CAP,
            "lot_qty": LOT_QTY,
            "pending_reserves_slot": True,
            "wait_sec": WAIT_SEC,
            "duplicate_rule": "no_overlap_replace",
        },
        "exit": {
            "horizon_sec": HORIZON_SEC,
            "lookup": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
            "session_close": True,
        },
        "observation_only": True,
        "runtime_reflect": False,
        "submit_cancel_live": "0/0/0",
        "discord_production_reflect": False,
        "capital_not_live_deployable": True,
        "creation_timestamp_jst": ts,
        "model_identity_preflight": {
            "coefficients_identity": model_identity.get("coefficients_identity"),
            "intercept_identity": model_identity.get("intercept_identity"),
            "feature_order_identity": model_identity.get("feature_order_identity"),
            "scaler_identity": model_identity.get("scaler_identity"),
        },
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
