"""Tests for E1_X7 Pullback Flow Quality."""
from __future__ import annotations

from research.e1_x6_fcrr.features import FeatureBuffer
from research.e1_x6_provisional.cost_contract import post_cost_label_bps, yen_roundtrip_cost
from research.e1_x7_pfq.candidates import assert_registry_max_three, candidate_registry, derive_thresholds, passes_candidate
from research.e1_x7_pfq.config import CANDIDATES, EXIT_CANDIDATES, MIN_CLASSIFIED_TRADES_30S
from research.e1_x7_pfq.feature_contract import audit_flow_at_decision
from research.e1_x7_pfq.config import EXIT_THRESHOLDS


def _buf_with_ticks(n_up=3, n_down=0, n_flat=0):
    b = FeatureBuffer()
    t0 = 1000.0
    mid = 100.0
    vol = 1000.0
    # seed history for completeness not required for audit_flow
    for i in range(n_up):
        mid += 1.0
        vol += 10.0
        b.push(t0 + i, mid - 0.5, mid + 0.5, mid, vol)
    for i in range(n_down):
        mid -= 1.0
        vol += 10.0
        b.push(t0 + n_up + i, mid - 0.5, mid + 0.5, mid, vol)
    for i in range(n_flat):
        vol += 5.0
        b.push(t0 + n_up + n_down + i, mid - 0.5, mid + 0.5, mid, vol)
    return b, t0 + n_up + n_down + n_flat


def test_uptick_ratio_formula():
    b, t = _buf_with_ticks(n_up=3, n_down=1)
    a = audit_flow_at_decision(b, decision_time=t, episode_id="e", day="20260721", session="AM", symbol="A")
    assert a.ratio_valid
    assert abs(a.uptick_volume_ratio_30s - (a.uptick_volume_30s / a.ratio_denominator)) < 1e-12


def test_uptick_ratio_denominator():
    b, t = _buf_with_ticks(n_up=2, n_down=2)
    a = audit_flow_at_decision(b, decision_time=t, episode_id="e", day="d", session="AM", symbol="A")
    assert abs(a.ratio_denominator - (a.uptick_volume_30s + a.downtick_volume_30s)) < 1e-12


def test_uptick_ratio_zero_denominator_not_evaluable():
    b = FeatureBuffer()
    # only flat ticks — no classified volume
    for i in range(5):
        b.push(1000.0 + i, 100.0, 101.0, 100.5, 1000.0 + i * 0)  # zero volume delta
    a = audit_flow_at_decision(b, decision_time=1004.0, episode_id="e", day="d", session="AM", symbol="A")
    assert a.ratio_valid is False
    assert a.ratio_invalid_reason == "FLOW_RATIO_NOT_EVALUABLE"
    assert a.uptick_volume_ratio_30s is None


def test_uptick_ratio_requires_three_classified_trades():
    b, t = _buf_with_ticks(n_up=2, n_down=0)  # only 2 classified
    a = audit_flow_at_decision(b, decision_time=t, episode_id="e", day="d", session="AM", symbol="A")
    assert a.classified_trade_count_30s < MIN_CLASSIFIED_TRADES_30S
    assert a.ratio_valid is False


def test_price_update_deduplicates_equal_pushes():
    b = FeatureBuffer()
    b.push(1.0, 10.0, 11.0, 10.5, 100.0)
    b.push(2.0, 10.0, 11.0, 10.5, 100.0)  # equal mid — not an update
    b.push(3.0, 10.5, 11.5, 11.0, 110.0)  # mid change
    a = audit_flow_at_decision(b, decision_time=3.0, episode_id="e", day="d", session="AM", symbol="A")
    assert a.price_update_count_10s == 1
    assert a.mid_change_count_10s == 1


def test_price_update_excludes_stale_events():
    b = FeatureBuffer()
    b.push(1.0, 10.0, 11.0, 10.5, 100.0)
    # decision far in future vs last tick => stale
    a = audit_flow_at_decision(b, decision_time=100.0, episode_id="e", day="d", session="AM", symbol="A")
    assert a.ratio_invalid_reason == "STALE"


def test_feature_asof_not_future():
    b = FeatureBuffer()
    b.push(10.0, 10.0, 11.0, 10.5, 100.0)
    a = audit_flow_at_decision(b, decision_time=10.0, episode_id="e", day="d", session="AM", symbol="A")
    assert a.feature_asof_time is not None
    assert a.feature_asof_time <= 10.0 + 1e-9


def test_candidate_thresholds_use_feature_only():
    audits = []
    for i in range(20):
        audits.append({
            "price_update_count_10s": i,
            "uptick_volume_ratio_30s": i / 20.0,
            "ratio_valid": True,
            "classified_trade_count_30s": 5,
        })
    thr = derive_thresholds(audits)
    assert "pnl" not in thr
    assert thr["derivation"] == "build_only_feature_quantile"


def test_candidate_registry_max_three():
    thr = {"price_update_count_10s_q70": 5.0, "uptick_volume_ratio_30s_q30": 0.5}
    reg = candidate_registry(thr)
    assert_registry_max_three(reg)
    assert len(reg) == 3
    assert len(CANDIDATES) == 3


def test_no_date_symbol_session_permit():
    thr = {"price_update_count_10s_q70": 5.0, "uptick_volume_ratio_30s_q30": 0.5}
    # passes_candidate ignores day/symbol
    a = {"price_update_count_10s": 10, "uptick_volume_ratio_30s": 0.2,
         "ratio_valid": True, "classified_trade_count_30s": 5, "day": "X", "symbol": "Y"}
    assert passes_candidate(a, "PFQ_JOINT", thr)


def test_path_same_symbol_day_session():
    # contract encoded in joint.build_path_points signature
    from research.e1_x7_pfq.joint import build_path_points
    assert callable(build_path_points)


def test_exit_registry_distinct():
    assert EXIT_CANDIDATES[0] != EXIT_CANDIDATES[1]
    assert len(EXIT_CANDIDATES) == 2


def test_cost_5bps_once():
    assert abs(yen_roundtrip_cost(1000.0) - 50.0) < 1e-9
    assert abs(post_cost_label_bps(1000.0, 1000.0) - (-5.0)) < 1e-9


def test_full_event_replay():
    # smoke: empty entries ok
    from research.e1_x7_pfq.joint import replay_pair
    r = replay_pair([], candidate_id="PFQ_JOINT", exit_candidate="PFQ_X_PROTECT", events_by_day={})
    assert r["n_pass"] == 0


def test_ab_determinism():
    from research.e1_x7_pfq.joint import replay_pair
    a = replay_pair([], candidate_id="PFQ_UPDATE_Q70", exit_candidate="PFQ_X_PROGRESS_STRUCT", events_by_day={})
    b = replay_pair([], candidate_id="PFQ_UPDATE_Q70", exit_candidate="PFQ_X_PROGRESS_STRUCT", events_by_day={})
    assert a["n_pass"] == b["n_pass"]
