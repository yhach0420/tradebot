"""Day1 confirmed-entry freeze tests. No sendorder. Day2 primary unchanged."""
from __future__ import annotations

from pathlib import Path

from research.futures_reversal_confirmed_entry_day1_v1 import (
    ARM_EXPIRY_SEC,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    DAY2_PRIMARY,
    DAY2_PRIMARY_UNCHANGED,
    ENTRY,
    EXIT,
    ORIGINAL_FIVE,
    SELECTOR,
    TERCILE_N,
    USE_PRECURSORS,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
    VERDICT_D,
)
from research.futures_reversal_confirmed_entry_day1_v1.analyze import _classify
from research.futures_reversal_confirmed_entry_day1_v1.engine import _episodes, tercile_three
from research.futures_reversal_precursor_day1_v1.engine import minute_grid


PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_reversal_confirmed_entry_day1_v1"


def test_freeze_surface():
    assert SELECTOR == "OBSERVED_TRADE_N_180S"
    assert ARM_EXPIRY_SEC == 180
    assert TERCILE_N == 16
    assert ORIGINAL_FIVE == ("09:20", "09:40", "10:20", "10:30", "10:40")
    assert ENTRY is False
    assert EXIT is False
    assert USE_PRECURSORS is False
    assert DAY2_PRIMARY_UNCHANGED is True
    assert DAY2_PRIMARY["substitutions_allowed"] is False
    assert DAY2_PRIMARY["selector"] == "OBSERVED_TRADE_N_180S"
    assert DAY2_PRIMARY["horizon"] == "10m"
    assert DAY2_PRIMARY["direction"] == "LONG TOP16"


def test_minute_grid_unchanged():
    g = minute_grid("20260911")
    assert len(g) == 126
    assert g[0].hour == 9 and g[0].minute == 5
    assert g[-1].hour == 11 and g[-1].minute == 10


def test_one_episode_from_consecutive_both_down():
    rows = [
        (100.0, "BOTH_DOWN"),
        (160.0, "BOTH_DOWN"),
        (220.0, "BOTH_UP"),
        (280.0, "BOTH_DOWN"),
        (340.0, "MIXED"),
    ]
    eps = _episodes(rows)
    assert len(eps) == 2
    assert eps[0]["n_minutes"] == 2
    assert eps[1]["n_minutes"] == 1
    assert eps[0]["arm_t"] == 100.0


def test_tercile_three_freeze_16():
    rows = [{"symbol": f"S{i:02d}", "value": float(48 - i)} for i in range(48)]
    split = tercile_three(rows)
    assert split is not None
    assert len(split["TOP"]) == 16
    assert len(split["MIDDLE"]) == 16
    assert len(split["BOTTOM"]) == 16
    assert split["TOP"][0] == "S00"
    assert split["BOTTOM"][-1] == "S47"


def test_ten30_loser_is_case_d_not_case_a():
    events = [
        {"TOP": {"10m": {"LONG_mean": 74.0}}, "BOTTOM": {"10m": {"LONG_mean": -160.0}}},
        {"TOP": {"10m": {"LONG_mean": -104.0}}, "BOTTOM": {"10m": {"LONG_mean": -138.0}}},
    ]
    traces = {
        "09:20": {"status": "TRIGGER", "TOP_LONG_10m": 74.0},
        "10:20": {"status": "EXPIRED", "TOP_LONG_10m": None},
        "10:30": {"status": "TRIGGER", "TOP_LONG_10m": -104.0},
    }
    case, verdict, nxt, flags = _classify(events, traces)
    assert case == CASE_D
    assert verdict == VERDICT_D
    assert flags["ten30_triggered_negative"] is True
    assert flags["original_0920_1020_survive"] is False
    assert nxt.startswith("NEED_ADDITIONAL")


def test_verdicts_and_no_retune():
    assert VERDICT_A.endswith("ENTRY_PLAUSIBLE_DAY1_V1")
    assert VERDICT_B.endswith("RELATIVE_ONLY_DAY1_V1")
    assert VERDICT_C.endswith("ALREADY_PRICED_DAY1_V1")
    assert VERDICT_D.endswith("NOT_SUFFICIENT_DAY1_V1")
    assert CASE_A == "A" and CASE_B == "B" and CASE_C == "C" and CASE_D == "D"
    for p in PKG.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt
        assert "grid_search" not in txt
        assert "market_breadth" not in txt
        assert "NK_L1_IMB" not in txt
        if p.name == "engine.py":
            assert "precursors(" not in txt
            assert "P1" not in txt
            assert "P2" not in txt
            assert "C1" not in txt
            assert "Top8" not in txt
            assert "Top12" not in txt
            assert "Top20" not in txt
