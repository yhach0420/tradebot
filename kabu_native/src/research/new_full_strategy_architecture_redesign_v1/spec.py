"""Frozen V3 Full Strategy contract. No economics. V1/V2 immutable."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_full_strategy_architecture_redesign_v1 import (
    ALPHA_MECHANISM_CHANGED,
    AM_END_HM,
    AM_START_HM,
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_AFTER_PASS,
    ARCHITECTURE_ID,
    BAR_FINALIZATION_SEMANTICS_CLARIFIED,
    CASE_FROZEN,
    CASE_UNIMPLEMENTABLE,
    ENTRY_ALPHA_CHANGED,
    NEXT_IF_FROZEN,
    OPERATIONAL_SESSION_RULE_CHANGED,
    PARENT_V1,
    PARENT_V1_SHA256,
    PARENT_V2,
    PORTFOLIO_CAP_CHANGED,
    Q1_NEW_LOGIC_COMPLETION_DIRECT,
    Q2_BLOCKING_WITHOUT_THIS_RUN,
    Q3_OLD_RCA_AS_PURPOSE,
    SESSION,
    SESSION_FLATTEN_HM,
    SESSION_FLATTEN_T_LABEL,
    TECHNICAL_EXIT_CHANGED,
)
from research.simple_full_strategy_discovery_v1 import (
    BOARD_FRESHNESS_SEC,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    MIN_QTY,
    POSITION_CAP,
    SHARES,
)
from research.simple_tech_entry_family import EMA_LONG, EMA_SHORT, EMA_SLOPE_BARS, WARMUP_BARS
from research.systematic_state_transition_library_precommit_v1.spec import execution_contract

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "source_audit.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

TREND_UP_DEF = (
    "simple_tech_entry_family.stages.trend_up: "
    f"ema{int(EMA_SHORT)}[i] > ema{int(EMA_LONG)}[i] AND "
    f"ema{int(EMA_LONG)}[i] > ema{int(EMA_LONG)}[i-{int(EMA_SLOPE_BARS)}]; "
    f"WARMUP_BARS={int(WARMUP_BARS)}"
)


def dumps_sha256(obj: Any) -> str:
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def frozen_strategy() -> dict[str, Any]:
    x1 = execution_contract()
    return {
        "ARCHITECTURE_ID": ARCHITECTURE_ID,
        "SPEC_VERSION": "V3",
        "PARENT_V1": PARENT_V1,
        "PARENT_V1_SHA256": PARENT_V1_SHA256,
        "PARENT_V2": PARENT_V2,
        "ALPHA_MECHANISM": (
            "Cross-sectional breadth of canonical 1m MA trend_up expanding on a common "
            "ingress-causal timer, gating a per-name FALSE→TRUE onset pulse."
        ),
        "ALPHA_MECHANISM_CHANGED": ALPHA_MECHANISM_CHANGED,
        "ENTRY_ALPHA_CHANGED": ENTRY_ALPHA_CHANGED,
        "TECHNICAL_EXIT_CHANGED": TECHNICAL_EXIT_CHANGED,
        "PORTFOLIO_CAP_CHANGED": PORTFOLIO_CAP_CHANGED,
        "BAR_FINALIZATION_SEMANTICS_CLARIFIED": BAR_FINALIZATION_SEMANTICS_CLARIFIED,
        "OPERATIONAL_SESSION_RULE_CHANGED": OPERATIONAL_SESSION_RULE_CHANGED,
        "UNIVERSE": {
            "SOURCE": (
                "SAME_DAY_AM_FROZEN_UNIVERSE when present; else unique same-day "
                "registration_manifest or universe_core10_dynamic40_price_risk_am_{day}.csv"
            ),
            "KNOWN_AT": "AM registration / same-day AM CSV freeze, before first T_i",
            "MEMBERSHIP": "canonical symbols from that artifact only; not capture-discovered",
            "FUTURE_UNIVERSE_MEMBERSHIP_USED": False,
        },
        "TIMER": {
            "TIMER_ORDER_DOMAIN": "INGRESS_CAUSAL_ORDER",
            "ARRIVAL_CLOCK": "persisted rec['received_at_jst']",
            "ARRIVAL_ORDER": "persisted rec['sequence'] ascending",
            "MARKET_TIMESTAMP_SORT_FORBIDDEN": True,
            "CAPTURE_EVENT_EPOCH_EXCHANGE_FALLBACK_FORBIDDEN": True,
            "T_i": "JST minute boundary E_i + 60s for logical minute i (minute_epoch E_i)",
            "PROCESS": [
                "process ingress/capture records in rec['sequence'] order",
                "before record R, fire every T_i with last_arrival < T_i <= arrival(R)",
                "at T_i finalize minute i for all frozen-universe symbols from records already processed",
                "later-arriving events must not mutate that finalized bar",
            ],
            "TIMER_EVENT_PRECEDENCE": "TIMER_BEFORE_COINCIDENT_INGRESS",
            "COINCIDENT_RULE": (
                "If received_at_jst == T_i, the timer fires first; that record is not available to minute i"
            ),
            "LATE_EVENT_RETROACTIVE_BAR_MUTATION": False,
            "REPLAY_TIMER_LIVE_SEMANTICS_EQUIVALENT": True,
            "LIVE_EQUIVALENCE": (
                "Live: wall/session clock fires T_i; only already-ingested PUSH is available. "
                "Replay: insert the same T_i into the capture-sequence stream using received_at_jst, "
                "never by sorting AskTime/BidTime/CurrentPriceTime."
            ),
            "DATAFRAME_ORDER_USED": False,
        },
        "BREADTH": {
            "STATES": ["TRUE", "FALSE", "UNKNOWN"],
            "UNKNOWN_DISTINCT_FROM_FALSE": True,
            "PREDICATE": TREND_UP_DEF,
            "COMPARABLE_SET": "frozen-universe names with trend_up causally known for BOTH i-1 and i at T_i",
            "N_up_prev": "TRUE count at i-1 within COMPARABLE_SET[i]",
            "N_up_curr": "TRUE count at i within COMPARABLE_SET[i]",
            "BREADTH_EXPANDING": "N_up_curr > N_up_prev",
            "NEW_THRESHOLD": False,
            "UNIVERSE_OR_AVAILABILITY_CHANGE_ALONE_CAN_CREATE_EXPANSION": False,
        },
        "SIGNAL": {
            "ONSET": "s in COMPARABLE_SET[i] AND trend_up(s,i-1)=FALSE AND trend_up(s,i)=TRUE",
            "SIGNAL": "BREADTH_EXPANDING(i) AND ONSET(s,i)",
            "SIGNAL_T0": "T_i (BREADTH_EVAL_T[i]); no symbol-private later timestamp",
            "SIGNAL_RESERVES_SLOT": False,
            "SIMULTANEOUS_RANKING": False,
            "SYMBOL_SORT_USED": False,
            "FILL_ORDER": "actual Ask fill event by rec['sequence']; CAP/same-symbol checked at fill",
        },
        "EXECUTION": {
            **x1,
            "X1_PENDING_START": "SIGNAL_T0",
            "X1_PENDING_END": "SESSION_FLATTEN_T",
            "X1_EXPIRES": True,
            "X1_EXPIRY_EVENT": "SESSION_FLATTEN_T; unfilled ENTRY pending expired; no extra timeout",
            "ASK_FRESHNESS": "AskTime then ingress received_at_jst",
            "BID_FRESHNESS": "BidTime then ingress received_at_jst",
            "CURRENT_PRICE_TIME_QUOTE_FRESHNESS": False,
            "BOARD_FRESHNESS_SEC": float(BOARD_FRESHNESS_SEC),
            "MIN_QTY": float(MIN_QTY),
            "SEARCHED_THIS_RUN": False,
        },
        "EXIT_TECHNICAL": {
            "EXIT_ID": "Z_MA_TREND_LOSS",
            "RULE": "first completed 1m i with bar finalized at T_i > fill_t and trend_up(symbol,i) false",
            "AFTER_FIRE": "EXIT_PENDING; first causal fresh Bid1; slot held until actual EXIT fill",
            "TECHNICAL_EXIT_CHANGED": False,
        },
        "SESSION_FLATTEN": {
            "SESSION_FLATTEN_T": SESSION_FLATTEN_T_LABEL,
            "SESSION_FLATTEN_HM": list(SESSION_FLATTEN_HM),
            "TZ": "Asia/Tokyo",
            "REASON": "last complete strategy-clock boundary strictly before AM 11:30",
            "SESSION_FLATTEN_SELECTED_FROM_PNL": False,
            "AM_END": "11:30:00 JST",
            "AT_FLATTEN": [
                "expire ALL unfilled ENTRY pending signals",
                "forbid new ENTRY signal creation/admission",
                "mark every open position SESSION_EXIT_PENDING unless already EXIT_PENDING",
                "preserve already-pending technical EXIT; no duplicate exit order",
            ],
            "ENTRY_PENDING_EXPIRE_T": SESSION_FLATTEN_T_LABEL,
            "NEW_ENTRY_ALLOWED_AFTER_FLATTEN": False,
            "ENTRY_FILL_ALLOWED_AFTER_FLATTEN": False,
            "RETROSPECTIVE_BID_WALKBACK": False,
            "STANDING_BID": (
                "If last ingested Bid1 for the symbol is causally known and BidTime-fresh at T_flatten, "
                "execute at SESSION_FLATTEN_T using that Bid1. fill_t = SESSION_FLATTEN_T."
            ),
            "WAIT_BID": (
                "Else wait for first subsequent fresh causal Bid1 with arrival < AM 11:30. "
                "fill_t = that Bid event arrival time."
            ),
            "SESSION_EXIT_UNFILLED": "true if no executable Bid before 11:30; no fabricated PnL",
            "MAX_EXIT_FILL_PER_POSITION": 1,
            "SAME_TIMER_PRIORITY": (
                "If technical EXIT already pending, flatten does not create a second order. "
                "If both become eligible on the same T_i, one EXIT_PENDING; first causal Bid fills once."
            ),
        },
        "PORTFOLIO": {
            "SHARES": int(SHARES),
            "CAP": int(POSITION_CAP),
            "CAP_ROLE": "occupancy constraint, not a quality filter",
            "same_symbol": True,
            "occupancy_increments_at": "actual ENTRY fill_t",
            "slot_release": "actual EXIT fill",
            "reentry": True,
            "SESSION": SESSION,
            "SESSION_WINDOW": {"start_hm": list(AM_START_HM), "end_hm": list(AM_END_HM), "tz": "Asia/Tokyo"},
        },
        "VWAP_ENTRY_USED": False,
        "VWAP_EXIT_USED": False,
        "VWAP_FILTER_USED": False,
        "BOARD_PRIMARY_ALPHA": False,
        "NEW_THRESHOLD": False,
        "SIZING": False,
    }


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "FINAL_PRE_ECONOMICS_SPEC_PASS": True,
        "NO_PNL": True,
        "NO_REPLAY": True,
        "NO_ECONOMICS": True,
        "Q1": Q1_NEW_LOGIC_COMPLETION_DIRECT,
        "Q2": Q2_BLOCKING_WITHOUT_THIS_RUN,
        "Q3": Q3_OLD_RCA_AS_PURPOSE,
        "ANOTHER_PRECOMMIT_AFTER_PASS": ANOTHER_PRECOMMIT_AFTER_PASS,
        "CASE_FROZEN": CASE_FROZEN,
        "CASE_UNIMPLEMENTABLE": CASE_UNIMPLEMENTABLE,
        "NEXT_IF_FROZEN": NEXT_IF_FROZEN,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "FROZEN_STRATEGY": frozen_strategy(),
    }


def spec_sha256() -> str:
    return dumps_sha256(canonical_spec())


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
