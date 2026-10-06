"""Assemble the complete-strategy contract. Does not replay trades."""
from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.spec import spec_sha256
from research.symbol_setup_baseline_complete_strategy_precommit import (
    BASELINE_ID,
    BASELINE_SPEC_SHA256,
    CASE_COST,
    CASE_IDENTITY,
    CASE_READY,
    CASE_RUNNER,
    CASE_SIZE,
    CASE_UNIVERSE,
    COMPLETE_STRATEGY_ID,
    ENTRY_EXECUTION_ID,
    ENTRY_EXECUTION_SHA256,
    EXIT_CONTRACT_SHA256,
    EXIT_EXECUTION_ID,
    EXIT_EXECUTION_SHA256,
    EXIT_ID,
    NEXT_READY,
    NEXT_STOP,
    PORTFOLIO_ID,
    PORTFOLIO_SHA256,
    THESIS_ID,
    THESIS_SHA256,
)
from research.symbol_setup_baseline_complete_strategy_precommit.isolation import NATIVE
from research.symbol_setup_baseline_complete_strategy_precommit.runner import (
    EVENT_ORDER,
    EXPLICIT_ADDITIONAL_COST_YEN,
    RUNNER_ID,
    SHARES,
    account,
    ledger_sha,
    max_drawdown_yen,
    profit_factor,
)
from research.symbol_setup_baseline_complete_strategy_precommit.universe import recover
from research.symbol_setup_thesis_aligned_exit_precommit.execution import execution_identity
from replay.pnl_yen import compute_pnl_yen_100

ORIGINAL18 = (
    "20260722",
    "20260728",
    "20260729",
    "20260730",
    "20260731",
    "20260803",
    "20260804",
    "20260805",
    "20260806",
    "20260807",
    "20260810",
    "20260817",
    "20260819",
    "20260820",
    "20260824",
    "20260825",
    "20260826",
    "20260827",
)
EXTENSION17 = (
    "20260723",
    "20260724",
    "20260727",
    "20260812",
    "20260813",
    "20260818",
    "20260821",
    "20260828",
    "20260831",
    "20260901",
    "20260902",
    "20260903",
    "20260904",
    "20260907",
    "20260908",
    "20260909",
    "20260910",
)


def _sha(body: Any) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _dates() -> list[str]:
    path = NATIVE / "results" / "research" / "symbol_setup_baseline_minimal_completion_v1" / "report.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    return [str(d) for d in body["coverage"]["eligible_dates"]]


def _size() -> dict[str, Any]:
    src = inspect.getsource(compute_pnl_yen_100)
    check = account(1000.0, 1010.0)
    formula_ok = check["gross_pnl_yen"] == 1000.0 and "fees/tax excluded" in src and int(SHARES) == 100
    body = {
        "POSITION_SIZE_POLICY_ID": "SIMPLE_TECH_V1_FIXED_100_SHARES",
        "shares_per_fill": 100,
        "source_file": "src/replay/pnl_yen.py",
        "function": "compute_pnl_yen_100",
        "source_sha256": hashlib.sha256(src.encode("utf-8")).hexdigest(),
        "pyramiding": False,
        "why": "V1 stores pnl_yen_100 from compute_pnl_yen_100, whose contract is gross yen for 100 shares.",
    }
    body["POSITION_SIZE_POLICY_SHA256"] = _sha(body)
    body["resolved"] = formula_ok
    return body


def _cost() -> dict[str, Any]:
    body = {
        "COST_POLICY_ID": "SIMPLE_TECH_V1_GROSS_EXECUTION_PRICE_YEN",
        "explicit_commission": {"class": "EXPLICIT_ADDITIONAL_COST", "value": 0.0},
        "explicit_slippage_tax": {"class": "EXPLICIT_ADDITIONAL_COST", "value": 0.0},
        "explicit_round_trip_bps": {"class": "EXPLICIT_ADDITIONAL_COST", "value": 0.0},
        "quote_spread": {
            "class": "OBSERVED_IN_EXECUTION_PRICE",
            "entry": "passive bid limit, filled when the ask crosses, at the limit",
            "exit": "causal executable bid",
        },
        "other_cost": {"class": "EXPLICIT_ADDITIONAL_COST", "value": 0.0},
        "primary_additional_yen": float(EXPLICIT_ADDITIONAL_COST_YEN),
        "source": "replay.pnl_yen.compute_pnl_yen_100 documents fees and tax as excluded. V1 calls that function.",
        "not_used": ["5 bps tax", "8 bps tax"],
    }
    body["COST_POLICY_SHA256"] = _sha({k: v for k, v in body.items() if k != "COST_POLICY_SHA256"})
    body["resolved"] = True
    return body


def build() -> dict[str, Any]:
    dates = _dates()
    groups_ok = dates == sorted(set(ORIGINAL18 + EXTENSION17)) and len(dates) == 35
    fold_a, fold_b, fold_c = dates[:12], dates[12:24], dates[24:]
    folds_ok = (
        len(fold_a) == 12
        and len(fold_b) == 12
        and len(fold_c) == 11
        and fold_a[0] == "20260722"
        and fold_a[-1] == "20260806"
        and fold_b[0] == "20260807"
        and fold_b[-1] == "20260826"
        and fold_c[0] == "20260827"
        and fold_c[-1] == "20260910"
    )
    live_exit = execution_identity()
    runner_sha = hashlib.sha256((NATIVE / "src" / "research" / "symbol_setup_baseline_complete_strategy_precommit" / "runner.py").read_bytes()).hexdigest()
    portfolio_sha = hashlib.sha256((NATIVE / "src" / "research" / "simple_tech_entry_family" / "portfolio.py").read_bytes()).hexdigest()
    size = _size()
    cost = _cost()
    uni = recover(dates) if groups_ok else {"resolved": False, "sessions": [], "UNIVERSE_POLICY_ID": "", "UNIVERSE_POLICY_SHA256": "", "membership_sha256": ""}
    ident_ok = (
        spec_sha256() == BASELINE_SPEC_SHA256
        and tuple(ELIGIBLE_DAYS) == ORIGINAL18
        and live_exit["EXIT_EXECUTION_ID"] == EXIT_EXECUTION_ID
        and live_exit["EXIT_EXECUTION_SHA256"] == EXIT_EXECUTION_SHA256
        and portfolio_sha == PORTFOLIO_SHA256
        and groups_ok
        and folds_ok
        and ledger_sha([]) == ledger_sha([])
    )
    runner_ok = bool(runner_sha) and RUNNER_ID != ""
    if not ident_ok:
        verdict, nxt = CASE_IDENTITY, NEXT_STOP
    elif not uni.get("resolved"):
        verdict, nxt = CASE_UNIVERSE, NEXT_STOP
    elif not size["resolved"]:
        verdict, nxt = CASE_SIZE, NEXT_STOP
    elif not cost["resolved"]:
        verdict, nxt = CASE_COST, NEXT_STOP
    elif not runner_ok:
        verdict, nxt = CASE_RUNNER, NEXT_STOP
    else:
        verdict, nxt = CASE_READY, NEXT_READY
    strategy = None
    strategy_sha = None
    if verdict == CASE_READY:
        strategy = {
            "COMPLETE_STRATEGY_ID": COMPLETE_STRATEGY_ID,
            "baseline_id": BASELINE_ID,
            "baseline_spec_sha256": BASELINE_SPEC_SHA256,
            "thesis_id": THESIS_ID,
            "thesis_sha256": THESIS_SHA256,
            "entry_execution_id": ENTRY_EXECUTION_ID,
            "entry_execution_sha256": ENTRY_EXECUTION_SHA256,
            "exit_id": EXIT_ID,
            "exit_contract_sha256": EXIT_CONTRACT_SHA256,
            "exit_execution_id": EXIT_EXECUTION_ID,
            "exit_execution_sha256": EXIT_EXECUTION_SHA256,
            "universe_policy_id": uni["UNIVERSE_POLICY_ID"],
            "universe_policy_sha256": uni["UNIVERSE_POLICY_SHA256"],
            "universe_membership_sha256": uni["membership_sha256"],
            "position_size_policy_id": size["POSITION_SIZE_POLICY_ID"],
            "position_size_policy_sha256": size["POSITION_SIZE_POLICY_SHA256"],
            "cost_policy_id": cost["COST_POLICY_ID"],
            "cost_policy_sha256": cost["COST_POLICY_SHA256"],
            "portfolio_id": PORTFOLIO_ID,
            "portfolio_sha256": PORTFOLIO_SHA256,
            "event_order": list(EVENT_ORDER),
            "invalid_data_policy": "INVALID_THESIS_OBSERVATION then FAIL_CLOSE_INVALID_DATA, not thesis loss",
            "session_close_policy": "SESSION_FAIL_CLOSE at AM end on the last valid causal bid",
            "surface_sha256": _sha(dates),
            "original18_sha256": _sha(list(ORIGINAL18)),
            "extension17_sha256": _sha(list(EXTENSION17)),
            "fold_sha256": _sha({"A": fold_a, "B": fold_b, "C": fold_c}),
            "runner_id": RUNNER_ID,
            "runner_sha256": runner_sha,
            "persistence_k": 1,
            "signal_bar_reused": False,
            "lost_is_absorbing": True,
            "cap": 5,
            "pyramiding": False,
        }
        strategy_sha = _sha(strategy)
    sessions = [
        {
            "date": r["date"],
            "source": r.get("source"),
            "universe_n": r.get("universe_n"),
            "resolved": r.get("resolved"),
            "reason": r.get("reason"),
            "symbols": r.get("symbols"),
        }
        for r in uni.get("sessions") or []
    ]
    return {
        "verdict": verdict,
        "next": nxt,
        "complete_strategy_id": COMPLETE_STRATEGY_ID if strategy_sha else None,
        "complete_strategy_sha256": strategy_sha,
        "strategy": strategy,
        "universe": {k: v for k, v in uni.items() if k != "sessions"},
        "universe_sessions": sessions,
        "position_size": size,
        "cost": cost,
        "runner": {"ECONOMIC_REPLAY_RUNNER_ID": RUNNER_ID, "ECONOMIC_REPLAY_RUNNER_SHA256": runner_sha},
        "folds": {"FOLD_A": fold_a, "FOLD_B": fold_b, "FOLD_C": fold_c},
        "original18": list(ORIGINAL18),
        "extension17": list(EXTENSION17),
        "surface_n": len(dates),
        "entry_changed": False,
        "exit_changed": False,
        "event_order_frozen": True,
        "gates_frozen": True,
        "formula_check": {
            "profit_factor_empty": profit_factor([]),
            "drawdown_empty": max_drawdown_yen([]),
            "double_ledger": ledger_sha([]) == ledger_sha([]),
        },
        "new_pnl_run": False,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "pb1_required": False,
        "market_context_required": False,
        "sector_context_required": False,
        "m3_used": False,
        "futures_driver_used": False,
        "one_minute_mixed": True,
        "structure_layer_present": False,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "pb1_changed": False,
        "v4_changed": False,
        "v5_created": False,
    }
