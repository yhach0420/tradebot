"""NEW_FULL_STRATEGY_ARCHITECTURE_PRECOMMIT_V2. Causal hardening. No PnL."""
from __future__ import annotations

from research.new_full_strategy_architecture_precommit_v2 import (
    ANALYSIS_ID,
    CASE_UNRESOLVED,
    CORE_ARCHITECTURE_CHANGED,
    NEXT_IF_UNRESOLVED,
    PARENT_ARCHITECTURE_ID,
    Q1_NEW_LOGIC_COMPLETION_DIRECT,
    Q2_BLOCKING_WITHOUT_THIS_RUN,
    Q3_OLD_RCA_AS_PURPOSE,
    STOP_SESSION_CLOSE,
)
from research.new_full_strategy_architecture_precommit_v2.analyze import PNL_KEYS, build_answers, decide
from research.new_full_strategy_architecture_precommit_v2.spec import canonical_spec


def _walk_pnl(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert str(k) not in PNL_KEYS
            _walk_pnl(v)
    elif isinstance(obj, list):
        for v in obj:
            _walk_pnl(v)


def test_contract_no_economics_no_retune():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["NO_PNL"] is True
    assert spec["NO_REPLAY"] is True
    assert spec["CORE_ARCHITECTURE_CHANGED"] is False
    assert spec["NEW_THRESHOLD"] is False
    assert spec["ARBITRARY_BREADTH_DELAY_ADDED"] is False
    assert Q1_NEW_LOGIC_COMPLETION_DIRECT is True
    assert Q2_BLOCKING_WITHOUT_THIS_RUN is True
    assert Q3_OLD_RCA_AS_PURPOSE is False
    assert CORE_ARCHITECTURE_CHANGED is False


def test_session_close_unresolved_no_pnl():
    pack = decide()
    g = pack["gates"]
    d = pack["decision"]
    assert g["CAUSAL_UNIVERSE_PASS"] is True
    assert g["CAUSAL_BREADTH_CLOCK_PASS"] is True
    assert g["COMPARABLE_SET_PASS"] is True
    assert g["SIMULTANEOUS_SIGNAL_PASS"] is True
    assert g["X1_PENDING_PASS"] is True
    assert g["QUOTE_FRESHNESS_PASS"] is True
    assert g["EXIT_CAUSAL_PASS"] is True
    assert g["SESSION_CLOSE_CAUSAL_PASS"] is False
    assert pack["universe"]["FUTURE_UNIVERSE_MEMBERSHIP_USED"] is False
    assert pack["universe"]["FULL_SESSION_DISCOVERED_UNIVERSE_USED"] is False
    assert pack["clock"]["ARBITRARY_BREADTH_DELAY_ADDED"] is False
    assert pack["clock"]["BREADTH_CLOCK_LIVE_IMPLEMENTABLE"] is True
    assert pack["comparable"]["UNKNOWN_DISTINCT_FROM_FALSE"] is True
    assert pack["simultaneous"]["SYMBOL_SORT_USED"] is False
    assert pack["simultaneous"]["SIGNAL_RESERVES_SLOT"] is False
    assert pack["x1"]["X1_PENDING_PASS"] is True
    assert pack["quote"]["CURRENT_PRICE_TIME_USED_FOR_QUOTE_FRESHNESS"] is False
    assert pack["session_close"]["SESSION_CLOSE_IS_MARK_ONLY"] is True
    assert pack["session_close"]["RETROSPECTIVE_PRE_CLOSE_BID_USED_AS_LATER_FILL"] is True
    assert d["VERDICT"] == CASE_UNRESOLVED
    assert d["NEXT"] == NEXT_IF_UNRESOLVED
    assert d["SPECIFIC_STOP"] == STOP_SESSION_CLOSE
    assert d["FULL_STRATEGY_SPEC_SHA256_V2"] is None
    assert d["NEW_ARCHITECTURE_ECONOMICS_RUN"] is False
    _walk_pnl(pack)
    a = build_answers(pack)
    assert a["18_selected_architecture_ID"] == PARENT_ARCHITECTURE_ID
    assert a["51_breadth_universe_source"]
    assert a["52_universe_known_causally"] is True
    assert a["53_full_session_discovered_universe_used"] is False
    assert a["54_arbitrary_breadth_delay_added"] is False
    assert a["55_common_clock_live_implementable"] is True
    assert a["56_UNKNOWN_distinct_from_FALSE"] is True
    assert a["57_comparable_set_identical_for_prev_curr"] is True
    assert a["58_simultaneous_signals_ranked"] is False
    assert a["59_signals_reserve_slots"] is False
    assert a["60_CAP_checked_at_actual_fill"] is True
    assert a["62_X1_pending_lifetime_proven"] is True
    assert a["66_CurrentPriceTime_quote_freshness"] is False
    assert a["67_session_close_executable_causality_proven"] is False
    assert a["68_retrospective_pre_close_Bid_used_as_later_fill"] is True
    assert a["69_core_architecture_changed"] is False
    assert a["70_new_threshold"] is False
    assert a["71_PnL_read"] is False
    assert a["72_economics_run"] is False
    assert a["49_VERDICT"] == CASE_UNRESOLVED
    assert a["50_NEXT"] == NEXT_IF_UNRESOLVED
