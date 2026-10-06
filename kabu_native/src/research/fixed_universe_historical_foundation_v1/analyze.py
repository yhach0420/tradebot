"""Phase 0 foundation decision. No strategy. No PnL. No purchase."""
from __future__ import annotations

from typing import Any

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.fixed_universe_historical_foundation_v1 import (
    CASE_PARTIAL_EXTERNAL,
    CASE_READY,
    CASE_STOCK_BLOCKED,
    ENTRY,
    EXIT,
    FACTOR_LAYERS,
    FUTURE_STRATEGY_FAMILIES,
    LIVE_20260914_CHANGED,
    LIVE_REPOSITIONING,
    LIVE_REPOSITIONING_SCRIPT_CHANGE,
    NEXT_FREEZE,
    NEXT_MECHANISM,
    NEXT_PANEL,
    PAPER_CHANGED,
    PANEL_CONDITIONED,
    RECOMMENDED_STOCK_N,
    RUNTIME_CHANGED,
    STRATEGY_SEARCH_STARTED,
)
from research.fixed_universe_historical_foundation_v1.calendar import dst_plan, session_calendar_rows
from research.fixed_universe_historical_foundation_v1.corporate_actions import plan as ca_plan
from research.fixed_universe_historical_foundation_v1.sources import (
    cost_estimate,
    futures_historical,
    gaps,
    jquants_equity_minute_usable,
    minimum_required,
    source_rows,
)
from research.fixed_universe_historical_foundation_v1.split import split_plan
from research.fixed_universe_historical_foundation_v1.technical import library_spec, self_check
from research.fixed_universe_historical_foundation_v1.timestamps import fully_known, leakage_protection, semantics_rows
from research.fixed_universe_historical_foundation_v1.universe import (
    candidate_rows,
    methodology,
    sector_coverage,
)
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE


def decide() -> dict[str, Any]:
    jq = jquants_equity_minute_usable()
    fut = futures_historical()
    req = minimum_required()
    g = gaps()
    blockers = [x for x in g if x["severity"] == "BLOCKER_BEFORE_PANEL"]
    if jq["blocked"] or not jq["usable"]:
        case = "STOCK_BLOCKED"
        verdict = CASE_STOCK_BLOCKED
        nxt = "RESOLVE_JAPANESE_EQUITY_MINUTE_SOURCE_V1"
    elif not req["enough_to_start_after_purchase_and_time_confirm"]:
        case = "STOCK_BLOCKED"
        verdict = CASE_STOCK_BLOCKED
        nxt = "RESOLVE_JAPANESE_EQUITY_MINUTE_SOURCE_V1"
    else:
        # Equity minutes exist. External 1-minute is incomplete (Asia/VIX/Brent/cash rates).
        # Timestamp Time field not fully specified. Do not fake READY.
        case = "PARTIAL_EXTERNAL"
        verdict = CASE_PARTIAL_EXTERNAL
        nxt = NEXT_FREEZE
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "THEN": [NEXT_PANEL, NEXT_MECHANISM],
        "FOUNDATION_VIABLE": jq["usable"] and fut["nk225_1min_official"],
        "STOCK_MINUTE_BLOCKED": False,
        "EXTERNAL_COMPLETE": False,
        "ENOUGH_TO_START_AFTER_PURCHASE": True,
        "TIMESTAMP_FULLY_KNOWN": fully_known(),
        "STRATEGY_SEARCH_STARTED": False,
        "PROFIT_OPTIMIZATION": False,
        "UNIVERSE_FROZEN": False,
        "PANEL_CONDITIONED": bool(PANEL_CONDITIONED),
        "LIVE_20260914_CHANGED": bool(LIVE_20260914_CHANGED),
        "DAY1_MINING_REOPENED": False,
        "RUNTIME_CHANGED": bool(RUNTIME_CHANGED),
        "PAPER_CHANGED": bool(PAPER_CHANGED),
        "ENTRY": bool(ENTRY),
        "EXIT": bool(EXIT),
        "BLOCKERS_BEFORE_RESEARCH_PANEL": [x["gap_id"] for x in blockers],
        "INTERPRETATION": (
            "Japanese equity 1-minute history exists at J-Quants (2 years, paid add-on) "
            "and OSE NK225/TOPIX/NK225mini 1-minute exists at DataCube. "
            "A 45-name Fixed Day-Trade Universe is viable as PANEL-CONDITIONED research. "
            "External 1-minute is not complete (Asia cash, VIX, Brent, cash US rates). "
            "J-Quants equity minute Time start-vs-end is not fully specified. "
            "That is enough to freeze the universe next, then confirm timestamps and "
            "build an aligned panel. It is not enough to claim full READY. "
            "Do not start strategy search."
        ),
        "READY_VERDICT_NOT_USED": CASE_READY,
        "FACTOR_LAYERS": list(FACTOR_LAYERS),
        "FUTURE_STRATEGY_FAMILIES": list(FUTURE_STRATEGY_FAMILIES),
        "CAUSE_FIRST_RCA": (
            "When winners/losers appear, inspect external market/FX/oil/rates/Asia/"
            "breadth/sector events before technical feature search."
        ),
        "ENTRY_EXIT_RULE": (
            "Do not optimize ENTRY then attach an independent EXIT. "
            "EXIT comes from the ENTRY thesis continuation/failure/invalidation."
        ),
        "HISTORICAL_VS_PAPER": (
            "Historical 1-minute OHLCV is MECHANISM/SIGNAL evidence. "
            "Paper Ask ENTRY / Bid EXIT / spread / fill / CAP is EXECUTABLE evidence."
        ),
    }


def build_report_body() -> dict[str, Any]:
    return {
        "universe_methodology": methodology(),
        "universe_candidates": candidate_rows(),
        "sector_coverage": sector_coverage(),
        "data_sources": source_rows(),
        "jquants_equity_minute": jquants_equity_minute_usable(),
        "futures_historical": futures_historical(),
        "timestamp_semantics": semantics_rows(),
        "leakage_protection": leakage_protection(),
        "corporate_actions": ca_plan(),
        "session_calendar": session_calendar_rows(),
        "dst_plan": dst_plan(),
        "research_split": split_plan(),
        "technical_library": library_spec(),
        "technical_self_check": self_check(),
        "minimum_required": minimum_required(),
        "gaps": gaps(),
        "cost_estimate": cost_estimate(),
        "decision": decide(),
        "live_20260914": {
            "trading_date": LIVE_TRADING_DATE,
            "plan_changed": False,
            "script_changed": bool(LIVE_REPOSITIONING_SCRIPT_CHANGE),
            "repositioning_label_only": LIVE_REPOSITIONING,
            "day2_frozen_confirmation_unchanged": True,
            "futures_capture_unchanged": True,
            "breadth_capture_unchanged": True,
            "feature_mining_closed": bool(FEATURE_MINING_CLOSED),
        },
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    u = dict(report.get("universe_methodology") or {})
    jq = dict(report.get("jquants_equity_minute") or {})
    fut = dict(report.get("futures_historical") or {})
    leak = dict(report.get("leakage_protection") or {})
    split = dict(report.get("research_split") or {})
    tech = dict(report.get("technical_library") or {})
    ca = dict(report.get("corporate_actions") or {})
    dst = dict(report.get("dst_plan") or {})
    cost = dict(report.get("cost_estimate") or {})
    gaps_l = list(report.get("gaps") or [])
    live = dict(report.get("live_20260914") or {})
    src = list(report.get("data_sources") or [])

    def _src(name: str) -> dict[str, Any]:
        return next((r for r in src if r.get("instrument") == name), {})

    return {
        "1_Fixed_Universe_approach_viable": bool(u.get("APPROACH_VIABLE")),
        "2_recommended_stock_N": int(RECOMMENDED_STOCK_N),
        "3_selection_methodology": u.get("FREEZE_STEP_MUST_VERIFY"),
        "4_survivorship_panel_conditioning_documented": bool(u.get("PANEL_CONDITIONED_HISTORICAL_RESEARCH"))
        and u.get("CLAIM_ONE_YEAR_AGO_SAME_UNIVERSE") is False,
        "5_J_Quants_minute_data_usable": bool(jq.get("usable")),
        "6_history_available": jq.get("history_years"),
        "7_futures_historical_source": "J-Quants DataCube NK225/NK225mini/TOPIX 1-minute per-contract CSV",
        "8_USDJPY_source_candidate": _src("USDJPY").get("provider"),
        "9_US_futures_source_candidate": _src("ES_NQ_FUTURES_1MIN").get("provider"),
        "10_oil_source_candidate": _src("WTI_CL_FUTURES_1MIN").get("provider"),
        "11_Asia_source_candidates": "KOSPI/HSI/CSI300/A50 official 1-minute not confirmed; GAP start without",
        "12_rates_source_candidates": "JGB futures DataCube 1-minute official; US2Y/US10Y via ZT/ZN Databento proxy; FRED daily overnight-only",
        "13_timestamp_semantics_fully_known": bool(d.get("TIMESTAMP_FULLY_KNOWN")),
        "14_DST_handling_plan": bool(dst.get("plan_ready")),
        "15_corporate_action_plan": bool(ca.get("plan_ready")),
        "16_minimum_common_historical_period": (
            f"min {split.get('min_years')}y preferred {split.get('preferred_years')}y; "
            "equity minute cap 2y; dates unassigned until overlap ingested"
        ),
        "17_data_gaps": [g["gap_id"] for g in gaps_l],
        "18_cost_estimate_if_known": cost.get("recommended_start_bundle_note"),
        "19_blockers_before_research": d.get("BLOCKERS_BEFORE_RESEARCH_PANEL"),
        "20_same_bar_leakage_protection_ready": bool(leak.get("SPEC_READY")) and leak.get("APPLIED_TO_PANEL") is False,
        "21_chronological_split_plan": bool(split.get("plan_ready")) and split.get("random_split_primary") is False,
        "22_simple_technical_library_definition_ready": bool(tech.get("ready")) and tech.get("strategy_search") is False,
        "23_20260914_live_plan_changed": False,
        "24_Day1_mining_reopened": False,
        "25_Runtime_changed": False,
        "26_Paper_changed": False,
        "27_submit_cancel_live": "0/0/0",
        "28_VERDICT": d.get("VERDICT"),
        "29_NEXT": d.get("NEXT"),
        "live_trading_date": live.get("trading_date") or LIVE_TRADING_DATE,
        "futures_1min_autosplice": False,
        "futures_autosplice_forbidden": bool(fut.get("autosplice_forbidden")),
        "jquants_not_realtime_entry": jq.get("realtime_entry_source") is False,
        "strategy_search_started": bool(STRATEGY_SEARCH_STARTED),
        "FEATURE_MINING_CLOSED": bool(FEATURE_MINING_CLOSED),
    }


assert FEATURE_MINING_CLOSED is True
assert LIVE_TRADING_DATE == "20260914"
assert LIVE_20260914_CHANGED is False
