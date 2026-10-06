"""Freeze the literal thesis-loss exit. No replay."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.simple_tech_entry_family import ARCHITECTURE_ID, STRATEGY_ID
from research.simple_tech_entry_family.spec import spec_sha256
from research.symbol_setup_baseline_minimal_completion.contract import execution as entry_execution
from research.symbol_setup_baseline_minimal_completion.contract import portfolio
from research.symbol_setup_baseline_minimal_completion.thesis import thesis
from research.symbol_setup_thesis_aligned_exit_precommit import (
    BASELINE_ID,
    BASELINE_SPEC_SHA256,
    CASE_EXEC,
    CASE_FAIL,
    CASE_READY,
    ENTRY_EXECUTION_ID,
    ENTRY_EXECUTION_SHA256,
    EXIT_ID,
    NEXT_EXEC,
    NEXT_FAIL,
    NEXT_READY,
    PORTFOLIO_ID,
    PORTFOLIO_SHA256,
    SURFACE_FIRST,
    SURFACE_LAST,
    SURFACE_N,
    THESIS_ID,
    THESIS_SHA256,
)
from research.symbol_setup_thesis_aligned_exit_precommit.execution import execution_identity
from research.symbol_setup_thesis_aligned_exit_precommit.isolation import NATIVE


def _sha(body: dict[str, Any]) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def surface() -> dict[str, Any]:
    path = NATIVE / "results" / "research" / "symbol_setup_baseline_minimal_completion_v1" / "report.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    dates = list(body["coverage"]["eligible_dates"])
    return {
        "source": "results/research/symbol_setup_baseline_minimal_completion_v1/report.json",
        "eligible_session_n": len(dates),
        "eligible_dates": dates,
        "range": [dates[0], dates[-1]] if dates else [],
        "matches_bound_surface": len(dates) == SURFACE_N and bool(dates) and dates[0] == SURFACE_FIRST and dates[-1] == SURFACE_LAST,
        "classification": "DEVELOPMENT_EXPOSED",
        "holdout": False,
        "frozen_validation": False,
        "prospective": False,
        "post_20260911_opened": False,
        "ohlc_only_history_used": False,
    }


def exit_contract(exe: dict[str, Any]) -> dict[str, Any]:
    body = {
        "EXIT_ID": EXIT_ID,
        "PERSISTENCE_K": 1,
        "THESIS_OBSERVATION_TIMEFRAME": "1min_completed",
        "SIGNAL_BAR_REUSED_FOR_EXIT": False,
        "first_observation": "first completed 1-minute bar whose finalize time is strictly after fill_t",
        "THESIS_LIVE": "EMA9[t] > EMA21[t] AND EMA21[t] > EMA21[t-3], all finite, completed bar only",
        "THESIS_LOST": "NOT THESIS_LIVE on one new post-fill bar",
        "equivalent_loss": "EMA9[t] <= EMA21[t] OR EMA21[t] <= EMA21[t-3]",
        "reasons": {
            "FAST_SLOW_CROSS_LOSS": "EMA9 <= EMA21 AND EMA21[t] > EMA21[t-3]",
            "SLOW_TREND_SLOPE_LOSS": "EMA9 > EMA21 AND EMA21[t] <= EMA21[t-3]",
            "BOTH_TREND_COMPONENTS_LOST": "EMA9 <= EMA21 AND EMA21[t] <= EMA21[t-3]",
        },
        "reasons_are_diagnostics": True,
        "same_exit_execution_for_all_reasons": True,
        "state": "LIVE at fill; LOST is absorbing; no resurrection while the position is open",
        "non_finite_ema": "INVALID_THESIS_OBSERVATION, not THESIS_LOST",
        "invalid_action": "FAIL_CLOSE_INVALID_DATA",
        "invalid_price": "the same first causal bid at or after that bar finalize; otherwise the position stays open until session fail-close",
        "EXIT_EXECUTION_ID": exe["EXIT_EXECUTION_ID"],
        "EXIT_EXECUTION_SHA256": exe["EXIT_EXECUTION_SHA256"],
        "session_close": "SESSION_FAIL_CLOSE at the AM session end on the last valid causal bid",
        "session_close_is_thesis_loss": False,
        "forbidden_as_thesis_loss": [
            "RCI returns to or below -80",
            "volume falls below 1.5 times the prior median",
            "the Bollinger pullback touch disappears",
            "price no longer breaks the previous high",
            "the board quantity relation worsens",
            "price rises above the upper band",
        ],
        "event_order": [
            "FAIL_CLOSE_INVALID_DATA",
            "FINALIZE_COMPLETED_BAR",
            "UPDATE_INDICATORS",
            "EVALUATE_OPEN_POSITION_THESIS",
            "EMIT_THESIS_LOST",
            "EXECUTE_EXIT if causal bid available",
            "SLOT_RELEASE on EXIT execution",
            "GENERATE_NEW_ENTRY_SIGNAL",
            "APPLY_SAME_SYMBOL / CAP",
            "CREATE_PENDING_ENTRY",
            "PROCESS_PENDING_FILL",
        ],
        "k_values_tested": [],
        "pnl_used": False,
    }
    body["EXIT_CONTRACT_SHA256"] = _sha(body)
    return body


def build() -> dict[str, Any]:
    live_spec = spec_sha256()
    th = thesis()
    ent = entry_execution()
    port = portfolio()
    exe = execution_identity()
    data = surface()
    contract = exit_contract(exe)
    ident_ok = (
        STRATEGY_ID == BASELINE_ID
        and ARCHITECTURE_ID == "MA_BB_RCI_VOLUME_PRICE_ACTION_BOARD_SUPPORT"
        and live_spec == BASELINE_SPEC_SHA256
        and th["THESIS_ID"] == THESIS_ID
        and th["THESIS_SHA256"] == THESIS_SHA256
        and ent["EXECUTION_ID"] == ENTRY_EXECUTION_ID
        and ent["EXECUTION_SHA256"] == ENTRY_EXECUTION_SHA256
        and port["PORTFOLIO_ID"] == PORTFOLIO_ID
        and port["PORTFOLIO_SHA256"] == PORTFOLIO_SHA256
        and port["sha_matches_recovered"]
        and data["matches_bound_surface"]
    )
    if not ident_ok:
        verdict, nxt = CASE_FAIL, NEXT_FAIL
    elif not exe["identity_resolved"]:
        verdict, nxt = CASE_EXEC, NEXT_EXEC
    else:
        verdict, nxt = CASE_READY, NEXT_READY
    return {
        "baseline_id": BASELINE_ID,
        "baseline_spec_sha256": live_spec,
        "architecture_id": ARCHITECTURE_ID,
        "thesis_id": th["THESIS_ID"],
        "thesis_sha256": th["THESIS_SHA256"],
        "entry_changed": False,
        "prior_wording_corrected": True,
        "prior_wording": {
            "old_claim": "A_EMA_STRUCTURE_LOSS = EMA9 <= EMA21 was described as the exact negation of the V1 trend clause",
            "correction": "That predicate is FAST_SLOW_RELATION_LOSS only. The slow-trend rise is a second required component.",
            "old_artifacts_modified": False,
            "v27": "PERSISTENCE::3m::A_EMA_STRUCTURE_LOSS remains MECHANISM_DISCOVERY_ONLY. EXIT_POLICY_CREATED false.",
            "v28": "3m EMA persistence K=6 remains DIAGNOSTIC_EVIDENCE_ONLY. ADOPTED false.",
        },
        "thesis_logic": {
            "FAST_SLOW_RELATION_LIVE": "EMA9[t] > EMA21[t]",
            "SLOW_TREND_RISING": "EMA21[t] > EMA21[t-3]",
            "SYMBOL_SETUP_THESIS_LIVE": "FAST_SLOW_RELATION_LIVE AND SLOW_TREND_RISING",
            "SYMBOL_SETUP_THESIS_LOST": "NOT SYMBOL_SETUP_THESIS_LIVE",
            "inputs": "finite EMA9 and EMA21 on a completed 1-minute bar, including EMA21 three bars earlier",
        },
        "exit": contract,
        "execution": exe,
        "entry_execution": {
            "ENTRY_EXECUTION_ID": ent["EXECUTION_ID"],
            "ENTRY_EXECUTION_SHA256": ent["EXECUTION_SHA256"],
            "changed": False,
        },
        "portfolio": {
            "PORTFOLIO_ID": port["PORTFOLIO_ID"],
            "PORTFOLIO_SHA256": port["PORTFOLIO_SHA256"],
            "cap": port["cap"],
            "same_symbol": port["same_symbol"],
            "slot_release": port["slot_release"],
            "reentry": "only after slot release, and only from a new complete V1 entry signal",
            "changed": False,
            "pb1_substituted": False,
        },
        "surface": data,
        "one_minute_mixed": True,
        "structure_layer_present": False,
        "support_resistance_added": False,
        "rci_used_in_exit": False,
        "volume_used_in_exit": False,
        "board_used_in_exit_thesis": False,
        "new_pnl_run": False,
        "pf_calculated": False,
        "exit_candidates_compared_by_pnl": False,
        "prospective_data_opened": False,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "pb1_changed": False,
        "v4_changed": False,
        "v5_created": False,
        "verdict": verdict,
        "next": nxt,
    }
