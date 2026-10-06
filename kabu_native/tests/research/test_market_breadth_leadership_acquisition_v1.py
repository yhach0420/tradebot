"""MARKET_BREADTH_LEADERSHIP_ACQUISITION_V1 tests. No sendorder. No 20260911 backfill."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.futures_context_day1_effect_check_v1 import ANCHOR_HM, FEATURE_NAMES, PLACEBO_SHIFT_SEC
from research.market_breadth_leadership_acquisition_v1 import (
    DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
    DEFAULT_CADENCE_SEC,
    ENTRY,
    EXCHANGE_DIVISION,
    FALLBACK_CADENCE_SEC,
    FORBIDDEN_BACKFILL_DAYS,
    FORBIDDEN_TYPES,
    RANKING_TYPES,
)
from research.market_breadth_leadership_acquisition_v1.client import get_ranking, mutation_counts
from research.market_breadth_leadership_acquisition_v1.collector import in_primary_window, next_cadence
from research.market_breadth_leadership_acquisition_v1.derive import derive_snapshot
from research.market_breadth_leadership_acquisition_v1.probe import propose_cadence, schema_report
from research.market_breadth_leadership_acquisition_v1.safety import scan_package_source
from research.market_breadth_leadership_acquisition_v1.writer import refuse_historical_pseudosync
from research.new_causal_information_acquisition_v1.spec import standard_config_unchanged

JST = ZoneInfo("Asia/Tokyo")
PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "market_breadth_leadership_acquisition_v1"


def test_frozen_types_only():
    assert RANKING_TYPES == (1, 2, 5, 6, 7, 14, 15)
    for t in FORBIDDEN_TYPES:
        assert t not in RANKING_TYPES
    assert EXCHANGE_DIVISION == "T"
    assert ENTRY is False
    assert DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED is False
    assert "20260911" in FORBIDDEN_BACKFILL_DAYS


def test_day2_price_return_freeze_untouched():
    assert PLACEBO_SHIFT_SEC == 300
    assert len(ANCHOR_HM) == 13
    assert FEATURE_NAMES[0] == "NK_RET_30S"
    assert "NK_RET_180S" in FEATURE_NAMES
    assert FEATURE_NAMES[-1] == "COMPOSITE"


def test_no_mutation_api_in_package():
    scan = scan_package_source()
    assert scan["ok"] is True
    assert scan["register_mutation_n"] == 0
    assert scan["unregister_n"] == 0
    assert scan["sendorder_n"] == 0
    mut = mutation_counts()
    assert mut["register_mutation_n"] == 0
    assert mut["unregister_n"] == 0
    assert mut["sendorder_n"] == 0
    for p in PKG.glob("*.py"):
        if p.name == "safety.py":
            continue
        txt = p.read_text(encoding="utf-8")
        assert "send" + "order(" not in txt
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt


def test_forbidden_ranking_type_rejected():
    class _Rest:
        base_url = "http://localhost:18080/kabusapi"

    with pytest.raises(ValueError):
        get_ranking(rest=_Rest(), token="x", ranking_type=3)
    with pytest.raises(ValueError):
        get_ranking(rest=_Rest(), token="x", ranking_type=8)


def test_no_20260911_backfill():
    with pytest.raises(ValueError, match="historical reconstruct forbidden"):
        refuse_historical_pseudosync("20260911", now=datetime(2026, 9, 12, 12, 0, tzinfo=JST))


def test_historical_pseudosync_forbidden():
    with pytest.raises(ValueError, match="pseudo-sync forbidden"):
        refuse_historical_pseudosync("20260914", now=datetime(2026, 9, 12, 12, 0, tzinfo=JST))


def test_derive_equity_only_robust_not_raw_mean():
    raw = derive_snapshot(
        by_type={
            1: {
                "Ranking": [
                    {"No": 1, "Symbol": "A", "ChangePercentage": 10.0, "ExchangeName": "東証プ"},
                    {"No": 2, "Symbol": "B", "ChangePercentage": 8.0, "ExchangeName": "東証ス"},
                ]
            },
            2: {"Ranking": [{"No": 1, "Symbol": "C", "ChangePercentage": -9.0, "ExchangeName": "東証グ"}]},
            5: {
                "Ranking": [
                    {"UpCount": 5, "DownCount": 1, "ExchangeName": "東証プ"},
                    {"UpCount": 2, "DownCount": 4, "ExchangeName": "東証ス"},
                ]
            },
            6: {
                "Ranking": [
                    {
                        "No": 1,
                        "Symbol": "1306",
                        "RapidTradePercentage": 1_000_000.0,
                        "ChangePercentage": 1.0,
                        "ExchangeName": "東証ETF/ETN",
                    },
                    {
                        "No": 2,
                        "Symbol": "A",
                        "RapidTradePercentage": 50.0,
                        "ChangePercentage": 2.0,
                        "ExchangeName": "東証プ",
                    },
                ]
            },
            7: {
                "Ranking": [
                    {
                        "No": 1,
                        "Symbol": "A",
                        "RapidPaymentPercentage": 20.0,
                        "ChangePercentage": 1.0,
                        "ExchangeName": "東証プ",
                    }
                ]
            },
            14: {"Ranking": [{"Category": "001", "CategoryName": "X", "ChangePercentage": 3.0}]},
            15: {"Ranking": [{"Category": "001", "CategoryName": "X", "ChangePercentage": 3.0}]},
        }
    )
    assert raw["kind"] == "LEADERSHIP_SURGE_SECTOR_STATE"
    assert raw["view"] == "EQUITY_ONLY"
    assert raw["not"] == "ADVANCE_DECLINE_RATIO"
    assert "VOLUME_SURGE_BREADTH" not in raw
    assert "VALUE_SURGE_BREADTH" not in raw
    assert "SECTOR_BREADTH_ASYMMETRY" not in raw
    assert raw["VOLUME_SURGE_STATE"]["MEDIAN_SURGE"] == 50.0
    assert raw["VOLUME_SURGE_STATE"]["EQUITY_TOP50_N"] == 1
    assert raw["nonprimary_raw"]["TYPE6_RAW_MEAN"] > 1000
    assert raw["nonprimary_raw"]["forbidden_as_feature"] is True
    assert raw["TICK_NORM_MEDIAN"] == pytest.approx((4 / 6 + (-2 / 6)) / 2)
    assert raw["SECTOR"]["universe"] == "ONE_33_SECTOR_UNIVERSE"


def test_schema_empty_ranking_ok_on_weekend():
    got = schema_report(1, {"Type": 1, "ExchangeDivision": "T", "Ranking": []})
    assert got["ok"] is True
    assert got["empty"] is True


def test_cadence_drops_on_429():
    ok = propose_cadence(ranking_rows=[], http_429_n=0)
    assert ok["cadence_sec"] == DEFAULT_CADENCE_SEC
    drop = propose_cadence(ranking_rows=[], http_429_n=1)
    assert drop["cadence_sec"] == FALLBACK_CADENCE_SEC
    assert next_cadence(60, saw_429=True) == FALLBACK_CADENCE_SEC
    assert next_cadence(120, saw_429=False) == FALLBACK_CADENCE_SEC


def test_primary_window():
    inside = datetime(2026, 9, 14, 9, 5, tzinfo=JST)
    outside = datetime(2026, 9, 14, 8, 44, tzinfo=JST)
    assert in_primary_window(inside) is True
    assert in_primary_window(outside) is False


def test_standard_paper_50_unchanged():
    std = standard_config_unchanged()
    assert std["ok"] is True
    assert std["CORE_SLOTS"] == 10
    assert std["DYNAMIC_SLOTS"] == 40
    assert std["TOTAL_SLOTS"] == 50
