"""Acquire official daily then retry freeze. No strategy. Fail closed without API key. 20260914 unchanged."""
from __future__ import annotations

import inspect

import numpy as np
import pytest

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.fixed_daytrade_universe_v1 import (
    CASE_CALENDAR_MISMATCH,
    CASE_FROZEN,
    CASE_KEY_REQUIRED,
    CASE_NO_VA,
    EMPTY_UNFROZEN_SHA,
    ENTRY,
    EXIT,
    LAST_COMPLETE_TSE_SESSION,
    LIVE_20260914_CHANGED,
    NEXT_PANEL,
    NEXT_SET_KEY,
    OUTCOME_USED,
    PAPER_CHANGED,
    PANEL_BUILT,
    RUNTIME_CHANGED,
    SELECTION_METHODOLOGY_VERSION,
    STRATEGY_SEARCH_STARTED,
    TARGET_N,
)
from research.fixed_daytrade_universe_v1.acquire import acquire_official_daily
from research.fixed_daytrade_universe_v1.analyze import build_answers, build_report_body, decide, universe_identity_sha
from research.fixed_daytrade_universe_v1.calendar_window import build_official_window
from research.fixed_daytrade_universe_v1.daily_source import probe_daily_source
from research.fixed_daytrade_universe_v1.liquidity import WINDOW, liquidity_pass, metrics_aligned_to_sessions, metrics_from_daily
from research.fixed_daytrade_universe_v1.operability import evaluate_operability, paper_rules
from research.fixed_daytrade_universe_v1.publish import SHEET_ORDER
from research.fixed_daytrade_universe_v1.schema import daily_schema_status, map_daily_row, symbol4
from research.fixed_universe_historical_foundation_v1.analyze import build_answers as phase0_answers
from research.fixed_universe_historical_foundation_v1.analyze import build_report_body as phase0_body
from research.fixed_universe_historical_foundation_v1.universe import CANDIDATES, EXCLUDED_LOT_SIZE_BLOCKERS
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE

SAMPLE_DAILY = {
    "Date": "2023-03-24",
    "Code": "86970",
    "O": 2047.0,
    "H": 2069.0,
    "L": 2035.0,
    "C": 2045.0,
    "UL": "0",
    "LL": "0",
    "Vo": 2202500.0,
    "Va": 4507051850.0,
    "AdjFactor": 1.0,
}


def test_phase0_autosplice_canonical_false():
    a = phase0_answers(phase0_body())
    assert a["futures_1min_autosplice"] is False
    assert a["futures_autosplice_forbidden"] is True


def test_no_key_fail_closed():
    daily = probe_daily_source()
    assert daily["available"] is False
    assert daily["yfinance_used"] is False
    acquired = acquire_official_daily()
    assert acquired["available"] is False
    assert acquired["CASE_HINT"] == CASE_KEY_REQUIRED
    assert acquired["yfinance_used"] is False
    assert acquired["kabu_board_used"] is False
    d = decide(acquired=acquired)
    assert d["VERDICT"] == CASE_KEY_REQUIRED
    assert d["NEXT"] == NEXT_SET_KEY
    assert d["universe_frozen"] is False
    assert d["did_not_guess"] is True
    body = build_report_body(phase0_fix={"ok": True, "from": True, "to": False}, acquired=acquired)
    a = build_answers(body)
    assert a["2_candidate_N"] == int(TARGET_N) == len(CANDIDATES)
    assert a["3_quantitative_liquidity_data_available"] is False
    assert a["18_any_low_liquidity_name_retained_only_for_sector_coverage"] is False
    assert a["19_outcome_PnL_used"] is False
    assert a["20_future_return_used"] is False
    assert a["21_Paper_result_used"] is False
    assert a["22_PANEL_CONDITIONED"] is True
    assert a["23_universe_frozen"] is False
    assert a["24_universe_SHA"] is None
    assert a["27_strategy_search_started"] is False
    assert a["28_panel_built"] is False
    assert a["29_20260914_live_changed"] is False
    assert a["32_submit_cancel_live"] == "0/0/0"
    assert a["33_VERDICT"] == CASE_KEY_REQUIRED
    assert "9983" in str(a["13_9983_exact_exclusion_reason"])
    assert "6861" in str(a["14_6861_exact_exclusion_reason"])
    assert a["15_same_rule_applied_to_all_candidates"] is True
    assert a["11_100_share_notional_audit_complete"] is False
    assert a["12_trading_unit_audit_complete"] is False
    assert a["operability_rule_defined"] is True
    assert a["operability_audit_complete"] is False
    assert a["applied_this_run"] is False
    assert body["operability_rules"]["applied_this_run"] is False
    assert "9983" not in {c[0] for c in CANDIDATES}
    assert "6861" not in {c[0] for c in CANDIDATES}


def test_window_excludes_20260914_and_weekend():
    assert WINDOW["n"] == 60
    assert WINDOW["last"] == "20260911"
    assert TRADING_DATE == "20260914"
    assert FEATURE_MINING_CLOSED is True
    assert LIVE_20260914_CHANGED is False
    assert STRATEGY_SEARCH_STARTED is False
    assert PANEL_BUILT is False
    assert OUTCOME_USED is False
    assert ENTRY is False and EXIT is False
    assert RUNTIME_CHANGED is False and PAPER_CHANGED is False


def test_official_schema_mapping_no_guess():
    sch = daily_schema_status(SAMPLE_DAILY)
    assert sch["ok"] is True
    assert sch["trading_value_raw_field"] == "Va"
    row = map_daily_row(SAMPLE_DAILY)
    assert row["symbol"] == "8697"
    assert row["trading_value"] == 4507051850.0
    assert row["volume"] == 2202500.0
    missing = daily_schema_status({"Date": "2026-09-11", "Code": "72030", "O": 1, "H": 1, "L": 1, "C": 1, "Vo": 1})
    assert missing["ok"] is False
    assert missing["trading_value_field_available"] is False


def test_calendar_mismatch_and_cutoff():
    rows = [{"Date": "2026-06-18", "HolDiv": "1"}, {"Date": "2026-09-11", "HolDiv": "1"}]
    win = build_official_window(rows)
    assert win["mismatch"] is True
    assert win["verdict_if_mismatch"] == CASE_CALENDAR_MISMATCH
    cal = [{"Date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "HolDiv": "1"} for d in WINDOW["days"]]
    cal.append({"Date": "2026-09-14", "HolDiv": "1"})
    ok = build_official_window(cal)
    assert ok["ok"] is True
    assert ok["last"] == LAST_COMPLETE_TSE_SESSION
    assert "20260914" not in ok["days"]
    assert ok["max_date"] <= LAST_COMPLETE_TSE_SESSION


def test_identity_sha_not_empty_unfrozen():
    sha = universe_identity_sha(["7203", "9984"])
    assert sha != EMPTY_UNFROZEN_SHA
    assert universe_identity_sha([]) != EMPTY_UNFROZEN_SHA
    assert sha == universe_identity_sha(["9984", "7203"])
    other = universe_identity_sha(["7203"])
    assert other != sha
    _ = SELECTION_METHODOLOGY_VERSION


def test_missing_session_not_zero_volume():
    days = WINDOW["days"]
    rows = [{"date": days[0], "trading_value": 2e9, "volume": 1e6, "close": 1000}]
    m = metrics_aligned_to_sessions(session_days=days, rows=rows)
    assert m["missing_session_n"] == 59
    assert m["did_not_fill_missing_as_zero_volume"] is True
    assert m["median_daily_trading_value_60d"] == 2e9
    ok, reasons = liquidity_pass(m)
    assert ok is False
    assert "active_session_n_below_floor" in reasons


def test_liquidity_metrics_not_mean_only():
    va = [1e9] * 30 + [1e11] * 30
    m = metrics_from_daily(va=va, vo=[1e6] * 60, present_n=60, expected_n=60)
    assert m["median_daily_trading_value_60d"] is not None
    assert m["p20_daily_trading_value_60d"] is not None
    assert m["recent_surge_only_flag"] is True
    ok, reasons = liquidity_pass(m)
    assert ok is False
    assert "first30_vs_last30_surge_only" in reasons


def test_operability_same_rule_high_price():
    rules = paper_rules()
    assert rules["same_rule_all_candidates"] is True
    assert "6857" in rules["high_price_names_same_rule"]
    assert "9983" in rules["high_price_names_same_rule"]
    cheap = evaluate_operability(trading_unit=100, price=800.0)
    rich = evaluate_operability(trading_unit=100, price=40000.0)
    unknown = evaluate_operability(trading_unit=None, price=40000.0)
    assert cheap["structurally_ineligible"] is False
    assert rich["structurally_ineligible"] is False
    assert rich["temporarily_not_trade_eligible"] is True
    assert unknown["structurally_ineligible"] is False
    assert unknown["paper_eligibility"] == "PAPER_ELIGIBILITY_UNKNOWN"
    assert unknown["temporarily_not_trade_eligible"] is None
    assert cheap["same_rule"] is True and rich["same_rule"] is True
    assert rules["operability_rule_defined"] is True
    assert rules["operability_audit_complete"] is False
    assert rules["applied_this_run"] is False


def test_mocked_liquid_universe_freezes():
    days = list(WINDOW["days"])
    daily_by_symbol = {}
    master = {}
    for code, *_rest in CANDIDATES:
        daily_by_symbol[code] = [
            {
                "date": d,
                "symbol": code,
                "open": 1000.0,
                "high": 1010.0,
                "low": 990.0,
                "close": 2000.0 if code != "6857" else 40000.0,
                "volume": 1_000_000.0,
                "trading_value": 5_000_000_000.0,
            }
            for d in days
        ]
        master[code] = {
            "symbol": code,
            "prod_cat": "011",
            "common_stock_domestic": True,
            "etf": False,
            "trading_unit": None,
            "market": "0111",
            "market_name": "プライム",
            "sector33_name": "x",
        }
    acquired = {
        "available": True,
        "CASE_HINT": None,
        "window": {"days": days, "first": days[0], "last": days[-1], "n": 60, "mismatch": False},
        "daily_by_symbol": daily_by_symbol,
        "master_asof_cutoff": master,
        "master_asof_window_start": master,
        "yfinance_used": False,
        "credentials": {"present": True, "env_var_name": "JQUANTS_API_KEY"},
    }
    body = build_report_body(phase0_fix={"ok": True, "from": True, "to": False}, acquired=acquired)
    d = body["decision"]
    a = build_answers(body)
    assert d["VERDICT"] == CASE_FROZEN
    assert d["NEXT"] == NEXT_PANEL
    assert d["universe_frozen"] is True
    assert d["final_n"] == 45
    assert "9983" not in d["final_symbols"]
    assert "6861" not in d["final_symbols"]
    assert a["23_universe_frozen"] is True
    assert a["24_universe_SHA"] != EMPTY_UNFROZEN_SHA
    assert a["24_universe_SHA"] == universe_identity_sha(sorted(d["final_symbols"]))
    high = next(r for r in body["universe_candidates"] if r["symbol"] == "6857")
    assert high["membership_status"] == "FIXED_UNIVERSE_MEMBER"
    assert high["paper_eligibility"] == "PAPER_ELIGIBILITY_UNKNOWN"
    assert high["research_member"] is True
    assert high["structurally_ineligible"] is False
    assert a["18_any_low_liquidity_name_retained_only_for_sector_coverage"] is False
    assert a["12_trading_unit_audit_complete"] is False
    assert a["11_100_share_notional_audit_complete"] is True
    assert a["operability_rule_defined"] is True
    assert a["operability_audit_complete"] is False
    assert a["applied_this_run"] is False
    assert body["operability_rules"]["applied_this_run"] is False
    assert body["operability_rules"]["paper_preflight_required"] is True


def test_va_missing_hint():
    acquired = {
        "available": False,
        "CASE_HINT": CASE_NO_VA,
        "reason": "Va_field_absent_in_sample",
        "yfinance_used": False,
    }
    d = decide(acquired=acquired)
    assert d["VERDICT"] == CASE_NO_VA


def test_no_pnl_or_strategy_in_source():
    import research.fixed_daytrade_universe_v1.analyze as a
    import research.fixed_daytrade_universe_v1.__main__ as m

    src = inspect.getsource(a) + inspect.getsource(m)
    assert "harvest_development" not in src
    assert "evaluate_strategy" not in src
    assert "compute_pnl_yen_100" not in src
    assert "sendorder" not in src
    assert "fetch_previous_day_yfinance" not in src
    assert "import yfinance" not in src


def test_sheet_order():
    assert SHEET_ORDER == (
        "FINAL_UNIVERSE",
        "CANDIDATE_AUDIT",
        "LIQUIDITY_60D",
        "LIQUIDITY_30_30",
        "OPERABILITY",
        "LISTING_MASTER",
        "SECTOR_COVERAGE",
        "EXCLUSIONS",
        "SOURCE_AUDIT",
        "MANIFEST",
    )
    _ = pytest
    _ = np
    _ = EXCLUDED_LOT_SIZE_BLOCKERS
    _ = symbol4("72030") == "7203"
