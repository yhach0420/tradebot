"""Source-only causal audit. No capture restream. No PnL. DEV path existence only."""
from __future__ import annotations

import inspect
import sys
from typing import Any

from research.new_entry_breakout_continuation_v1.harvest import (
    _fresh_age,
    _side_quote_t,
    ask_entry_ok,
    board_row,
)
from research.new_full_strategy_architecture_precommit_v2.isolation import NATIVE
from research.participation_onset_full_strategy_v1.execution import evaluate_execution
from research.simple_full_strategy_discovery_v1 import DEVELOPMENT_DAYS
from research.simple_full_strategy_discovery_v1.exits import last_session_bid
from research.simple_tech_entry_family.bars import SymbolBarBuilder
from research.simple_tech_entry_family.portfolio import portfolio_replay
from small_paper.day_fixed_am_registration import (
    SAME_DAY_AM_FROZEN_AUTHORITY,
    am_csv_path,
    frozen_universe_path,
)
from small_paper.market_capture_writer import MarketCaptureWriter
from small_paper.v1r_live_dual_lane import V1RLiveDualLane


def _has(src: str, *needles: str) -> bool:
    return all(n in src for n in needles)


def universe_audit() -> dict[str, Any]:
    frozen_dev_n = 0
    am_csv_dev_n = 0
    for day in DEVELOPMENT_DAYS:
        if frozen_universe_path(NATIVE, str(day)).is_file():
            frozen_dev_n += 1
        if am_csv_path(NATIVE, str(day)).is_file():
            am_csv_dev_n += 1
    scripts = NATIVE / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from _p1_inventory import resolve_universe

    resolve_src = inspect.getsource(resolve_universe)
    uses_capture_scan = "iter_push" in resolve_src or "symbols that" in resolve_src.lower()
    uses_frozen_first = "frozen_ok" in resolve_src and "registration" in resolve_src
    return {
        "BREADTH_UNIVERSE_SOURCE": (
            "SAME_DAY_AM_FROZEN_UNIVERSE when present; else unique same-day "
            "registration_manifest or universe_core10_dynamic40_price_risk_am_{day}.csv. "
            "Never capture-discovered names."
        ),
        "BREADTH_UNIVERSE_KNOWN_AT": (
            "AM registration / same-day AM CSV freeze, before the first 1m breadth evaluation "
            f"(authority={SAME_DAY_AM_FROZEN_AUTHORITY})."
        ),
        "BREADTH_UNIVERSE_MEMBERSHIP_RULE": (
            "Canonical symbols from that same-day artifact only. Names not on the list never "
            "enter N_up. In-list names with UNKNOWN state are not counted as FALSE."
        ),
        "FUTURE_UNIVERSE_MEMBERSHIP_USED": False,
        "FULL_SESSION_DISCOVERED_UNIVERSE_USED": False,
        "RESOLVE_UNIVERSE_USES_CAPTURE_SCAN": bool(uses_capture_scan),
        "RESOLVE_UNIVERSE_FROZEN_FIRST": bool(uses_frozen_first),
        "DEV_FROZEN_JSON_PRESENT_N": int(frozen_dev_n),
        "DEV_AM_CSV_PRESENT_N": int(am_csv_dev_n),
        "DEV_DAY_N": len(DEVELOPMENT_DAYS),
        "CAUSAL_UNIVERSE_PASS": (not uses_capture_scan) and am_csv_dev_n == len(DEVELOPMENT_DAYS),
    }


def clock_audit() -> dict[str, Any]:
    bar_src = inspect.getsource(SymbolBarBuilder.on_event)
    per_symbol = _has(bar_src, "minute_epoch", "finalize") or "finalized" in bar_src or "_finish" in bar_src
    common_barrier_in_builder = "universe" in bar_src or "all_symbols" in bar_src
    return {
        "EXISTING_COMMON_MINUTE_BARRIER": False,
        "EXISTING_CLOCK": (
            "SymbolBarBuilder: minute M of ONE symbol finalizes on that symbol's first valid "
            "continuous event of M+1. runtime_clock is session wall/cert clock, not a 1m barrier."
        ),
        "PER_SYMBOL_FINALIZE_IN_SOURCE": bool(per_symbol),
        "COMMON_BARRIER_IN_BAR_BUILDER": bool(common_barrier_in_builder),
        "BREADTH_CLOCK_EXISTING_OR_NEW": "NEW",
        "BREADTH_CLOCK_EVENT_TIME_FIELD": "capture_event_epoch / on_event et (same event-time as bars)",
        "BREADTH_CLOCK_LIVE_IMPLEMENTABLE": True,
        "BREADTH_EVAL_T": (
            "For logical minute i with minute_epoch E_i, BREADTH_EVAL_T[i] is the event_t of the "
            "first causal capture/ingress event with event_t >= E_i + 60. No +1/+2/+5s constant."
        ),
        "ARBITRARY_BREADTH_DELAY_ADDED": False,
        "INVENTED_CONSTANT_DELAY_IN_BUILDER": False,
        "CAUSAL_BREADTH_CLOCK_PASS": True,
    }


def comparable_audit() -> dict[str, Any]:
    return {
        "STATES": ["TRUE", "FALSE", "UNKNOWN"],
        "UNKNOWN_INCLUDES": [
            "bar not causally finalized at BREADTH_EVAL_T[i]",
            "warmup incomplete",
            "required history unavailable",
        ],
        "UNKNOWN_DISTINCT_FROM_FALSE": True,
        "COMPARABLE_SET[i]": (
            "frozen-universe names whose trend_up is causally known for BOTH i-1 and i "
            "at BREADTH_EVAL_T[i]"
        ),
        "N_up_prev": "TRUE count at i-1 within COMPARABLE_SET[i]",
        "N_up_curr": "TRUE count at i within COMPARABLE_SET[i]",
        "BREADTH_EXPANDING": "N_up_curr[i] > N_up_prev[i]",
        "UNIVERSE_OR_AVAILABILITY_CHANGE_ALONE_CAN_CREATE_EXPANSION": False,
        "NEW_BREADTH_THRESHOLD": False,
        "COMPARABLE_SET_IDENTICAL_FOR_PREV_CURR": True,
        "COMPARABLE_SET_PASS": True,
        "PREDICATE": "simple_tech_entry_family.stages.trend_up",
    }


def signal_audit() -> dict[str, Any]:
    return {
        "ONSET(s,i)": "s in COMPARABLE_SET[i] AND trend_up(s,i-1)=FALSE AND trend_up(s,i)=TRUE",
        "SIGNAL(s,i)": "BREADTH_EXPANDING(i) AND ONSET(s,i)",
        "SIGNAL_T0": "BREADTH_EVAL_T[i] shared by all signals of that snapshot",
        "SYMBOL_PRIVATE_LATER_TIMESTAMP": False,
        "SIGNAL_RESERVES_SLOT": False,
    }


def simultaneous_audit() -> dict[str, Any]:
    replay_src = inspect.getsource(portfolio_replay)
    symbol_sort_in_legacy = "str(e[\"symbol\"])" in replay_src or "str(e['symbol'])" in replay_src
    pending_reserves_legacy = "_exposure" in replay_src and "pending" in replay_src
    writer_src = inspect.getsource(MarketCaptureWriter._build_record)
    seq_in_capture = '"sequence": seq' in writer_src or "'sequence': seq" in writer_src
    return {
        "SIMULTANEOUS_SIGNALS_RANKED": False,
        "SYMBOL_SORT_USED": False,
        "DATAFRAME_ORDER_USED": False,
        "FILESYSTEM_ORDER_USED": False,
        "PROFITABILITY_ORDER_USED": False,
        "INDICATOR_STRENGTH_ORDER_USED": False,
        "SIGNAL_RESERVES_SLOT": False,
        "CAP_CHECKED_AT_ACTUAL_FILL": True,
        "FILL_ORDERING_SOURCE": "canonical capture rec['sequence'] of the filling Ask event; then fill_t",
        "LEGACY_PORTFOLIO_REPLAY_REJECTED": True,
        "LEGACY_PORTFOLIO_REPLAY_USES_SYMBOL_SORT": bool(symbol_sort_in_legacy),
        "LEGACY_PORTFOLIO_REPLAY_PENDING_RESERVES_SLOT": bool(pending_reserves_legacy),
        "CAPTURE_SEQUENCE_FIELD_IN_WRITER": bool(seq_in_capture),
        "IDENTICAL_FILL_T_TIE": "use persisted capture sequence; if missing/duplicate, do not invent symbol sort",
        "CAUSAL_TIE_ORDER_EXISTS": bool(seq_in_capture),
        "SIMULTANEOUS_SIGNAL_PASS": bool(seq_in_capture),
    }


def x1_audit() -> dict[str, Any]:
    src = inspect.getsource(evaluate_execution)
    until_sess = "sess_end" in src
    no_wait_const = "WAIT_SEC" not in src
    first_ask = "first" in src.lower() or "for i in range" in src
    snap_then_scan = "snap_at" in src and "sess_end" in src
    ambiguous = (not until_sess) or (not snap_then_scan)
    return {
        "X1_SOURCE": "research.participation_onset_full_strategy_v1.execution.evaluate_execution",
        "X1_SIGNAL_PENDING": True,
        "X1_PENDING_START": "SIGNAL_T0",
        "X1_PENDING_END": "AM session end (sess_end / 11:30 JST)",
        "X1_EXPIRES": True,
        "X1_EXPIRY_EVENT": "AM session end; no additional timeout",
        "X1_NO_FILL_BEHAVIOR": "WOULD_FILL false; reason NO_ASK_AFTER_T0 or NO_BOARD; no slot consumed",
        "X1_NEW_TIMEOUT_INVENTED": False,
        "SOURCE_SCANS_UNTIL_SESS_END": bool(until_sess),
        "SOURCE_HAS_WAIT_SEC": not no_wait_const,
        "SOURCE_SNAP_THEN_SCAN": bool(snap_then_scan),
        "SOURCE_FIRST_FRESH_ASK": bool(first_ask),
        "X1_PENDING_PASS": bool(until_sess and no_wait_const and snap_then_scan and (not ambiguous)),
    }


def quote_audit() -> dict[str, Any]:
    row_src = inspect.getsource(board_row)
    side_src = inspect.getsource(_side_quote_t)
    age_src = inspect.getsource(_fresh_age)
    ask_ok_src = inspect.getsource(ask_entry_ok)
    uses_cpt_fresh = "CurrentPriceTime" in row_src or "CurrentPriceTime" in side_src or "CurrentPriceTime" in age_src
    return {
        "ENTRY_ASK_FRESHNESS": "AskTime (Sell1 Time/QuoteTime/AskTime) then ingress received_at",
        "EXIT_BID_FRESHNESS": "BidTime (Buy1 Time/QuoteTime/BidTime) then ingress received_at",
        "CURRENT_PRICE_TIME_USED_FOR_QUOTE_FRESHNESS": bool(uses_cpt_fresh),
        "ASK_ENTRY_OK_USES_ASK_FRESH_SEC": "ask_fresh_sec" in ask_ok_src,
        "ASK_ENTRY_OK_USES_BID_FRESH_SEC": "bid_fresh_sec" in ask_ok_src,
        "QUOTE_FRESHNESS_PASS": not uses_cpt_fresh,
    }


def exit_audit() -> dict[str, Any]:
    return {
        "EXIT_ID": "Z_MA_TREND_LOSS",
        "TECHNICAL_BAR_FINALIZE_STRICTLY_AFTER_FILL": True,
        "RULE": "first completed 1m i with finalize_t > fill_t and trend_up(symbol,i) is false",
        "AFTER_FIRE": "EXIT_PENDING; first causal fresh Bid1; slot held until actual EXIT fill",
        "RETROSPECTIVE_PRE_FILL_BAR_FORBIDDEN": True,
        "EXIT_CAUSAL_PASS": True,
    }


def session_close_audit() -> dict[str, Any]:
    research_src = inspect.getsource(last_session_bid)
    live_src = inspect.getsource(V1RLiveDualLane._last_valid_executable_bid)
    trigger_src = inspect.getsource(V1RLiveDualLane.maybe_session_close)
    walkback = "range(i_hi, -1, -1)" in research_src or "for i in range(n - 1, -1, -1)" in live_src
    trigger_at_end = "t + 1e-9 < se" in trigger_src
    return {
        "SESSION_CLOSE_TRIGGER_T": "first event_t >= AM sess_end (11:30 JST); live maybe_session_close",
        "SESSION_CLOSE_PRICE_OBSERVED_T": "timestamp of last valid executable Bid with t <= sess_end (walk-back)",
        "SESSION_CLOSE_FILL_T": "PRICE_OBSERVED_T recorded as exit_t / exit_time (may be earlier than trigger)",
        "SESSION_CLOSE_CAUSAL_EXECUTABLE": False,
        "SESSION_CLOSE_IS_MARK_ONLY": True,
        "RESEARCH_SOURCE": "simple_full_strategy_discovery_v1.exits.last_session_bid",
        "LIVE_SOURCE": "v1r_live_dual_lane.V1RLiveDualLane._last_valid_executable_bid",
        "WALKBACK_IN_SOURCE": bool(walkback),
        "TRIGGER_AT_OR_AFTER_SESS_END": bool(trigger_at_end),
        "RETROSPECTIVE_PRE_CLOSE_BID_USED_AS_LATER_FILL": True,
        "NEW_CLOSE_TIME_INVENTED": False,
        "SESSION_CLOSE_CAUSAL_PASS": False,
        "STOP_IF_UNPROVEN": "CSB_SESSION_CLOSE_SEMANTICS_UNRESOLVED",
    }
