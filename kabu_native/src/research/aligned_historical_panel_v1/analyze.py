"""Panel decision. Bind frozen universe. Fail closed on unknown Time. No strategy. No PnL."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.aligned_historical_panel_v1 import (
    ANALYSIS_ID,
    CANONICAL_TZ,
    CASE_ADDON,
    CASE_EXT_BLOCK,
    CASE_FETCH,
    CASE_PARTIAL,
    CASE_READY,
    CASE_SCHEMA,
    CASE_TIME_BLOCK,
    CASE_UNIVERSE,
    LIVE_20260914_CHANGED,
    NEXT_ADDON,
    NEXT_CAUSE,
    NEXT_EXT,
    NEXT_TIME,
    NEXT_UNIVERSE,
    PAPER_CHANGED,
    PNL_USED,
    PROBE_DATE,
    RUNTIME_CHANGED,
    SAME_BAR_CLOSE_ENTRY,
    STRATEGY_SEARCH_STARTED,
)
from research.aligned_historical_panel_v1.external import STATUS_AVAILABLE, STATUS_MISSING, inventory_external
from research.aligned_historical_panel_v1.minute_schema import CORPORATE_ACTION_POLICY, MISSING_MINUTE_POLICY
from research.aligned_historical_panel_v1.probe_minute import probe_minute_entitlement, probe_tick_crosscheck
from research.aligned_historical_panel_v1.split_panel import unassigned
from research.aligned_historical_panel_v1.technical import technical_foundation
from research.aligned_historical_panel_v1.time_semantics import (
    SEMANTICS_BAR_END,
    SEMANTICS_BAR_START,
    SEMANTICS_UNKNOWN,
    bar_clock,
)
from research.aligned_historical_panel_v1.universe_bind import bind_frozen_universe
from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE


def _sha(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


EVENT_JOIN_ARCHITECTURE = {
    "join_key": "available_at_jst <= decision_time_jst",
    "scheduled_vs_result_separated": True,
    "result_not_joined_before_release": True,
    "populated_this_run": False,
    "event_types": [
        "TSE_OPEN",
        "TSE_CLOSE",
        "OSE_DAY_OPEN",
        "OSE_NIGHT_OPEN",
        "CN_HK_OPEN",
        "US_CASH_OPEN",
        "US_CASH_CLOSE",
        "BOJ",
        "FOMC",
        "MAJOR_MACRO_RELEASE",
    ],
}


def _fail(case: str, verdict: str, nxt: str, *, reason: str, interpretation: str) -> dict[str, Any]:
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "panel_ready": False,
        "reason": reason,
        "INTERPRETATION": interpretation,
        "strategy_search_started": False,
        "pnl_used": False,
        "universe_changed": False,
    }


def decide(*, universe: dict[str, Any], minute: dict[str, Any], ticks: dict[str, Any], external: dict[str, Any]) -> dict[str, Any]:
    if not universe.get("ok"):
        return _fail(
            "UNIVERSE",
            CASE_UNIVERSE,
            NEXT_UNIVERSE,
            reason=str(universe.get("reason") or "universe_not_frozen"),
            interpretation="Frozen universe artifacts were not verified. Panel does not invent a universe.",
        )
    if minute.get("reason") == "jquants_api_key_missing":
        return _fail(
            "KEY",
            "JQUANTS_API_KEY_REQUIRED_FOR_PANEL_V1",
            NEXT_ADDON,
            reason="jquants_api_key_missing",
            interpretation="API key missing. No yfinance/daily substitute for intraday panel.",
        )
    if minute.get("addon_required") or (not minute.get("available") and minute.get("http_status") in {401, 402, 403}):
        return _fail(
            "ADDON",
            CASE_ADDON,
            NEXT_ADDON,
            reason=str(minute.get("reason") or "minute_addon_required"),
            interpretation=(
                "J-Quants minute endpoint is not entitled on the current subscription. "
                "Daily bars are not used as an intraday substitute."
            ),
        )
    if not minute.get("available"):
        return _fail(
            "FETCH",
            CASE_FETCH,
            NEXT_ADDON,
            reason=str(minute.get("reason") or "minute_fetch_failed"),
            interpretation="Equity minute fetch failed closed.",
        )
    schema = dict(minute.get("schema") or {})
    if not schema.get("ok"):
        return _fail(
            "SCHEMA",
            CASE_SCHEMA,
            NEXT_ADDON,
            reason=str(minute.get("reason") or "schema_mismatch"),
            interpretation="Official minute schema mismatch. Field names were not guessed.",
        )
    semantics = str(ticks.get("semantics") or SEMANTICS_UNKNOWN)
    if semantics not in {SEMANTICS_BAR_START, SEMANTICS_BAR_END}:
        return _fail(
            "TIME",
            CASE_TIME_BLOCK,
            NEXT_TIME,
            reason=str(ticks.get("reason") or "time_semantics_unknown"),
            interpretation=(
                "J-Quants equity minute Time is not proven as BAR_START or BAR_END. "
                "Documentation is insufficient. Mechanism research does not start."
            ),
        )
    core = dict(external.get("core") or {})
    core_missing = [k for k, v in core.items() if k != "japan_equity_fixed_universe" and v == STATUS_MISSING]
    if core_missing:
        return {
            **_fail(
                "EXTERNAL",
                CASE_EXT_BLOCK,
                NEXT_EXT,
                reason="core_external_missing:" + ",".join(core_missing),
                interpretation=(
                    "Equity minute Time semantics are proven, but core external context "
                    "(NK/TOPIX 1-min and/or USDJPY and/or ES-NQ and/or Oil) is not aligned."
                ),
            ),
            "time_semantics": semantics,
            "equity_minute_ready": True,
        }
    optional_missing = bool(external.get("asia_optional_missing"))
    if optional_missing:
        return {
            "CASE": "PARTIAL",
            "VERDICT": CASE_PARTIAL,
            "NEXT": NEXT_CAUSE,
            "panel_ready": True,
            "reason": "core_ready_asia_optional_missing",
            "INTERPRETATION": "Core panel is usable. Optional Asia 1-min sources remain missing.",
            "time_semantics": semantics,
            "strategy_search_started": False,
            "pnl_used": False,
            "universe_changed": False,
        }
    return {
        "CASE": "READY",
        "VERDICT": CASE_READY,
        "NEXT": NEXT_CAUSE,
        "panel_ready": True,
        "reason": "universe_bound_minute_acquired_time_proven_core_external_aligned_split_ready",
        "INTERPRETATION": "Aligned historical panel is ready for CAUSE_FIRST_MECHANISM_DISCOVERY_V1.",
        "time_semantics": semantics,
        "strategy_search_started": False,
        "pnl_used": False,
        "universe_changed": False,
    }


def build_manifest(*, universe: dict[str, Any], minute: dict[str, Any], ticks: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    body = {
        "universe_id": universe.get("universe_id"),
        "universe_sha": universe.get("identity_sha256"),
        "symbol_n": universe.get("symbol_n"),
        "symbols": universe.get("symbols") or [],
        "source_list": [
            "jquants_equities_bars_minute",
            "jquants_equities_trades_tick_csv_for_semantics",
            "jquants_indices_daily_partial",
            "external_usdjpy_es_nq_cl_missing",
        ],
        "historical_first": None,
        "historical_last": None,
        "common_overlap": None,
        "equity_minute_semantics": ticks.get("semantics") or SEMANTICS_UNKNOWN,
        "external_timestamp_semantics": "unconfirmed_or_missing",
        "timezone_policy": f"store_original_plus_UTC_plus_JST; canonical={CANONICAL_TZ}; no_fixed_offset",
        "corporate_action_policy": CORPORATE_ACTION_POLICY,
        "missing_minute_policy": MISSING_MINUTE_POLICY,
        "split_dates": unassigned(),
        "probe_date": PROBE_DATE,
        "full_history_ingested": False,
        "verdict": decision.get("VERDICT"),
    }
    body["content_sha256"] = _sha(body)
    return body


def build_report_body() -> dict[str, Any]:
    universe = bind_frozen_universe()
    minute = probe_minute_entitlement() if universe.get("ok") else {"available": False, "reason": "universe_not_bound"}
    ticks = probe_tick_crosscheck(minute_probe=minute) if minute.get("available") else {
        "performed": False,
        "semantics": SEMANTICS_UNKNOWN,
        "reason": "minute_not_available",
    }
    if universe.get("ok") and minute.get("available"):
        external = inventory_external()
        if minute.get("available"):
            external["core"]["japan_equity_fixed_universe"] = STATUS_AVAILABLE if minute.get("schema", {}).get("ok") else STATUS_MISSING
    else:
        external = inventory_external()
        external["core"]["japan_equity_fixed_universe"] = STATUS_MISSING
    decision = decide(universe=universe, minute=minute, ticks=ticks, external=external)
    clock = None
    sem = str(ticks.get("semantics") or SEMANTICS_UNKNOWN)
    if sem in {SEMANTICS_BAR_START, SEMANTICS_BAR_END}:
        clock = bar_clock(labeled_date=PROBE_DATE, labeled_time="09:00", semantics=sem)
    split = unassigned()
    manifest = build_manifest(universe=universe, minute=minute, ticks=ticks, decision=decision)
    return {
        "universe_bind": universe,
        "equity_minute": minute,
        "time_semantics": {
            "EQUITY_MINUTE_TIME_SEMANTICS": sem,
            "tick_crosscheck": ticks,
            "documentation_alone_insufficient": True,
            "clock_example_0900": clock,
            "same_bar_close_entry": bool(SAME_BAR_CLOSE_ENTRY),
        },
        "corporate_action_policy": CORPORATE_ACTION_POLICY,
        "missing_minute_policy": MISSING_MINUTE_POLICY,
        "external": external,
        "split": split,
        "technical_foundation": technical_foundation(),
        "event_join_architecture": EVENT_JOIN_ARCHITECTURE,
        "quality": {
            "full_panel_row_n": None,
            "note": "full_2y_minute_ingest_not_started_until_time_semantics_and_entitlement_pass",
        },
        "manifest": manifest,
        "decision": decision,
        "live_20260914": {
            "trading_date": LIVE_TRADING_DATE,
            "plan_changed": bool(LIVE_20260914_CHANGED),
            "feature_mining_closed": bool(FEATURE_MINING_CLOSED),
        },
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    u = dict(report.get("universe_bind") or {})
    m = dict(report.get("equity_minute") or {})
    t = dict(report.get("time_semantics") or {})
    ticks = dict(t.get("tick_crosscheck") or {})
    ext = dict(report.get("external") or {})
    rows = list(ext.get("rows") or [])
    split = dict(report.get("split") or {})
    d = dict(report.get("decision") or {})
    man = dict(report.get("manifest") or {})
    live = dict(report.get("live_20260914") or {})

    def _ext(name: str) -> dict[str, Any]:
        for r in rows:
            if r.get("name") == name:
                return r
        return {}

    nk = _ext("NK225_TOPIX_context")
    fut = _ext("NK225_NK225mini_TOPIX_futures_1min")
    fx = _ext("USDJPY")
    us = _ext("ES_NQ")
    oil = _ext("WTI_CL")
    rates = _ext("rates_JGB_ZT_ZN")
    asia = _ext("Asia_KOSPI_HSI_CSI_A50")
    return {
        "1_frozen_universe_manifest_verified": bool(u.get("verified")),
        "2_final_symbol_N": u.get("symbol_n"),
        "3_universe_SHA": u.get("identity_sha256"),
        "4_equity_minute_entitlement_available": bool(m.get("available")),
        "5_minute_history_first_last": [m.get("min_date_in_sample"), m.get("max_date_in_sample")] if m.get("available") else None,
        "6_each_symbol_coverage": None,
        "7_equity_minute_Time_semantics": t.get("EQUITY_MINUTE_TIME_SEMANTICS"),
        "8_tick_crosscheck_performed": bool(ticks.get("performed")),
        "9_same_bar_leakage_protected": SAME_BAR_CLOSE_ENTRY is False,
        "10_corporate_action_handling": CORPORATE_ACTION_POLICY,
        "11_NK_TOPIX_source": nk.get("status") or STATUS_MISSING,
        "12_futures_contracts_kept_separate": True,
        "13_USDJPY_source_range": {"status": fx.get("status"), "range": None, "source": fx.get("source")},
        "14_ES_NQ_source_range": {"status": us.get("status"), "range": None, "source": us.get("source")},
        "15_WTI_source_range": {"status": oil.get("status"), "range": None, "source": oil.get("source")},
        "16_rates_source_status": rates.get("status") or STATUS_MISSING,
        "17_Asia_source_status": asia.get("status") or STATUS_MISSING,
        "18_canonical_UTC_JST_conversion_pass": bool(t.get("clock_example_0900")),
        "19_common_overlap_period": None,
        "20_chronological_split_exact_dates": split,
        "21_Discovery_N_days": (split.get("discovery") or {}).get("n") if split.get("dates_assigned") else None,
        "22_Confirmation_N_days": (split.get("confirmation") or {}).get("n") if split.get("dates_assigned") else None,
        "23_Frozen_Validation_N_days": (split.get("frozen_validation") or {}).get("n") if split.get("dates_assigned") else None,
        "24_missing_minute_policy": MISSING_MINUTE_POLICY,
        "25_source_quality_failures": [
            x
            for x in [
                None if m.get("available") else "equity_minute_not_entitled_or_failed",
                None if t.get("EQUITY_MINUTE_TIME_SEMANTICS") in {SEMANTICS_BAR_START, SEMANTICS_BAR_END} else "time_semantics_unknown",
                "core_external_missing",
            ]
            if x
        ],
        "26_panel_manifest_SHA": man.get("content_sha256"),
        "27_strategy_search_started": bool(STRATEGY_SEARCH_STARTED),
        "28_PnL_used": bool(PNL_USED),
        "29_Runtime_changed": bool(RUNTIME_CHANGED),
        "30_Paper_changed": bool(PAPER_CHANGED),
        "31_20260914_live_changed": bool(live.get("plan_changed")),
        "32_submit_cancel_live": "0/0/0",
        "33_VERDICT": d.get("VERDICT"),
        "34_NEXT": d.get("NEXT"),
        "futures_autosplice": False,
        "universe_changed": False,
        "daily_not_used_as_intraday": True,
        "FEATURE_MINING_CLOSED": bool(FEATURE_MINING_CLOSED),
        "probe_date": PROBE_DATE,
        "futures_status": fut.get("status"),
        "panel_conditioned_universe": bool(u.get("PANEL_CONDITIONED")),
    }


assert ANALYSIS_ID == "BUILD_ALIGNED_HISTORICAL_PANEL_V1"
assert CASE_TIME_BLOCK.endswith("TIME_SEMANTICS_BLOCKED_V1")
assert CASE_ADDON == "JQUANTS_MINUTE_ADDON_REQUIRED_V1"
assert LIVE_TRADING_DATE == "20260914"
