"""Assemble live-ready V2 report. Weekend probe is not a research outcome. No strategy."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.day2_futures_plus_market_breadth_acquisition_v1 import (
    ANALYSIS_ID,
    BREADTH_LIVE_COMMAND,
    BREADTH_PID_REL,
    CASE_LIVE_READY,
    DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
    FUTURES_LIVE_COMMAND,
    FUTURES_PID_REL,
    KIND,
    NEXT_RUN,
    PARENT_ID,
)
from research.day2_futures_plus_market_breadth_acquisition_v1.coexistence import (
    coexistence_preflight,
    refuse_research_outcome,
)
from research.day2_futures_plus_market_breadth_acquisition_v1.isolation import NATIVE, PARENT_OUT
from research.futures_context_day1_effect_check_v1 import ANCHOR_HM, FEATURE_NAMES, PLACEBO_SHIFT_SEC
from research.market_breadth_leadership_acquisition_v1 import (
    DEFAULT_CADENCE_SEC,
    FALLBACK_CADENCE_SEC,
    FORBIDDEN_RESEARCH_OUTCOME_DAYS,
    RANKING_TYPES,
)
from research.market_breadth_leadership_acquisition_v1.client import mutation_counts
from research.market_breadth_leadership_acquisition_v1.collector import next_cadence
from research.market_breadth_leadership_acquisition_v1.derive import (
    FORBIDDEN_PRIMARY_KEYS,
    PRIMARY_CONTEXT_CANDIDATES,
    classify_item,
    derive_snapshot,
)
from research.market_breadth_leadership_acquisition_v1.safety import scan_package_source
from research.market_breadth_leadership_acquisition_v1.writer import refuse_historical_pseudosync
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")


def _parent_pin() -> dict[str, Any]:
    import json

    path = PARENT_OUT / "report.json"
    if not path.is_file():
        return {"ok": False, "reason": "missing_parent_report"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    d = dict(prev.get("decision") or {})
    ok = str(d.get("VERDICT") or "") == "MARKET_BREADTH_LEADERSHIP_MODE_READY_V1"
    return {
        "ok": bool(ok),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "report": str(path),
    }


def _writer_schema_frozen() -> bool:
    from research.market_breadth_leadership_acquisition_v1 import writer as wmod

    src = Path(wmod.__file__).read_text(encoding="utf-8")
    return all(
        tok in src
        for tok in ('"received_at"', '"requested_type"', '"ExchangeDivision"', '"http_status"', '"raw"')
    ) and "equity_only" not in src and "ETF" not in src


def run_live_ready_report(*, native_root: Optional[Path] = None, tests_passed: Optional[bool] = None) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    parent = _parent_pin()
    coex = coexistence_preflight(native_root=root, trading_date=datetime.now(JST).strftime("%Y%m%d"))
    scan = scan_package_source()
    mut = mutation_counts()
    orders = live_order_counts()
    fixture = derive_snapshot(
        by_type={
            1: {
                "Ranking": [
                    {"No": 1, "Symbol": "A", "ChangePercentage": 8.0, "ExchangeName": "東証プ"},
                    {"No": 2, "Symbol": "B", "ChangePercentage": 6.0, "ExchangeName": "東証ス"},
                ]
            },
            2: {"Ranking": [{"No": 1, "Symbol": "C", "ChangePercentage": -5.0, "ExchangeName": "東証グ"}]},
            5: {
                "Ranking": [
                    {"Symbol": "A", "UpCount": 8, "DownCount": 2, "ExchangeName": "東証プ"},
                    {"Symbol": "B", "UpCount": 1, "DownCount": 1, "ExchangeName": "東証ス"},
                ]
            },
            6: {
                "Ranking": [
                    {"No": 1, "Symbol": "1306", "RapidTradePercentage": 1_000_000.0, "ChangePercentage": 1.0, "ExchangeName": "東証ETF/ETN"},
                    {"No": 2, "Symbol": "A", "RapidTradePercentage": 50.0, "ChangePercentage": 2.0, "ExchangeName": "東証プ"},
                    {"No": 3, "Symbol": "B", "RapidTradePercentage": 40.0, "ChangePercentage": -1.0, "ExchangeName": "東証ス"},
                ]
            },
            7: {
                "Ranking": [
                    {"No": 1, "Symbol": "1321", "RapidPaymentPercentage": 500_000.0, "ChangePercentage": 1.0, "ExchangeName": "東証ETF/ETN"},
                    {"No": 2, "Symbol": "A", "RapidPaymentPercentage": 20.0, "ChangePercentage": 3.0, "ExchangeName": "東証プ"},
                ]
            },
            14: {
                "Ranking": [
                    {"No": 1, "Category": "001", "CategoryName": "X", "ChangePercentage": 2.0},
                    {"No": 2, "Category": "002", "CategoryName": "Y", "ChangePercentage": -1.0},
                    {"No": 3, "Category": "003", "CategoryName": "Z", "ChangePercentage": 0.5},
                ]
            },
            15: {
                "Ranking": [
                    {"No": 1, "Category": "002", "CategoryName": "Y", "ChangePercentage": -1.0},
                    {"No": 2, "Category": "003", "CategoryName": "Z", "ChangePercentage": 0.5},
                    {"No": 3, "Category": "001", "CategoryName": "X", "ChangePercentage": 2.0},
                ]
            },
        },
        prev_by_type={
            1: {"Ranking": [{"Symbol": "A", "ChangePercentage": 1.0, "ExchangeName": "東証プ"}]},
            2: {"Ranking": [{"Symbol": "Z", "ChangePercentage": -1.0, "ExchangeName": "東証ス"}]},
            6: {"Ranking": [{"Symbol": "A", "RapidTradePercentage": 10.0, "ExchangeName": "東証プ"}]},
        },
    )
    etf = classify_item({"ExchangeName": "東証ETF/ETN"})
    supervised = classify_item({"ExchangeName": "東証監理"})
    weekend_blocked = False
    try:
        refuse_research_outcome("20260912")
    except ValueError:
        weekend_blocked = True
    backfill_blocked = False
    try:
        refuse_historical_pseudosync("20260911", now=datetime(2026, 9, 14, 10, 0, tzinfo=JST))
    except ValueError:
        backfill_blocked = True

    cadence_ok = next_cadence(60, saw_429=False) == DEFAULT_CADENCE_SEC and next_cadence(60, saw_429=True) == FALLBACK_CADENCE_SEC
    primary_ok = all(k not in fixture for k in FORBIDDEN_PRIMARY_KEYS)
    vol = dict(fixture.get("VOLUME_SURGE_STATE") or {})
    val = dict(fixture.get("VALUE_SURGE_STATE") or {})
    sector = dict(fixture.get("SECTOR") or {})
    nonprimary = dict(fixture.get("nonprimary_raw") or {})
    impl_ok = (
        bool(parent.get("ok"))
        and primary_ok
        and bool(vol.get("MEDIAN_SURGE") is not None)
        and bool(nonprimary.get("forbidden_as_feature"))
        and weekend_blocked
        and backfill_blocked
        and bool(coex.get("concurrent_safe"))
        and int(mut.get("register_mutation_n") or 0) == 0
        and int(mut.get("sendorder_n") or 0) == 0
        and cadence_ok
        and _writer_schema_frozen()
        and etf["is_etf_etn"]
        and supervised["equity_only"]
        and sector.get("universe") == "ONE_33_SECTOR_UNIVERSE"
        and DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED is False
    )
    verdict = CASE_LIVE_READY if impl_ok and tests_passed is not False else "MARKET_BREADTH_LEADERSHIP_LIVE_NOT_READY_V2"
    nxt = NEXT_RUN if verdict == CASE_LIVE_READY else "FIX_LIVE_READY_GATES_V1"
    answers = {
        "1_raw_Type6_mean_removed_from_primary": "VOLUME_SURGE_BREADTH" not in fixture and nonprimary.get("forbidden_as_feature") is True,
        "2_raw_Type7_mean_removed_from_primary": "VALUE_SURGE_BREADTH" not in fixture and nonprimary.get("forbidden_as_feature") is True,
        "3_equity_only_derived_view_exists": fixture.get("view") == "EQUITY_ONLY",
        "4_ETF_ETN_raw_preserved": _writer_schema_frozen() and etf["is_etf_etn"] and not etf["equity_only"],
        "5_Type1_2_robust_metrics": {
            "UP_LEADER_MEDIAN_CHANGE": fixture.get("UP_LEADER_MEDIAN_CHANGE"),
            "DOWN_LEADER_MEDIAN_CHANGE": fixture.get("DOWN_LEADER_MEDIAN_CHANGE"),
            "UP_LEADER_TOP10_MEDIAN": fixture.get("UP_LEADER_TOP10_MEDIAN"),
            "DOWN_LEADER_TOP10_MEDIAN": fixture.get("DOWN_LEADER_TOP10_MEDIAN"),
            "UP_LEADER_EQUITY_N": fixture.get("UP_LEADER_EQUITY_N"),
            "DOWN_LEADER_EQUITY_N": fixture.get("DOWN_LEADER_EQUITY_N"),
            "NEW_UP_LEADER_N": fixture.get("NEW_UP_LEADER_N"),
            "NEW_DOWN_LEADER_N": fixture.get("NEW_DOWN_LEADER_N"),
        },
        "6_Type5_normalized_tick_metric": {
            "TICK_NORM_MEDIAN": fixture.get("TICK_NORM_MEDIAN"),
            "sum_up_minus_down": (fixture.get("TICK") or {}).get("sum_up_minus_down"),
            "formula": (fixture.get("TICK") or {}).get("formula"),
        },
        "7_Type6_robust_metrics": vol,
        "8_Type7_robust_metrics": val,
        "9_Type14_15_one_sector_universe": sector.get("universe") == "ONE_33_SECTOR_UNIVERSE" and sector.get("full_list_mean_diff_is_not_a_breadth_metric") is True,
        "10_sector_dispersion_implemented": sector.get("sector_dispersion") is not None,
        "11_rank_persistence_implemented": vol.get("RANK_PERSISTENCE") is not None,
        "12_new_entrant_metric_implemented": vol.get("NEW_ENTRANT_N") is not None and fixture.get("NEW_UP_LEADER_N") is not None,
        "13_weekend_probe_excluded_from_research": weekend_blocked and "20260912" in FORBIDDEN_RESEARCH_OUTCOME_DAYS,
        "14_concurrent_Futures_Breadth_safe": bool(coex.get("concurrent_safe")),
        "15_register_mutation": int(mut.get("register_mutation_n") or 0) + int(scan.get("register_mutation_n") or 0),
        "16_sendorder": int(mut.get("sendorder_n") or 0) + int(scan.get("sendorder_n") or 0),
        "17_tests_passed": tests_passed,
        "18_VERDICT": verdict,
        "19_NEXT": nxt,
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ID": PARENT_ID,
        "KIND": KIND,
        "clock": datetime.now(JST).isoformat(timespec="seconds"),
        "parent": parent,
        "coexistence": coex,
        "frozen_types": list(RANKING_TYPES),
        "day2_price_return_freeze": {
            "changed": False,
            "FEATURE_NAMES": list(FEATURE_NAMES),
            "PLACEBO_SHIFT_SEC": PLACEBO_SHIFT_SEC,
            "ANCHOR_CLOCK_N": len(ANCHOR_HM),
        },
        "primary_context_candidates": list(PRIMARY_CONTEXT_CANDIDATES),
        "forbidden_primary_keys": list(FORBIDDEN_PRIMARY_KEYS),
        "test_fixture_derived": fixture,
        "test_fixture_note": "SYNTHETIC only. Not 20260912 leftover close. Not a research outcome.",
        "monday_sequence": {
            "07:55": FUTURES_LIVE_COMMAND,
            "09:05": BREADTH_LIVE_COMMAND,
            "cadence_sec": DEFAULT_CADENCE_SEC,
            "fail_soft_sec": FALLBACK_CADENCE_SEC,
            "futures_pid": FUTURES_PID_REL,
            "breadth_pid": BREADTH_PID_REL,
            "breadth_window": "09:05-11:25 JST",
            "futures_window": "07:55-11:30 JST",
        },
        "transport_gate": [
            "7 ranking types present",
            "received_at advancing",
            "snapshot N",
            "empty response N/rate",
            "429 N",
            "HTTP error N",
            "schema drift N",
            "per-type first/last received_at",
        ],
        "combined_layers": ["FUTURES_CONTEXT", "MARKET_BREADTH_LEADERSHIP", "STOCK_SPECIFIC_STATE"],
        "first_tests_only": ["A_breadth_explains_market_future_move", "B_breadth_changes_stock_selector_direction"],
        "answers": answers,
        "decision": {
            "CASE": "LIVE_READY" if verdict == CASE_LIVE_READY else "NOT_READY",
            "VERDICT": verdict,
            "NEXT": nxt,
            "ENTRY": False,
            "EXIT": False,
            "STRATEGY_BUILT": False,
            "DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED": False,
            "WEEKEND_PROBE_IS_RESEARCH_OUTCOME": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        },
        "orders": orders,
        "submit_cancel_live": f"{orders.get('submit', 0)}/{orders.get('cancel', 0)}/{orders.get('live', 0)}",
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
