"""Causal board / Pre-CAP RCA. Diagnostic only. No policy. No threshold search. No board_ok revival."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    CANONICAL_FRESHNESS_SEC,
    EXIT_REASON,
    PARENT_SPEC_SHA256_EXPECTED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
)
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.branch_u_holdout_spec import (
    CAUSAL_SPEC_SHA256_EXPECTED,
    HOLDOUT_CLASS,
    MIN_BRANCH_U_EXIT_N,
)
from research.simple_tech_redesign.v28_spec import SHARES
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_CAUSAL_BOARD_INFORMATION_RCA"
HOLDOUT_ANALYSIS_ID = "SIMPLE_TECH_BRANCH_U_TEMPORAL_HOLDOUT_V1"
HOLDOUT_VERDICT_KEPT = "SIMPLE_TECH_BRANCH_U_HOLDOUT_ACCUMULATING"
HOLDOUT_STATUS = "SIMPLE_TECH_BRANCH_U_HOLDOUT_TERMINATED_INCONCLUSIVE"
HOLDOUT_TERMINATION_REASON = "PRECOMMITTED_DIRECTION_GATE_NOT_REACHED_BUT_RESEARCH_PRIORITY_MOVED_TO_CAUSAL_RCA"

PRIMARY_QUESTION = "PRE_CAP_ENTRY_QUALITY"
SECONDARY_QUESTION = "EXIT_BOARD_CONFIRMATION"
CAP_ROLE = "MAX_CONCURRENT_POSITION_CONSTRAINT"
BOARD_OK_REUSED = False
POLICY_CREATED = False
THRESHOLD_SEARCH = False
COMBINATION_SEARCH = False
PNL_WINDOW_SEARCH = False
SIZING_CHANGED = False
CAP_CHANGED = False
ENTRY_CHANGED = False
EXIT_CHANGED = False
TRUE_OOS = False
CERTIFIED = False
RUNTIME_CANDIDATE = False
RESEARCH_PARALLELISM = 1

EVOLUTION_WINDOW_SEC = float(CANONICAL_FRESHNESS_SEC)
EVOLUTION_WINDOW_SOURCE = "CANONICAL_FRESHNESS_SEC_FROZEN_NOT_PNL_SEARCHED"
DEPTH_LEVELS = 10
TRUE_L1_BID = "Buy1"
TRUE_L1_ASK = "Sell1"
KABU_INVERTED_BIDPRICE_NOT_USED_AS_TRUE_BID = True

CLIFF_MIN_ABS = 0.20
EXIT_CLIFF_MIN_ABS = 0.30
DAY_AGREEMENT_MIN = 0.50
MAX_TOP_SYMBOL_SHARE = 0.50
MIN_GROUP_N = 8
MIN_EXIT_GROUP_N = 5
MIN_VARS_FOR_SUPPORT = 2

FEATURE_KEYS = (
    "spread_bps",
    "bid1_qty",
    "ask1_qty",
    "bid1_ask1_qty_ratio",
    "l1_imbalance",
    "bid_depth",
    "ask_depth",
    "depth_imbalance",
    "bid_px_change",
    "ask_px_change",
    "bid_qty_change",
    "ask_qty_change",
    "spread_bps_change",
    "l1_imbalance_change",
    "bid1_downshift_n",
    "bid_depletion_event_n",
    "bid_refill_event_n",
    "ask_depletion_event_n",
    "ask_add_event_n",
    "spread_expand_event_n",
)

AUDIT_EXAMPLES = (
    ("20260902", "4440"),
    ("20260902", "4667"),
    ("20260831", "6862"),
)

FORBIDDEN_DAYS = ("20260903",)


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if obj is None:
        return None
    return str(obj)


def canonical_board_rca_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "BRANCH_U_ONE_SHOT_SPEC_SHA256": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
            "CAUSAL_SPEC_SHA256": CAUSAL_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "PRIMARY_QUESTION": PRIMARY_QUESTION,
            "SECONDARY_QUESTION": SECONDARY_QUESTION,
            "CAP_ROLE": CAP_ROLE,
            "POSITION_CAP": int(POSITION_CAP),
            "SHARES": int(SHARES),
            "WAIT_SEC": float(DEV_WAIT_SEC),
            "BOARD_OK_REUSED": False,
            "POLICY_CREATED": False,
            "THRESHOLD_SEARCH": False,
            "COMBINATION_SEARCH": False,
            "PNL_WINDOW_SEARCH": False,
            "EVOLUTION_WINDOW_SEC": float(EVOLUTION_WINDOW_SEC),
            "EVOLUTION_WINDOW_SOURCE": EVOLUTION_WINDOW_SOURCE,
            "TRUE_L1_BID": TRUE_L1_BID,
            "TRUE_L1_ASK": TRUE_L1_ASK,
            "KABU_INVERTED_BIDPRICE_NOT_USED_AS_TRUE_BID": True,
            "DEPTH_LEVELS": int(DEPTH_LEVELS),
            "FEATURE_KEYS": list(FEATURE_KEYS),
            "CLIFF_MIN_ABS": float(CLIFF_MIN_ABS),
            "EXIT_CLIFF_MIN_ABS": float(EXIT_CLIFF_MIN_ABS),
            "DAY_AGREEMENT_MIN": float(DAY_AGREEMENT_MIN),
            "MAX_TOP_SYMBOL_SHARE": float(MAX_TOP_SYMBOL_SHARE),
            "MIN_GROUP_N": int(MIN_GROUP_N),
            "MIN_EXIT_GROUP_N": int(MIN_EXIT_GROUP_N),
            "MIN_VARS_FOR_SUPPORT": int(MIN_VARS_FOR_SUPPORT),
            "TESTED_BRANCH_U_EXIT_MECHANISMS": list(TESTED_BRANCH_U_EXIT_MECHANISMS),
            "EXIT_REASON": EXIT_REASON,
            "HOLDOUT_VERDICT_KEPT": HOLDOUT_VERDICT_KEPT,
            "HOLDOUT_STATUS": HOLDOUT_STATUS,
            "HOLDOUT_CLASS": HOLDOUT_CLASS,
            "MIN_BRANCH_U_EXIT_N": int(MIN_BRANCH_U_EXIT_N),
            "TRUE_OOS": False,
            "research_parallelism": 1,
        }
    )


def spec_sha256_board_rca(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_board_rca_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert BOARD_OK_REUSED is False
assert POLICY_CREATED is False
assert THRESHOLD_SEARCH is False
assert abs(float(EVOLUTION_WINDOW_SEC) - float(CANONICAL_FRESHNESS_SEC)) < 1e-12
assert int(POSITION_CAP) == 5
assert list(LOCKED_SERIES_DAYS) == ["20260828", "20260831", "20260901", "20260902"]
assert "20260903" not in list(ELIGIBLE_DAYS)
assert "20260903" not in list(LOCKED_SERIES_DAYS)
