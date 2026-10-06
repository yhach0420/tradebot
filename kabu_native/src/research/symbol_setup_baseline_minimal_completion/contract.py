"""Bind V1 and decide whether an exact thesis exit already exists."""
from __future__ import annotations

import hashlib
from typing import Any

from research.am_entry_profit_improvement import C14_ID, DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import ARCHITECTURE_ID, STRATEGY_ID
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_entry_family.v3_spec import ANALYSIS_ID as V3_ID
from research.simple_tech_entry_family.v3_spec import spec_sha256_v3
from research.simple_tech_exit_family.v18_spec import ANALYSIS_ID as V18_ID
from research.simple_tech_exit_family.v18_spec import POLICY_ID as V18_POLICY
from research.simple_tech_exit_family.v18_spec import spec_sha256_v18
from research.simple_tech_exit_family.v19_spec import ANALYSIS_ID as V19_ID
from research.simple_tech_exit_family.v19_spec import spec_sha256_v19
from research.simple_tech_redesign.v26_spec import ANALYSIS_ID as V26_ID
from research.simple_tech_redesign.v26_spec import spec_sha256_v26
from research.simple_tech_redesign.v27_spec import ANALYSIS_ID as V27_ID
from research.simple_tech_redesign.v27_spec import spec_sha256_v27
from research.simple_tech_redesign.v28_spec import ANALYSIS_ID as V28_ID
from research.simple_tech_redesign.v28_spec import POLICY_ID as V28_POLICY
from research.simple_tech_redesign.v28_spec import spec_sha256_v28
from research.simple_tech_redesign.v29_spec import ANALYSIS_ID as V29_ID
from research.simple_tech_redesign.v29_spec import spec_sha256_v29
from research.symbol_setup_baseline_minimal_completion import (
    ARCHITECTURE_ID as REQUIRED_ARCH,
    BASELINE_ID,
    BASELINE_SPEC_SHA256,
    CASE_DATA,
    CASE_EXIT,
    CASE_FAIL,
    CASE_READY,
    NEXT_DATA,
    NEXT_EXIT,
    NEXT_FAIL,
    NEXT_READY,
    PORTFOLIO_ID,
    PORTFOLIO_SHA256,
)
from research.symbol_setup_baseline_minimal_completion.coverage import coverage
from research.symbol_setup_baseline_minimal_completion.isolation import NATIVE
from research.symbol_setup_baseline_minimal_completion.thesis import mechanism_family, thesis
from small_paper.v1r_primary_runtime import POSITION_CAP


def _sha(body: dict[str, Any]) -> str:
    import json

    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def execution() -> dict[str, Any]:
    body = {
        "EXECUTION_ID": "SIMPLE_TECH_V1_PASSIVE_BID_W5",
        "limit": "bid at the completed bar finalize snapshot",
        "wait_sec": float(DEV_WAIT_SEC),
        "fill_rule": "first later continuous executable ask at or below the limit, quantity >= 100, freshness <= 5 seconds",
        "fill_price": "the limit, with no price improvement",
        "source": "src/research/simple_tech_entry_family/harvest.py standalone_fill",
        "engine": "src/research/e1_x34a_execution_policy/arms.py find_ask_cross_fill",
        "not_used": "historical next-bar open",
    }
    body["EXECUTION_SHA256"] = _sha({k: v for k, v in body.items() if k != "EXECUTION_SHA256"})
    return body


def portfolio() -> dict[str, Any]:
    path = NATIVE / "src" / "research" / "simple_tech_entry_family" / "portfolio.py"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "PORTFOLIO_ID": PORTFOLIO_ID,
        "PORTFOLIO_SHA256": digest,
        "sha_matches_recovered": digest == PORTFOLIO_SHA256,
        "source_file": "src/research/simple_tech_entry_family/portfolio.py",
        "cap": int(POSITION_CAP),
        "same_symbol": "block while a pending or open position exists for that symbol and session date",
        "slot_release": "pending expires at 5 seconds without a fill; an open slot releases on EXIT",
        "reentry": "allowed after the slot is released",
        "session": str(SESSION),
        "session_close": "FAIL_CLOSE at the AM session end on the last valid bid. Not thesis invalidation.",
    }


def prior_exits() -> list[dict[str, Any]]:
    parent = BASELINE_ID
    return [
        {
            "analysis_id": "SIMPLE_TECH_V1_C14",
            "strategy_parent": parent,
            "entry_identity": parent,
            "exit_mechanism": C14_ID,
            "timeframe": "post-fill quote path",
            "causal_rule": "Arch E imbalance guard, else 600/750 second continuation, else session-close bid",
            "threshold_persistence": "legacy paper exit",
            "future_information_used": False,
            "pnl_used_to_select": False,
            "status": "LEGACY_NOT_THESIS_ALIGNED",
            "sha256": "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255",
        },
        {
            "analysis_id": V3_ID,
            "strategy_parent": parent,
            "entry_identity": parent,
            "exit_mechanism": "none; exit-neutral ask markout",
            "timeframe": "30/60/180/300 second marks",
            "causal_rule": "mark only; no exit order",
            "threshold_persistence": "horizons are diagnostic",
            "future_information_used": True,
            "pnl_used_to_select": False,
            "status": "DIAGNOSTIC_EVIDENCE_ONLY",
            "sha256": spec_sha256_v3(),
        },
        {
            "analysis_id": V18_ID,
            "strategy_parent": "later E4 stack, not V1 execution",
            "entry_identity": "T3_PULLBACK_RCI development stack",
            "exit_mechanism": V18_POLICY,
            "timeframe": "180 seconds",
            "causal_rule": "first causal bid at 180 seconds",
            "threshold_persistence": "fixed hold",
            "future_information_used": False,
            "pnl_used_to_select": True,
            "status": "DIAGNOSTIC_EVIDENCE_ONLY",
            "sha256": spec_sha256_v18(),
        },
        {
            "analysis_id": V19_ID,
            "strategy_parent": "V18",
            "entry_identity": "later development stack",
            "exit_mechanism": "FIXED180_FIRST_CAUSAL_BID verification",
            "timeframe": "180 seconds",
            "causal_rule": "same fixed hold",
            "threshold_persistence": "fixed hold",
            "future_information_used": False,
            "pnl_used_to_select": True,
            "status": "DIAGNOSTIC_EVIDENCE_ONLY",
            "sha256": spec_sha256_v19(),
        },
        {
            "analysis_id": V26_ID,
            "strategy_parent": "T3_PULLBACK_RCI__E4_INSIDE1_W5",
            "entry_identity": "research coverage surface, not frozen V1 execution",
            "exit_mechanism": "primitive catalogue including A_EMA_STRUCTURE_LOSS = EMA9<=EMA21",
            "timeframe": "1m, 3m, 5m",
            "causal_rule": "primitive_true on completed bars after fill",
            "threshold_persistence": "no persistence exit frozen",
            "future_information_used": True,
            "pnl_used_to_select": False,
            "status": "DIAGNOSTIC_EVIDENCE_ONLY",
            "sha256": spec_sha256_v26(),
        },
        {
            "analysis_id": V27_ID,
            "strategy_parent": "V26",
            "entry_identity": "V26 research fills",
            "exit_mechanism": "PERSISTENCE::3m::A_EMA_STRUCTURE_LOSS",
            "timeframe": "3m",
            "causal_rule": "discovery rank across persistence, propagation, and non-recovery",
            "threshold_persistence": "mechanism name only; K not frozen; EXIT_POLICY_CREATED false",
            "future_information_used": True,
            "pnl_used_to_select": False,
            "status": "MECHANISM_DISCOVERY_ONLY",
            "sha256": spec_sha256_v27(),
        },
        {
            "analysis_id": V28_ID,
            "strategy_parent": "V27",
            "entry_identity": "added ask-fallback fills only",
            "exit_mechanism": V28_POLICY,
            "timeframe": "3m",
            "causal_rule": "sixth consecutive 3m bar with EMA9<=EMA21, then first causal bid",
            "threshold_persistence": "K=6 taken as the integer above the V27 median of 5",
            "future_information_used": True,
            "pnl_used_to_select": True,
            "status": "DIAGNOSTIC_EVIDENCE_ONLY",
            "sha256": spec_sha256_v28(),
        },
        {
            "analysis_id": V29_ID,
            "strategy_parent": "V28",
            "entry_identity": "V26 research fills",
            "exit_mechanism": "terminal sequence RCA after first 3m EMA damage",
            "timeframe": "3m",
            "causal_rule": "no exit policy",
            "threshold_persistence": "K6 explicitly not adopted",
            "future_information_used": True,
            "pnl_used_to_select": False,
            "status": "DIAGNOSTIC_EVIDENCE_ONLY",
            "sha256": spec_sha256_v29(),
        },
    ]


def build() -> dict[str, Any]:
    live = spec_sha256()
    ident_ok = STRATEGY_ID == BASELINE_ID and ARCHITECTURE_ID == REQUIRED_ARCH and live == BASELINE_SPEC_SHA256
    port = portfolio()
    exe = execution()
    th = thesis()
    fam = mechanism_family()
    data = coverage()
    exact_exit = False
    if not ident_ok or not port["sha_matches_recovered"]:
        verdict, nxt = CASE_FAIL, NEXT_FAIL
    elif not exact_exit:
        verdict, nxt = CASE_EXIT, NEXT_EXIT
    elif not data["EXACT_V1_REPLAY_DATA_READY"]:
        verdict, nxt = CASE_DATA, NEXT_DATA
    else:
        verdict, nxt = CASE_READY, NEXT_READY
    return {
        "baseline_id": BASELINE_ID,
        "baseline_spec_sha256": live,
        "identity_ok": ident_ok,
        "entry_changed": False,
        "setup_timeframe": "1min_completed",
        "trigger_timeframe": "1min_completed",
        "setup_timeframe_changed": False,
        "one_minute_mixed": True,
        "structure_layer_present": False,
        "support_resistance_added": False,
        "pb1_required": False,
        "market_context_required": False,
        "sector_context_required": False,
        "thesis": th,
        "stage_roles": th["clauses"],
        "prior_exits": prior_exits(),
        "ema_persistence": {
            "name": "PERSISTENCE::3m::A_EMA_STRUCTURE_LOSS",
            "found": True,
            "classification": "MECHANISM_DISCOVERY_ONLY",
            "artifact": "results/research/simple_tech_redesign/v27_exit_state_sequence_rca/report.json",
            "exit_policy_created": False,
            "v28_executable_but_not_frozen": True,
            "v28_adopted": False,
        },
        "exit_source": "NONE_EXACT",
        "exit_id": None,
        "exit_sha256": None,
        "exit_thesis_aligned": False,
        "mechanism_family": fam,
        "execution": exe,
        "portfolio": port,
        "session_close": {
            "role": "FAIL_CLOSE",
            "rule": "AM session end, last valid bid",
            "is_thesis_invalidation": False,
        },
        "coverage": {k: v for k, v in data.items() if k != "rows"},
        "coverage_rows": data["rows"],
        "research_days_n": len(tuple(ELIGIBLE_DAYS)),
        "complete_strategy_id": None,
        "complete_strategy_sha256": None,
        "verdict": verdict,
        "next": nxt,
        "new_pnl_run": False,
        "prospective_data_opened": False,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }
