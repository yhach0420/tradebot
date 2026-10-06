"""NEW_FULL_STRATEGY_ARCHITECTURE_REDESIGN_V1. Final pre-economics freeze. No PnL."""
from __future__ import annotations

from research.new_full_strategy_architecture_redesign_v1 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_AFTER_PASS,
    CASE_FROZEN,
    NEXT_IF_FROZEN,
    SESSION_FLATTEN_T_LABEL,
)
from research.new_full_strategy_architecture_redesign_v1.analyze import PNL_KEYS, build_answers, decide
from research.new_full_strategy_architecture_redesign_v1.spec import dumps_sha256, frozen_strategy


def _walk_pnl(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert str(k) not in PNL_KEYS
            _walk_pnl(v)
    elif isinstance(obj, list):
        for v in obj:
            _walk_pnl(v)


def test_redesign_freezes_v3_no_further_precommit():
    pack = decide()
    d = pack["decision"]
    g = pack["gates"]
    assert ANALYSIS_ID == "NEW_FULL_STRATEGY_ARCHITECTURE_REDESIGN_V1"
    assert all(g.values())
    assert d["VERDICT"] == CASE_FROZEN
    assert d["NEXT"] == NEXT_IF_FROZEN == "NEW_FULL_STRATEGY_IMPLEMENTATION_AND_DEV_EVAL_V1"
    sha = d["FULL_STRATEGY_SPEC_SHA256_V3"]
    assert isinstance(sha, str) and len(sha) == 64
    assert sha == dumps_sha256(frozen_strategy())
    assert d["ANOTHER_PRECOMMIT_AFTER_PASS"] is False
    assert ANOTHER_PRECOMMIT_AFTER_PASS is False
    assert pack["timer"]["LATE_EVENT_RETROACTIVE_BAR_MUTATION"] is False
    assert pack["timer"]["REPLAY_TIMER_LIVE_SEMANTICS_EQUIVALENT"] is True
    assert pack["flatten"]["RETROSPECTIVE_BID_WALKBACK"] is False
    assert pack["flatten"]["SESSION_FLATTEN_T"] == SESSION_FLATTEN_T_LABEL
    assert pack["quote"]["CURRENT_PRICE_TIME_QUOTE_FRESHNESS"] is False
    assert pack["guards"]["ALPHA_MECHANISM_CHANGED"] is False
    assert pack["guards"]["OPERATIONAL_SESSION_RULE_CHANGED"] is True
    _walk_pnl(pack)
    a = build_answers(pack)
    assert a["45_timer_uses_ingress_arrival_causality"] is True
    assert a["46_late_pre_boundary_timestamp_can_mutate_finalized_bar"] is False
    assert a["47_historical_replay_uses_future_arriving_event_in_old_bar"] is False
    assert a["48_timer_event_precedence_defined"] is True
    assert a["49_SESSION_FLATTEN_T"] == "11:29:00 JST"
    assert a["50_pending_ENTRY_expires_at_flatten"] is True
    assert a["51_old_pending_ENTRY_can_fill_after_flatten"] is False
    assert a["52_new_signal_accepted_after_flatten"] is False
    assert a["53_retrospective_Bid_walkback"] is False
    assert a["54_session_unfilled_handling_defined"] is True
    assert a["55_duplicate_EXIT_possible"] is False
    assert a["56_alpha_mechanism_changed"] is False
    assert a["57_technical_EXIT_changed"] is False
    assert a["58_operational_session_rule_changed"] is True
    assert a["59_new_V3_hash_generated"] is True
    assert a["60_another_precommit_planned_after_PASS"] is False
    assert a["61_next_after_PASS"] == NEXT_IF_FROZEN
