"""Tests for peer-propagation mechanism RCA. No strategy. No P/threshold/MA retune."""
from __future__ import annotations

import inspect

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1.freeze import freeze_sha256
from research.peer_propagation_mechanism_rca_v1 import (
    CASE_CONSUMED,
    CASE_EXECUTABLE,
    CASE_MARKET,
    CASE_PARTIAL,
    CASE_PERSIST,
    EXPECTED_FREEZE_SHA256,
    MA_PERIOD_TUNED,
    NEXT_PLAYBOOK,
    NEXT_STOP,
    PARENT_VERDICT,
    PNL_OPTIMIZATION,
    SMA_PERIODS,
    THRESHOLD_RETUNED,
)
from research.peer_propagation_mechanism_rca_v1.analyze import build_report_body, decide
from research.peer_propagation_mechanism_rca_v1.episodes import episode_stream
from research.peer_propagation_mechanism_rca_v1.execute import execution_path
from research.peer_propagation_mechanism_rca_v1.isolation import OUT, write_overlap_n
from research.peer_propagation_mechanism_rca_v1.ma import aligned_reclaim, rolling_sma
from research.peer_propagation_mechanism_rca_v1.publish import SHEET_ORDER
from research.peer_propagation_mechanism_rca_v1.spec import source_sha256
from research.peer_propagation_mechanism_rca_v1.walk import emit_rca


def test_frozen_parent_and_sma_periods():
    assert PARENT_VERDICT == "PEER_PROPAGATION_MECHANISM_FOUND_V1"
    assert EXPECTED_FREEZE_SHA256 == "02d901e8c3f34c0e53774f2a7640662315f7ab60560a63b1beef671497bf1296"
    assert SMA_PERIODS == (5, 25, 75)
    assert MA_PERIOD_TUNED is False
    assert THRESHOLD_RETUNED is False
    assert PNL_OPTIMIZATION is False
    assert source_sha256()
    assert len(source_sha256()) == 64


def test_episode_false_to_true_no_lookahead():
    seq = [False, True, True, True, False, True]
    out = episode_stream(seq)
    assert out["onsets"] == [1, 5]
    assert out["durations"][1] == 3
    assert out["durations"][5] == 1
    assert out["qualified_n"] == 4
    assert out["FUTURE_EPISODE_SELECTION_N"] == 0
    src = inspect.getsource(episode_stream)
    assert "max(" not in src.split("for i, now")[1]


def test_next_open_not_same_bar_close():
    rec = {
        "t": ["10:00", "10:01", "10:02", "10:03"],
        "o": np.array([100.0, 101.0, 102.0, 103.0]),
        "h": np.array([100.0, 102.0, 103.0, 104.0]),
        "l": np.array([100.0, 100.5, 101.0, 102.0]),
        "c": np.array([100.0, 101.5, 102.5, 103.5]),
        "n": 4,
    }
    path = execution_path(rec, [0, 1, 2, 3], 0, 1)
    assert path["same_bar_outcome"] is False
    assert path["entry_ok"] is True
    assert abs(float(path["next_open"]) - 101.0) < 1e-12
    assert abs(float(path["signal_close"]) - 100.0) < 1e-12
    assert path["xo_ret_1m_bps"] is not None
    assert abs(float(path["xo_ret_1m_bps"]) - (101.5 / 101.0 - 1.0) * 1e4) < 1e-6


def test_sma_reclaim_causal_and_fixed_periods():
    x = np.arange(10, dtype=float)
    s = rolling_sma(x, 5)
    assert np.isnan(s[3])
    assert abs(float(s[4]) - 2.0) < 1e-12
    assert aligned_reclaim(10.0, 10.5, 11.0, 10.8, 1) is True
    assert aligned_reclaim(11.0, 10.5, 10.0, 10.2, 1) is False
    src = inspect.getsource(emit_rca) + inspect.getsource(build_report_body)
    assert "SMA7" not in src
    assert "EMA" not in src
    assert "period optimization" not in src.lower()


def test_no_strategy_or_p_repair():
    src = inspect.getsource(emit_rca) + inspect.getsource(build_report_body) + inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    assert "RandomForest" not in src
    assert "MACD" not in src
    assert "Bollinger" not in src
    assert "freeze_boundaries" not in src
    for date in ("20251127", "20260422", "20260911"):
        assert date not in inspect.getsource(emit_rca)


def test_verdicts_and_sheets():
    assert CASE_EXECUTABLE == "PEER_PROPAGATION_EXECUTABLE_MECHANISM_SUPPORTED_V1"
    assert CASE_CONSUMED == "PEER_PROPAGATION_REAL_BUT_EDGE_CONSUMED_V1"
    assert CASE_PERSIST == "PEER_PROPAGATION_STATE_PERSISTENCE_ARTIFACT_V1"
    assert CASE_MARKET == "PEER_PROPAGATION_MARKET_WIDE_NOT_SECTOR_SPECIFIC_V1"
    assert CASE_PARTIAL == "PEER_PROPAGATION_RCA_PARTIAL_V1"
    assert NEXT_PLAYBOOK == "PEER_PROPAGATION_DAYTRADE_PLAYBOOK_DESIGN_V1"
    assert NEXT_STOP == "STOP_CROSS_SECTIONAL_PEER_PROPAGATION_V1"
    assert SHEET_ORDER[0] == "Binding"
    assert "Episode_Onset" in SHEET_ORDER
    assert "Execution_Latency" in SHEET_ORDER
    assert "Playbook_Map" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER
    d = decide({"onset_ok": True, "persistence_artifact": False, "next_open_ok": True, "consumed_most": False, "market_wide": False, "sector_specific": True})
    assert d["VERDICT"] == CASE_EXECUTABLE
    assert d["NEXT"] == NEXT_PLAYBOOK
    d2 = decide({"onset_ok": True, "persistence_artifact": False, "next_open_ok": False, "consumed_most": True, "market_wide": False, "sector_specific": True})
    assert d2["VERDICT"] == CASE_CONSUMED
    assert d2["NEXT"] == NEXT_STOP
    d3 = decide({"onset_ok": False, "persistence_artifact": True, "next_open_ok": False, "consumed_most": False, "market_wide": False, "sector_specific": False})
    assert d3["VERDICT"] == CASE_PERSIST
    assert write_overlap_n("", "") == 0
    assert "peer_propagation_mechanism_rca_v1" in str(OUT).replace("\\", "/")
    rec = {"ok": True, "high": {"a": 1}, "d1_only": True, "d2_not_read": True}
    assert freeze_sha256(rec)


def test_ma_not_in_peer_flags():
    from research.cross_sectional_peer_propagation_discovery_v1.features import p_flags

    src = inspect.getsource(p_flags)
    assert "sma" not in src.lower()
    assert "vwap" not in src.lower()
