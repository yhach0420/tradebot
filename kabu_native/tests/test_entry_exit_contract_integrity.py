"""EEC_v2 integrity unit tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research.entry_exit_contract.constants import CONTRACT_VERSION
from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract_integrity.episode import segment_true_episodes
from research.entry_exit_contract_integrity.evaluate import pairing_verdict_v2
from research.entry_exit_contract_integrity.execution import execution_ladder
from research.entry_exit_contract_integrity.metrics import mfe_capture_block, pnl_pct_5bps
from research.entry_exit_contract.exits import ExitSim
from research.price_flow_exit.path_mfe import PathBar

JST = ZoneInfo("Asia/Tokyo")


def _c(sid="EC1", t=0, bl=1000.0, setup="s", pl=990.0, rh=1002.0, rl=1000.0) -> EntryContract:
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST) + timedelta(seconds=t)
    levels = {"breakout_level": bl, "entry_price": bl + 1}
    if sid == "EC2":
        levels = {
            "pullback_low": pl,
            "reclaim_level": pl + 5,
            "pre_pullback_high": pl + 20,
            "trend_reference": pl + 20,
            "vwap": pl + 10,
            "expected_retest_level": pl + 20,
        }
    if sid == "EC3":
        levels = {"range_high": rh, "range_low": rl, "range_mid": (rh + rl) / 2, "range_width": rh - rl}
    return EntryContract(
        strategy_id=sid,
        contract_version=CONTRACT_VERSION,
        symbol="1000.T",
        day="20260722",
        session="AM",
        entry_signal_time=t0,
        entry_time=t0,
        entry_price=bl + 1 if sid == "EC1" else 1001.0,
        entry_reason="x",
        entry_feature_snapshot={},
        expected_market_path="x",
        expected_horizon_sec=90.0,
        invalidation_level=bl if sid != "EC2" else pl,
        invalidation_reason_definition="x",
        hold_condition_definition="x",
        profit_exit_definition="x",
        emergency_exit_definition="x",
        setup_id=setup,
        episode_id=f"BAD:{setup}:{t0.isoformat()}",  # intentionally has time — will be replaced
        source_quality="OK",
        quote_quality="OK",
        volume_quality="OK",
        trade_side_quality="OK",
        levels=levels,
    )


def test_episode_id_has_no_entry_time():
    xs = [_c(t=0, setup="a"), _c(t=30, setup="b", bl=1000.5)]
    seg = segment_true_episodes(xs)
    for c in seg["remapped"]:
        assert "T10:" not in c.episode_id and "2026-07-22T" not in c.episode_id


def test_same_wave_dedupe_one_entry():
    xs = [_c(t=0, setup="a"), _c(t=20, setup="b", bl=1000.1), _c(t=40, setup="c", bl=1000.2)]
    seg = segment_true_episodes(xs)
    assert seg["one_episode_one_entry_n"] == 1
    assert seg["episode_blocked_n"] == 2
    assert seg["true_episode_n"] == 1


def test_new_episode_after_gap():
    xs = [_c(t=0, setup="a"), _c(t=900, setup="b", bl=1010.0)]
    seg = segment_true_episodes(xs)
    assert seg["one_episode_one_entry_n"] == 2


def test_capture_ratio_units_percent_only():
    # yen must not be used as numerator with percent denominator
    entry, exit_px = 1000.0, 1005.0
    pct = pnl_pct_5bps(entry, exit_px)
    assert 0.4 < pct < 0.6  # ~0.5% - 0.05 = 0.45


def test_capture_not_when_mfe_nonpositive():
    c = _c()
    t0 = c.entry_time
    path = [PathBar(t0 + timedelta(seconds=i), 999.0, 998.5, 999.5, 100, 100, 10.0, -1, 0.0, 10.0) for i in range(1, 10)]
    ex = ExitSim(path[-1].t, 998.5, "x", -200.0, 9.0, True, False, None, False, True, False)
    blk = mfe_capture_block(c, path, ex)
    assert blk["capture_ratio_positive_mfe_only"] is None or blk["zero_or_negative_mfe"]


def test_no_500ms_interpolation():
    c = _c()
    t0 = c.entry_time
    # only bars at 0 and 2s — no bar within 500ms
    path = [
        PathBar(t0, 1001, 1000.5, 1001.5, 200, 100, 10.0, 1, 1.0, 8.0),
        PathBar(t0 + timedelta(seconds=2), 1001, 1000.0, 1001.5, 200, 100, 10.0, 1, 1.0, 8.0),
    ]
    ladder = execution_ladder(c, path, exit_time=t0, exit_price=1000.5)
    assert ladder["R4_mode"] == "NOT_EVALUABLE"
    assert ladder["R3_mode"] == "OBSERVED"


def test_pairing_relative_uplift_not_edge():
    m2 = {
        "total_pnl_5bps": -100,
        "PF_5bps": 0.8,
        "pos_days": 0,
        "neg_days": 3,
        "baselines": {"M0": {"total_pnl_5bps": -500, "PF_5bps": 0.4}, "M1": {"total_pnl_5bps": -200, "PF_5bps": 0.5}},
        "reality": {"R1": {"PF_5bps": 0.7}, "R3": {"PF_5bps": 0.7}},
        "dependency": {"dependency_blocked": False},
    }
    assert pairing_verdict_v2(m2, cap5_pnl=-50, oos_n=3) == "ENTRY_EXIT_PAIRING_RELATIVE_UPLIFT"


def test_pairing_no_edge_when_worse():
    m2 = {
        "total_pnl_5bps": -500,
        "PF_5bps": 0.3,
        "pos_days": 0,
        "neg_days": 3,
        "baselines": {"M0": {"total_pnl_5bps": -100, "PF_5bps": 0.8}, "M1": {"total_pnl_5bps": -100, "PF_5bps": 0.8}},
        "reality": {"R1": {"PF_5bps": 0.3}, "R3": {"PF_5bps": 0.3}},
        "dependency": {},
    }
    assert pairing_verdict_v2(m2, cap5_pnl=-50, oos_n=3) == "ENTRY_EXIT_PAIRING_NO_EDGE"
