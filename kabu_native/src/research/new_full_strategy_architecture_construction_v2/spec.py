"""Frozen V4 Full Strategy contract. No economics. V3 identity unchanged."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_full_strategy_architecture_construction_v2 import (
    AM_END_HM,
    AM_START_HM,
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    ARCHITECTURE_ID,
    BOARD_PRIMARY_ALPHA,
    CASE_DUPLICATE,
    CASE_FROZEN,
    CASE_STRUCTURAL,
    CSB_EVAL_ID,
    CSB_RCA_RUN,
    CSB_REQUIRED_VERDICT,
    CSB_RETUNE,
    DAILY_MA_ADDED,
    EMA_LEVEL_PERIOD,
    HTF_ORIGIN_HM,
    HTF_TREND_BOOLEAN_USED,
    HTF_WIDTH_SEC,
    LEVEL_AVAILABLE_DAY_MIN,
    NEW_CANDIDATE_ECONOMICS_RUN,
    NEW_CANDIDATE_PNL_READ_N,
    NEXT_IF_FROZEN,
    OLD_ST_RCA_CONTINUED,
    PINNED_V3_SHA256,
    SESSION,
    SESSION_FLATTEN_HM,
    SESSION_FLATTEN_T_LABEL,
    SPEC_VERSION,
    VOLUME_THRESHOLD_SEARCH,
    VWAP_ENTRY_USED,
    VWAP_EXIT_USED,
    VWAP_FILTER_USED,
)
from research.simple_full_strategy_discovery_v1 import (
    BOARD_FRESHNESS_SEC,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    MIN_QTY,
    POSITION_CAP,
    SHARES,
)
from research.simple_tech_entry_family import VOLUME_MEDIAN_BARS, VOLUME_MULT
from research.systematic_state_transition_library_precommit_v1.spec import execution_contract

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "level_semantics.py",
    "volume_identity.py",
    "closed_lineage.py",
    "csb_pin.py",
    "availability.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

RESISTANCE_TEST_RULE = (
    "For completed 1m bar i and pre-existing level L=TEST_LEVEL[i]: "
    "Close[i-1] < L AND High[i] >= L AND Close[i] <= L. No distance threshold."
)
BREAK_RULE = (
    "On later completed 1m bar j > TEST_BAR_I, same LEVEL_VERSION_ID still active: "
    "Close[j] > TEST_LEVEL AND S_VOL_CONFIRM_1M[j]. SIGNAL_T0 = bar-j causal finalize timer. "
    "Same-bar test+break forbidden."
)
EXIT_RULE = (
    "Z_BREAK_LEVEL_SUPPORT_FAIL: after actual ENTRY fill, first later completed 1m k with "
    "finalize_t[k] > entry_fill_t AND Close[k] < BREAK_LEVEL → EXIT_PENDING → first fresh "
    "causal executable Bid1 → EXIT fill → slot release."
)
VOLUME_RULE = (
    f"volume_confirm: volume[j] >= {float(VOLUME_MULT)} * median(volume[j-{int(VOLUME_MEDIAN_BARS)}:j]); "
    "current bar excluded. SOURCE simple_tech_entry_family.stages.volume_confirm as S_VOL_CONFIRM_1M."
)


def dumps_sha256(obj: Any) -> str:
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def frozen_strategy() -> dict[str, Any]:
    x1 = execution_contract()
    return {
        "ARCHITECTURE_ID": ARCHITECTURE_ID,
        "SPEC_VERSION": SPEC_VERSION,
        "CSB_PARENT_EVAL": CSB_EVAL_ID,
        "CSB_CLOSED_VERDICT": CSB_REQUIRED_VERDICT,
        "CSB_RCA_RUN": CSB_RCA_RUN,
        "CSB_RETUNE": CSB_RETUNE,
        "PINNED_V3_SHA256": PINNED_V3_SHA256,
        "ALPHA_MECHANISM": (
            "Pre-existing completed 5m EMA21 as a numeric structural price level; "
            "1m resistance test of that frozen level; later close-through of the same "
            "LEVEL_VERSION_ID with canonical S_VOL_CONFIRM_1M; ENTRY; EXIT when the "
            "frozen BREAK_LEVEL fails as support."
        ),
        "HTF_LEVEL": {
            "INSTRUMENT": "5m EMA21",
            "PERIOD": int(EMA_LEVEL_PERIOD),
            "ROLE": "STRUCTURAL_PRICE_LEVEL",
            "HTF_TREND_BOOLEAN_USED": HTF_TREND_BOOLEAN_USED,
            "EMA9_GT_EMA21_USED": False,
            "DAILY_MA_ADDED": DAILY_MA_ADDED,
            "WIDTH_SEC": float(HTF_WIDTH_SEC),
            "ORIGIN_HM": list(HTF_ORIGIN_HM),
            "SESSION_SCOPED": True,
            "PARTIAL_BUCKET_FORBIDDEN": True,
            "SOURCE_AGG": "simple_tech_entry_family.v7_bars.aggregate_bars",
            "SOURCE_EMA": "simple_tech_entry_family.indicators.ema",
        },
        "LEVEL_ASOF": {
            "TEST_LEVEL[i]": "latest completed 5m EMA21 with finalize_t <= START_T[i]",
            "START_T[i]": "1m bar i minute_epoch (bar start), not 1m finalize_t",
            "LEVEL_PREEXISTS_TEST_BAR": True,
            "SAME_BAR_CONTRIBUTES_TO_TEST_LEVEL": False,
            "C1_ASOF_FINALIZE_LE_T0_NOT_USED_FOR_TEST_LEVEL": True,
        },
        "LEVEL_VERSION": {
            "LEVEL_VERSION_ID": "identity of the published completed 5m bar that produced TEST_LEVEL",
            "TEST_LEVEL_FIXED_IN_EPISODE": True,
            "NEW_LEVEL_EXPIRES_UNFILLED_EPISODE": True,
            "N_BAR_TIMEOUT": False,
            "BPS_TIMEOUT": False,
            "PNL_SELECTED_PERSISTENCE": False,
        },
        "RESISTANCE_TEST": {
            "RULE": RESISTANCE_TEST_RULE,
            "DISTANCE_THRESHOLD": False,
            "STATE": "RESISTANCE_TESTED",
        },
        "BREAK": {
            "LATER_BAR_REQUIRED": True,
            "RULE": BREAK_RULE,
            "SAME_BAR_TEST_AND_BREAK": False,
            "SIGNAL_T0": "completed bar-j causal finalize timer",
            "SIGNAL_RESERVES_SLOT": False,
        },
        "VOLUME": {
            "STATE_ID": "S_VOL_CONFIRM_1M",
            "SOURCE_PATH": "src/research/simple_tech_entry_family/stages.py",
            "SOURCE_FUNCTION": "volume_confirm",
            "RULE": VOLUME_RULE,
            "VOLUME_MEDIAN_BARS": int(VOLUME_MEDIAN_BARS),
            "VOLUME_MULT": float(VOLUME_MULT),
            "VOLUME_THRESHOLD_SEARCH": VOLUME_THRESHOLD_SEARCH,
            "ROLE": "BREAK_PARTICIPATION_CONFIRMATION",
        },
        "ENTRY": {
            "SEQUENCE": ["IDLE", "RESISTANCE_TESTED", "ENTRY_PENDING", "OPEN"],
            "EXECUTION": x1,
            "X1_PENDING_START": "SIGNAL_T0",
            "X1_PENDING_END": "SESSION_FLATTEN_T",
            "ASK_FRESHNESS": "AskTime then ingress received_at_jst",
            "BID_FRESHNESS": "BidTime then ingress received_at_jst",
            "CURRENT_PRICE_TIME_QUOTE_FRESHNESS": False,
            "BOARD_FRESHNESS_SEC": float(BOARD_FRESHNESS_SEC),
            "MIN_QTY": float(MIN_QTY),
        },
        "BREAK_LEVEL": {
            "AT_BREAK_CONFIRMED": "BREAK_LEVEL = TEST_LEVEL",
            "DYNAMIC_EMA_AFTER_ENTRY_CHANGES_THESIS": False,
            "PERSIST_THROUGH_TRADE": True,
        },
        "EXIT_TECHNICAL": {
            "EXIT_ID": "Z_BREAK_LEVEL_SUPPORT_FAIL",
            "RULE": EXIT_RULE,
            "Z3_USED": False,
            "Z_MA_TREND_LOSS_USED": False,
            "VWAP_EXIT_USED": False,
            "FIXED_TIMEOUT": False,
        },
        "EPISODE_RESET": {
            "BEFORE_ENTRY_EXPIRE": ["new LEVEL_VERSION_ID", "SESSION_FLATTEN_T"],
            "N_BAR_TIMEOUT": False,
            "AFTER_TRADE_REENTRY": "new complete RESISTANCE_TEST then BREAK_CONFIRM",
        },
        "TIMER": {
            "REUSED_FROM": "NEW_FULL_STRATEGY_ARCHITECTURE_REDESIGN_V1",
            "TIMER_ORDER_DOMAIN": "INGRESS_CAUSAL_ORDER",
            "ARRIVAL_CLOCK": "persisted rec['received_at_jst'] else rec['received_at']",
            "ARRIVAL_ORDER": "persisted rec['sequence'] ascending",
            "TIMER_EVENT_PRECEDENCE": "TIMER_BEFORE_COINCIDENT_INGRESS",
            "LATE_EVENT_RETROACTIVE_BAR_MUTATION": False,
            "MARKET_TIMESTAMP_SORT_FORBIDDEN": True,
            "CAPTURE_EVENT_EPOCH_EXCHANGE_FALLBACK_FORBIDDEN": True,
        },
        "SESSION_FLATTEN": {
            "SESSION_FLATTEN_T": SESSION_FLATTEN_T_LABEL,
            "SESSION_FLATTEN_HM": list(SESSION_FLATTEN_HM),
            "RETROSPECTIVE_BID_WALKBACK": False,
            "ENTRY_FILL_ALLOWED_AFTER_FLATTEN": False,
            "NEW_ENTRY_ALLOWED_AFTER_FLATTEN": False,
            "REUSED_FROM": "NEW_FULL_STRATEGY_ARCHITECTURE_REDESIGN_V1",
        },
        "PORTFOLIO": {
            "SHARES": int(SHARES),
            "CAP": int(POSITION_CAP),
            "same_symbol": True,
            "occupancy_increments_at": "actual ENTRY fill_t",
            "slot_release": "actual EXIT fill",
            "reentry": True,
            "reentry_requires_new_episode": True,
            "SESSION": SESSION,
            "SESSION_WINDOW": {"start_hm": list(AM_START_HM), "end_hm": list(AM_END_HM), "tz": "Asia/Tokyo"},
        },
        "FORBIDDEN_IN_V2": {
            "RCI": True,
            "BB": True,
            "VWAP_ENTRY_USED": VWAP_ENTRY_USED,
            "VWAP_EXIT_USED": VWAP_EXIT_USED,
            "VWAP_FILTER_USED": VWAP_FILTER_USED,
            "BOARD_PRIMARY_ALPHA": BOARD_PRIMARY_ALPHA,
            "SECOND_MA": True,
            "3M_SEARCH": True,
            "10M_SEARCH": True,
            "15M_SEARCH": True,
            "EMA_PERIOD_SEARCH": True,
            "DAILY_MA_ADDED": DAILY_MA_ADDED,
        },
        "STRUCTURAL_COVERAGE_INHERITED_MIN_DAYS": int(LEVEL_AVAILABLE_DAY_MIN),
        "ANOTHER_PRECOMMIT_RUN": ANOTHER_PRECOMMIT_RUN,
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "NEW_CANDIDATE_PNL_READ_N": NEW_CANDIDATE_PNL_READ_N,
        "NEW_CANDIDATE_ECONOMICS_RUN": NEW_CANDIDATE_ECONOMICS_RUN,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "CASE_FROZEN": CASE_FROZEN,
        "CASE_DUPLICATE": CASE_DUPLICATE,
        "CASE_STRUCTURAL": CASE_STRUCTURAL,
        "NEXT_IF_FROZEN": NEXT_IF_FROZEN,
    }


def spec_sha256() -> str:
    return dumps_sha256(frozen_strategy())


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
