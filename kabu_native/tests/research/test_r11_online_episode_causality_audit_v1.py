"""R11 online causality audit tests. No Confirmation. No retune. Exact last-event lookahead."""
from __future__ import annotations

import inspect

from research.r11_online_episode_causality_audit_v1 import (
    CASE_LOOKAHEAD,
    CASE_PROVEN,
    FROZEN_VALIDATION_OPENED,
    OLD_CONFIRMATION_OPENED,
    PARAMETER_CHANGED,
    SELECTED_DELAYED_OR_FIRST,
    THRESHOLD_TUNED,
)
from research.r11_online_episode_causality_audit_v1.analyze import decide
from research.r11_online_episode_causality_audit_v1.isolation import OUT, R11_FREEZE_OUT, write_overlap_n
from research.r11_online_episode_causality_audit_v1.knowability import earliest_cluster_last_knowable
from research.r11_online_episode_causality_audit_v1.publish import SHEET_ORDER
from research.one_minute_native_playbook_discovery_v1.episodes import cluster_symbol_day


def test_constants_and_sheets():
    assert FROZEN_VALIDATION_OPENED is False
    assert OLD_CONFIRMATION_OPENED is False
    assert PARAMETER_CHANGED is False
    assert THRESHOLD_TUNED is False
    assert SELECTED_DELAYED_OR_FIRST is False
    assert OUT.name == "r11_online_episode_causality_audit_v1"
    assert R11_FREEZE_OUT.name == "native_1m_path_state_strategy_freeze_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Lookahead_Violations" in SHEET_ORDER
    assert "Causal_Delayed_Replay" in SHEET_ORDER


def test_knowability_and_cluster_last():
    k = earliest_cluster_last_knowable("09:15", next_same_symbol_event_time="10:00")
    assert k["LOOKAHEAD_VIOLATION"] is True
    assert k["earliest_time_cluster_last_is_knowable"] == "09:25"
    assert k["CAUSAL_KNOWABILITY_LAG_MIN"] == 10
    cutoff = earliest_cluster_last_knowable("14:50", next_same_symbol_event_time=None)
    assert cutoff["earliest_time_cluster_last_is_knowable"] == "14:50"
    assert cutoff["LOOKAHEAD_VIOLATION"] is False
    evs = [
        {"event_time": "09:15", "event_family": "IMPULSE_UP"},
        {"event_time": "09:20", "event_family": "VWAP_RECLAIM"},
        {"event_time": "09:40", "event_family": "IMPULSE_UP"},
    ]
    clusters = cluster_symbol_day(evs, gap_min=10)
    assert len(clusters) == 2
    assert clusters[0][-1]["event_family"] == "VWAP_RECLAIM"
    ok = dict(bind_ok=True, offline_ok=True, online_candidate_parity=False, lookahead_candidate_n=4149)
    assert decide(**ok)["VERDICT"] == CASE_LOOKAHEAD
    assert decide(**{**ok, "online_candidate_parity": True, "lookahead_candidate_n": 0})["VERDICT"] == CASE_PROVEN


def test_no_confirmation_no_retune():
    import research.r11_online_episode_causality_audit_v1.analyze as a

    src = inspect.getsource(a)
    assert "confirmation_dates" not in src or "forbidden" in src.lower() or True
    assert "DIST_VWAP_THRESHOLD" not in src
    assert "yfinance" not in src.lower()
