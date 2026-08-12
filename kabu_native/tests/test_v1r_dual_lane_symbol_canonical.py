"""Regression: dual-lane canonical symbol key unifies 6098 / 6098.T."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from small_paper.v1r_live_dual_lane import (
    V1RLiveDualLane,
    canonical_symbol_key,
    reset_dual_lane_for_tests,
)


def test_canonical_symbol_key_bare_and_dot_t():
    assert canonical_symbol_key("6098") == "6098"
    assert canonical_symbol_key("6098.T") == "6098"
    assert canonical_symbol_key("6098.t") == "6098"
    assert canonical_symbol_key(canonical_symbol_key("6098.T")) == "6098"


def _snap(t: float, bid: float = 100.0, ask: float = 101.0, bq: float = 200.0, aq: float = 200.0):
    return {
        "event_time": t,
        "CurrentPriceTime": t,
        "Buy1": {"Price": bid, "Qty": bq},
        "Sell1": {"Price": ask, "Qty": aq},
        "CurrentPrice": (bid + ask) / 2.0,
        "board_age_sec": 0.0,
        "SpecialQuote": False,
        "imbalance": (bq - aq) / (bq + aq),
    }


@pytest.mark.parametrize(
    "admit_sym,tick_sym",
    [
        ("6098", "6098.T"),
        ("6098.T", "6098"),
        ("6098", "6098"),
        ("6098.T", "6098.T"),
    ],
)
def test_admit_then_tick_hits_same_primary_and_control(admit_sym, tick_sym, tmp_path: Path):
    reset_dual_lane_for_tests()
    dual = V1RLiveDualLane(trace_dir=tmp_path)
    t0 = 1_700_000_000.0
    out = dual.try_admit_fill(
        symbol=admit_sym,
        fill_price=100.0,
        fill_time=t0,
        payload=_snap(t0),
        source="v1r_native",
    )
    assert out["primary_admitted"] and out["control_admitted"]
    assert out["symbol_canonical"] == "6098"
    assert out["fill_snapshot_bound"] is True
    assert "6098" in dual.primary and "6098" in dual.control
    assert "6098.T" not in dual.primary

    exits = dual.on_tick(symbol=tick_sym, payload=_snap(t0 + 1.0), event_t=t0 + 1.0, push_sequence=1)
    assert dual.stats.tick_matches >= 2  # primary + control
    assert dual.primary["6098"].t  # board grew
    assert dual.control["6098"].t
    assert dual.open_n("primary") == 1
    assert dual.open_n("control") == 1
    # no exit yet (horizon not reached)
    assert exits == []
    events = [r["event"] for r in dual.traces]
    assert "ADMIT" in events
    assert "TICK_MATCH" in events
    assert (tmp_path / "v1r_dual_lane_trace.jsonl").is_file()
