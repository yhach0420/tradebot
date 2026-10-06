"""Phase 0 foundation tests. No strategy. No sendorder. 20260914 unchanged."""
from __future__ import annotations

import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.fixed_universe_historical_foundation_v1 import (
    CASE_PARTIAL_EXTERNAL,
    ENTRY,
    EXIT,
    LIVE_20260914_CHANGED,
    NEXT_FREEZE,
    PAPER_CHANGED,
    RECOMMENDED_STOCK_N,
    RUNTIME_CHANGED,
    SAME_BAR_CLOSE_ENTRY_ALLOWED,
    STRATEGY_SEARCH_STARTED,
    UNIVERSE_FROZEN,
)
from research.fixed_universe_historical_foundation_v1.analyze import build_answers, build_report_body, decide
from research.fixed_universe_historical_foundation_v1.publish import SHEET_ORDER
from research.fixed_universe_historical_foundation_v1.sources import jquants_equity_minute_usable
from research.fixed_universe_historical_foundation_v1.technical import (
    feature_usable_index,
    rci_at,
    same_clock_baseline,
    sma,
)
from research.fixed_universe_historical_foundation_v1.timestamps import availability_time_jst
from research.fixed_universe_historical_foundation_v1.universe import (
    CANDIDATES,
    FORBIDDEN_SELECTION_INPUTS,
    REQUIRED_SECTOR_BUCKETS,
    candidate_rows,
    methodology,
)
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import (
    PAPER_CHANGED as LIVE_PAPER_CHANGED,
    RUNTIME_CHANGED as LIVE_RUNTIME_CHANGED,
    TRADING_DATE,
)

JST = ZoneInfo("Asia/Tokyo")


def test_live_and_safety_unchanged():
    assert TRADING_DATE == "20260914"
    assert FEATURE_MINING_CLOSED is True
    assert LIVE_20260914_CHANGED is False
    assert STRATEGY_SEARCH_STARTED is False
    assert UNIVERSE_FROZEN is False
    assert ENTRY is False
    assert EXIT is False
    assert RUNTIME_CHANGED is False
    assert PAPER_CHANGED is False
    assert LIVE_RUNTIME_CHANGED is False
    assert LIVE_PAPER_CHANGED is False
    assert SAME_BAR_CLOSE_ENTRY_ALLOWED is False


def test_universe_candidates_no_outcome():
    assert len(CANDIDATES) == int(RECOMMENDED_STOCK_N)
    m = methodology()
    assert m["APPROACH_VIABLE"] is True
    assert m["FROZEN"] is False
    assert m["CLAIM_ONE_YEAR_AGO_SAME_UNIVERSE"] is False
    assert m["PANEL_CONDITIONED_HISTORICAL_RESEARCH"] is True
    assert m["REQUIRED_SECTORS_ALL_COVERED"] is True
    assert set(REQUIRED_SECTOR_BUCKETS) <= {c[3] for c in CANDIDATES}
    rows = candidate_rows()
    assert all(r["outcome_used"] is False for r in rows)
    assert all(r["historical_pnl_used"] is False for r in rows)
    assert "historical_pnl" in FORBIDDEN_SELECTION_INPUTS


def test_verdict_partial_external_not_faked_ready():
    d = decide()
    assert d["VERDICT"] == CASE_PARTIAL_EXTERNAL
    assert d["NEXT"] == NEXT_FREEZE
    assert d["TIMESTAMP_FULLY_KNOWN"] is False
    assert d["STOCK_MINUTE_BLOCKED"] is False
    assert jquants_equity_minute_usable()["usable"] is True
    assert jquants_equity_minute_usable()["realtime_entry_source"] is False
    body = build_report_body()
    a = build_answers(body)
    assert a["23_20260914_live_plan_changed"] is False
    assert a["24_Day1_mining_reopened"] is False
    assert a["25_Runtime_changed"] is False
    assert a["26_Paper_changed"] is False
    assert a["27_submit_cancel_live"] == "0/0/0"
    assert a["28_VERDICT"] == CASE_PARTIAL_EXTERNAL
    assert a["13_timestamp_semantics_fully_known"] is False
    assert a["5_J_Quants_minute_data_usable"] is True
    assert a["22_simple_technical_library_definition_ready"] is True
    assert a["futures_1min_autosplice"] is False
    assert body["futures_historical"]["autosplice_forbidden"] is True


def test_same_bar_and_rci_causal():
    t = datetime(2026, 9, 14, 10, 30, tzinfo=JST)
    assert availability_time_jst(t).hour == 10 and availability_time_jst(t).minute == 31
    assert feature_usable_index(4) == 5
    up = np.arange(1.0, 10.0)
    assert abs(rci_at(up, 8, 9) - 100.0) < 1e-9
    s = sma(np.array([1.0, 2.0, 3.0, 10.0], dtype=float), 3)
    assert np.isnan(s[1])
    assert abs(float(s[2]) - 2.0) < 1e-12
    with pytest.raises(ValueError):
        same_clock_baseline(np.arange(1.0, 11.0), 5)


def test_no_pnl_or_grid_in_phase0_source():
    import research.fixed_universe_historical_foundation_v1.analyze as a
    import research.fixed_universe_historical_foundation_v1.__main__ as m

    src = inspect.getsource(a) + inspect.getsource(m)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src
    assert "sendorder" not in src


def test_sheet_order_matches_spec():
    assert SHEET_ORDER == (
        "UNIVERSE_CANDIDATES",
        "SECTOR_COVERAGE",
        "DATA_SOURCE_MATRIX",
        "TIMESTAMP_SEMANTICS",
        "CORPORATE_ACTIONS",
        "SESSION_CALENDAR",
        "RESEARCH_SPLIT_PLAN",
    )
    _ = pytest
