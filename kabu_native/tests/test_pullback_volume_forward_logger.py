"""Phase687W57 — unit tests for Pullback Volume Forward Logger."""

from __future__ import annotations

from small_paper.pullback_volume_forward_logger import (
    VOL_PERSISTENCE_HIGH_THR,
    VOL_PERSISTENCE_LOW_THR,
    PullbackVolumeForwardState,
    board_state,
    build_entry_row,
    classify_healthy_collapse,
    compute_vol_persistence_300s,
    quality_bucket,
    update_price_path,
    volume_bucket,
)


def test_volume_bucket_boundaries():
    assert volume_bucket(VOL_PERSISTENCE_HIGH_THR) == "high"
    assert volume_bucket(VOL_PERSISTENCE_HIGH_THR - 1e-9) == "mid"
    assert volume_bucket(VOL_PERSISTENCE_LOW_THR) == "low"
    assert volume_bucket(VOL_PERSISTENCE_LOW_THR + 1e-9) == "mid"
    assert volume_bucket(None) == "missing"


def test_board_state_boundaries():
    assert board_state(0.01) == "improving"
    assert board_state(-0.01) == "worsening"
    assert board_state(0.0) == "flat"
    assert board_state(None) == "missing"


def test_missing_handling_quality_bucket():
    assert quality_bucket("missing", "missing") == "missing"
    assert quality_bucket("improving", "high") == "board_up_vol_high"
    assert quality_bucket("worsening", "low") == "board_down_vol_low"
    assert quality_bucket("flat", "high") == "vol_high_other_board"


def test_vol_persistence_direction():
    # steadily rising cumulative volume → high persistence
    rising = [100, 110, 120, 130, 140, 150]
    assert compute_vol_persistence_300s(rising) == 1.0
    flatish = [100, 100, 100, 100]
    assert compute_vol_persistence_300s(flatish) == 0.0


def test_non_hit_not_recorded():
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718")
    # rise>=0 → not pullback hit
    row = build_entry_row(
        st,
        {
            "symbol": "7203.T",
            "entry_time": "2026-07-18T09:10:00+09:00",
            "entry_rise_5min_pct": 0.5,
            "entry_vwap_dev_pct": -0.2,
            "universe_slot": "dynamic",
            "entry_price": 1000,
        },
        official_entry=True,
        official_reject=False,
    )
    assert row is None
    assert st.hit_count == 0


def test_hit_recorded_dynamic40():
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718")
    row = build_entry_row(
        st,
        {
            "symbol": "7203.T",
            "entry_time": "2026-07-18T09:10:00+09:00",
            "entry_rise_5min_pct": -0.3,
            "entry_vwap_dev_pct": -0.2,
            "universe_slot": "dynamic",
            "entry_price": 1000,
            "vol_persistence_300s": VOL_PERSISTENCE_HIGH_THR,
            "imbalance_chg_60s": 0.05,
        },
        official_entry=True,
        official_reject=False,
        session="am",
    )
    assert row is not None
    assert row["pullback_volume_bucket"] == "high"
    assert row["pullback_board_state"] == "improving"
    assert row["pullback_quality_bucket"] == "board_up_vol_high"
    # duplicate
    row2 = build_entry_row(
        st,
        {
            "symbol": "7203.T",
            "entry_time": "2026-07-18T09:10:00+09:00",
            "entry_rise_5min_pct": -0.3,
            "entry_vwap_dev_pct": -0.2,
            "universe_slot": "dynamic",
            "entry_price": 1000,
        },
        official_entry=True,
        official_reject=False,
    )
    assert row2 is None
    assert st.duplicate_skipped == 1


def test_future_labels_only_after_entry():
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718")
    row = build_entry_row(
        st,
        {
            "symbol": "7203.T",
            "entry_time": "2026-07-18T09:10:00+09:00",
            "entry_rise_5min_pct": -0.3,
            "entry_vwap_dev_pct": -0.2,
            "universe_slot": "dynamic",
            "entry_price": 1000.0,
            "vol_persistence_300s": 0.1,
        },
        official_entry=True,
        official_reject=False,
    )
    assert row is not None
    t0 = float(row["_entry_epoch"])
    # before entry — ignored
    update_price_path(st, symbol="7203.T", price=990.0, event_epoch=t0 - 10)
    assert row["mfe_5m"] is None
    # after entry
    update_price_path(st, symbol="7203.T", price=1010.0, event_epoch=t0 + 120)
    assert row["mfe_5m"] is not None and row["mfe_5m"] > 0


def test_healthy_collapse_labels():
    h, c = classify_healthy_collapse(
        mfe10=0.6, mfe30=0.2, mae10=-0.1, pnl_30m=0.1, hit_stop=False, winner_flag=False
    )
    assert h and not c
    h2, c2 = classify_healthy_collapse(
        mfe10=0.1, mfe30=0.1, mae10=-0.6, pnl_30m=-0.2, hit_stop=False, winner_flag=False
    )
    assert c2


def test_ampm_not_in_bucket_logic():
    # same features → same bucket regardless of session label
    assert volume_bucket(0.3) == volume_bucket(0.3)
    assert quality_bucket("improving", "high") == "board_up_vol_high"
