"""DAY2_FUTURES_PLUS_MARKET_BREADTH_ACQUISITION_V1 tests. No sendorder. No weekend leftover research."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.day2_futures_plus_market_breadth_acquisition_v1 import (
    CASE_LIVE_READY,
    DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
    ENTRY,
    EXIT,
    NEXT_RUN,
)
from research.day2_futures_plus_market_breadth_acquisition_v1.coexistence import refuse_research_outcome
from research.futures_context_day1_effect_check_v1 import ANCHOR_HM, FEATURE_NAMES, PLACEBO_SHIFT_SEC
from research.market_breadth_leadership_acquisition_v1.collector import next_cadence
from research.market_breadth_leadership_acquisition_v1.derive import classify_item, derive_snapshot
from research.market_breadth_leadership_acquisition_v1.safety import scan_package_source
from research.market_breadth_leadership_acquisition_v1.writer import refuse_historical_pseudosync

JST = ZoneInfo("Asia/Tokyo")
PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "day2_futures_plus_market_breadth_acquisition_v1"


def test_day2_futures_freeze_untouched():
    assert PLACEBO_SHIFT_SEC == 300
    assert len(ANCHOR_HM) == 13
    assert FEATURE_NAMES == (
        "NK_RET_30S",
        "NK_RET_60S",
        "NK_RET_180S",
        "TOPIX_RET_30S",
        "TOPIX_RET_60S",
        "TOPIX_RET_180S",
        "AGREEMENT",
        "COMPOSITE",
    )
    assert DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED is False
    assert ENTRY is False
    assert EXIT is False


def test_weekend_probe_not_research_outcome():
    with pytest.raises(ValueError, match="schema proof only"):
        refuse_research_outcome("20260912")
    with pytest.raises(ValueError, match="historical reconstruct forbidden"):
        refuse_historical_pseudosync("20260911", now=datetime(2026, 9, 14, 10, 0, tzinfo=JST))


def test_equity_only_excludes_etf_keeps_supervised_flag():
    etf = classify_item({"ExchangeName": "東証ETF/ETN"})
    eq = classify_item({"ExchangeName": "東証プ"})
    sup = classify_item({"ExchangeName": "東証監理"})
    seiri = classify_item({"ExchangeName": "東証整理"})
    assert etf["is_etf_etn"] is True
    assert etf["equity_only"] is False
    assert eq["equity_only"] is True
    assert sup["is_supervised"] is True
    assert sup["equity_only"] is True
    assert seiri["is_reorg"] is True
    assert seiri["equity_only"] is True


def test_type6_median_not_dominated_by_etf_tail():
    got = derive_snapshot(
        by_type={
            6: {
                "Ranking": [
                    {"No": 1, "Symbol": "E", "RapidTradePercentage": 1_000_000.0, "ChangePercentage": 0.0, "ExchangeName": "東証ETF/ETN"},
                    {"No": 2, "Symbol": "A", "RapidTradePercentage": 50.0, "ChangePercentage": 1.0, "ExchangeName": "東証プ"},
                    {"No": 3, "Symbol": "B", "RapidTradePercentage": 40.0, "ChangePercentage": -1.0, "ExchangeName": "東証ス"},
                ]
            }
        }
    )
    assert got["VOLUME_SURGE_STATE"]["MEDIAN_SURGE"] == 45.0
    assert got["VOLUME_SURGE_STATE"]["EQUITY_TOP50_N"] == 2
    assert got["nonprimary_raw"]["TYPE6_RAW_MEAN"] > 100_000


def test_persistence_and_new_entrants():
    prev = {"6": {"Ranking": [{"Symbol": "A", "RapidTradePercentage": 10.0, "ExchangeName": "東証プ"}]}}
    curr = {
        6: {
            "Ranking": [
                {"Symbol": "A", "RapidTradePercentage": 12.0, "ChangePercentage": 1.0, "ExchangeName": "東証プ"},
                {"Symbol": "B", "RapidTradePercentage": 8.0, "ChangePercentage": 2.0, "ExchangeName": "東証ス"},
            ]
        }
    }
    got = derive_snapshot(by_type=curr, prev_by_type={6: prev["6"]})
    assert got["VOLUME_SURGE_STATE"]["NEW_ENTRANT_N"] == 1
    assert got["VOLUME_SURGE_STATE"]["RANK_PERSISTENCE"] == pytest.approx(0.5)


def test_sector_one_universe_has_dispersion():
    t14 = {
        "Ranking": [
            {"No": 1, "Category": "001", "ChangePercentage": 4.0},
            {"No": 2, "Category": "002", "ChangePercentage": -2.0},
            {"No": 3, "Category": "003", "ChangePercentage": 0.0},
        ]
    }
    t15 = {
        "Ranking": [
            {"No": 1, "Category": "002", "ChangePercentage": -2.0},
            {"No": 2, "Category": "003", "ChangePercentage": 0.0},
            {"No": 3, "Category": "001", "ChangePercentage": 4.0},
        ]
    }
    got = derive_snapshot(by_type={14: t14, 15: t15}, prev_by_type={14: t14, 15: t15})
    sec = got["SECTOR"]
    assert sec["universe"] == "ONE_33_SECTOR_UNIVERSE"
    assert sec["sector_positive_n"] == 1
    assert sec["sector_negative_n"] == 1
    assert sec["sector_dispersion"] is not None
    assert sec["sector_leadership_persistence"] == 1.0
    assert sec["sector_rank_turnover"] == 0.0
    assert "SECTOR_BREADTH_ASYMMETRY" not in got


def test_fail_soft_cadence():
    assert next_cadence(60, saw_429=False) == 60
    assert next_cadence(60, saw_429=True) == 120
    assert next_cadence(120, saw_429=False) == 120


def test_type5_normalized_primary():
    got = derive_snapshot(
        by_type={
            5: {
                "Ranking": [
                    {"UpCount": 9, "DownCount": 1, "ExchangeName": "東証プ"},
                    {"UpCount": 1000, "DownCount": 1000, "ExchangeName": "東証ス"},
                ]
            }
        }
    )
    assert got["TICK_NORM_MEDIAN"] == pytest.approx((0.8 + 0.0) / 2)
    assert got["TICK_DIRECTION_PRESSURE_SUM"] == pytest.approx(8.0)


def test_no_order_api_in_new_package():
    scan = scan_package_source()
    assert scan["ok"] is True
    for p in PKG.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "send" + "order(" not in txt
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt


def test_expected_verdict_constants():
    assert CASE_LIVE_READY == "MARKET_BREADTH_LEADERSHIP_LIVE_READY_V2"
    assert NEXT_RUN == "RUN_DAY2_FUTURES_PLUS_FIRST_LIVE_BREADTH_V1"
