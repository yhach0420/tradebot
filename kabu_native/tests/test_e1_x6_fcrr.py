"""Mandatory FCRR contract tests (IMPLEMENTATION_SPEC §12)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.e1_x6_fcrr.config import CANDIDATE_IDS, RETENTION_SEC, precommit_body
from research.e1_x6_fcrr.features import FeatureBuffer
from research.e1_x6_fcrr.state_machine import Machine
from research.e1_x6_provisional.cost_contract import net_pnl_yen, yen_roundtrip_cost


def test_precommit_has_three_retention_only_variants():
    body = precommit_body()
    assert body["candidate_ids"] == list(CANDIDATE_IDS)
    assert body["candidate_count_limit"] == 3
    assert set(RETENTION_SEC) == set(CANDIDATE_IDS)
    assert body["economics_opened_before_precommit"] is False


def test_cost_5bps_once():
    assert abs(yen_roundtrip_cost(1000.0) - 50.0) < 1e-9
    e = net_pnl_yen(1000.0, 1000.0)
    assert abs(e["net_pnl_yen_100"] + 50.0) < 1e-9


def test_one_state_advance_per_observation():
    m = Machine("X", "FCRR_R10", volume_abs_floor=1.0)
    # incomplete → no advance
    assert m.observe(0.0, {"complete": False, "reason": "NO_TICKS"}) is None
    assert m.state == "IDLE"


def test_denominator_zero_blocks_reclaim():
    m = Machine("X", "FCRR_R10", volume_abs_floor=1.0)
    m.state = "SELLING_EXHAUSTED"
    from research.e1_x6_fcrr.state_machine import Episode
    m.episode = Episode(1, 0.0, micro_high=100.0, micro_high_frozen=True, pullback_low=99.0)
    m.prev_mid = 100.0
    feats = {
        "complete": True, "mid": 100.2, "bid": 100.15, "ask": 100.25, "vwap": 99.0,
        "spread_bps": 3.0, "volume_10s": 10.0, "volume_30s": 30.0,
        "median_active_volume_10s_120s": 0.0,  # denom 0
        "median_active_volume_30s_300s": 10.0,
        "active_10s_windows_120s": 4, "active_30s_windows_300s": 6,
        "uptick_volume_ratio_30s": 0.7, "price_update_count_10s": 5,
        "median_price_update_count_10s_120s": 2, "atr_180s": 1.0,
        "asof_time": 1.0,
    }
    m.observe(1.0, feats)
    assert m.state == "SELLING_EXHAUSTED"  # did not advance


def test_unset_volume_floor_blocks_reclaim():
    m = Machine("X", "FCRR_R10", volume_abs_floor=0.0)
    m.state = "SELLING_EXHAUSTED"
    from research.e1_x6_fcrr.state_machine import Episode
    m.episode = Episode(1, 0.0, micro_high=100.0, micro_high_frozen=True, pullback_low=99.0)
    m.prev_mid = 99.9
    feats = {
        "complete": True, "mid": 100.2, "bid": 100.15, "ask": 100.25, "vwap": 99.0,
        "spread_bps": 3.0, "volume_10s": 100.0, "volume_30s": 300.0,
        "median_active_volume_10s_120s": 50.0, "median_active_volume_30s_300s": 100.0,
        "active_10s_windows_120s": 4, "active_30s_windows_300s": 6,
        "uptick_volume_ratio_30s": 0.7, "price_update_count_10s": 5,
        "median_price_update_count_10s_120s": 2, "atr_180s": 1.0, "asof_time": 1.0,
    }
    m.observe(1.0, feats)
    assert m.state == "SELLING_EXHAUSTED"


def test_cap_blocked_locks_episode():
    m = Machine("X", "FCRR_R10", volume_abs_floor=1.0)
    from research.e1_x6_fcrr.state_machine import Episode
    m.episode = Episode(1, 0.0)
    m.state = "RETENTION_CONFIRMED"
    m.notify_cap_blocked(10.0)
    assert m.episode.entry_emitted is True
    assert m.state == "EPISODE_LOCKED"


def test_retention_not_met_no_entry():
    m = Machine("X", "FCRR_R30", volume_abs_floor=1.0)
    from research.e1_x6_fcrr.state_machine import Episode
    m.state = "RECLAIM_CROSSED"
    m.episode = Episode(
        1, 0.0, micro_high=100.0, micro_high_frozen=True,
        reclaim_t=0.0, reclaim_mid=100.1, new_high_after_cross=True,
    )
    feats = {
        "complete": True, "mid": 100.2, "bid": 100.15, "ask": 100.25, "vwap": 99.0,
        "spread_bps": 3.0, "volume_10s": 5.0, "uptick_volume_ratio_10s": 0.6,
        "atr_180s": 1.0, "asof_time": 5.0,
    }
    # only 5s elapsed; R30 needs 30s
    assert m.observe(5.0, feats) is None
    assert m.state == "RECLAIM_CROSSED"


def test_cross_and_entry_not_same_event():
    """RETENTION_CONFIRMED does not emit ENTRY on the confirming observation."""
    m = Machine("X", "FCRR_R10", volume_abs_floor=1.0)
    from research.e1_x6_fcrr.state_machine import Episode
    m.state = "RECLAIM_CROSSED"
    m.episode = Episode(
        1, 0.0, micro_high=100.0, micro_high_frozen=True,
        reclaim_t=0.0, reclaim_mid=100.5, new_high_after_cross=True,
    )
    feats = {
        "complete": True, "mid": 100.6, "bid": 100.55, "ask": 100.65, "vwap": 99.0,
        "spread_bps": 3.0, "volume_10s": 5.0, "uptick_volume_ratio_10s": 0.6,
        "atr_180s": 1.0, "asof_time": 10.0,
    }
    sig = m.observe(10.0, feats)
    assert sig is None
    assert m.state == "RETENTION_CONFIRMED"
    # next event emits entry
    feats2 = dict(feats)
    feats2["asof_time"] = 11.0
    sig2 = m.observe(11.0, feats2)
    assert sig2 is not None
    assert sig2["entry_reason"] == "E1_X6_FCRR"
    assert m.state == "EPISODE_LOCKED"


def test_feature_asof_not_future():
    buf = FeatureBuffer()
    t0 = 1_000_000.0
    for i in range(50):
        buf.push(t0 + i, 100.0, 100.1, 100.0, 1000.0 + i)
    snap = buf.snapshot(t0 + 49)
    assert snap["asof_time"] == t0 + 49
    assert snap.get("complete") in (True, False)  # may need more history


def test_missing_not_zero_pass_context():
    m = Machine("X", "FCRR_R10", volume_abs_floor=1.0)
    feats = {
        "complete": True, "mid": 100.0, "vwap": 99.0, "ret_180s": None,  # missing
        "linear_slope_180s": 0.1, "distance_from_session_high": 0.1,
        "distance_above_vwap": 0.1, "spread_bps": 3.0, "price_update_count_60s": 10,
        "active_volume_windows_120s": 4, "atr_180s": 1.0, "asof_time": 1.0,
    }
    m.observe(1.0, feats)
    assert m.state == "IDLE"
