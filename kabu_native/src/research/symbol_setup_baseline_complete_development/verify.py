"""Confirm the frozen contract before and after the replay."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.simple_tech_entry_family.spec import spec_sha256
from research.symbol_setup_baseline_minimal_completion.contract import execution as entry_execution
from research.symbol_setup_baseline_minimal_completion.thesis import thesis
from research.symbol_setup_baseline_complete_development import (
    BASELINE_SPEC_SHA256,
    COMPLETE_STRATEGY_SHA256,
    COST_SHA256,
    ENTRY_EXECUTION_SHA256,
    EXIT_CONTRACT_SHA256,
    EXIT_EXECUTION_SHA256,
    EXTENSION17_SHA256,
    FOLD_SHA256,
    ORIGINAL18_SHA256,
    PORTFOLIO_SHA256,
    POSITION_SIZE_SHA256,
    RUNNER_SHA256,
    SURFACE_SHA256,
    THESIS_SHA256,
    UNIVERSE_MEMBERSHIP_SHA256,
    UNIVERSE_POLICY_SHA256,
)
from research.symbol_setup_baseline_complete_strategy_precommit.contract import _cost, _sha, _size
from research.symbol_setup_baseline_complete_strategy_precommit.isolation import NATIVE
from research.symbol_setup_baseline_complete_strategy_precommit.universe import policy_sha256, recover
from research.symbol_setup_thesis_aligned_exit_precommit.execution import execution_identity


def _file_sha(rel: str) -> str:
    return hashlib.sha256((NATIVE / rel).read_bytes()).hexdigest()


def identity() -> dict[str, Any]:
    report = json.loads(
        (NATIVE / "results" / "research" / "symbol_setup_baseline_complete_strategy_precommit_v1" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    strategy = report["strategy"]
    dates = [r["date"] for r in report["universe_sessions"]]
    uni = recover(dates)
    exe = execution_identity()
    ent = entry_execution()
    size = _size()
    cost = _cost()
    checks = {
        "complete_strategy": _sha(strategy) == COMPLETE_STRATEGY_SHA256,
        "baseline": spec_sha256() == BASELINE_SPEC_SHA256,
        "thesis": thesis()["THESIS_SHA256"] == THESIS_SHA256,
        "entry_execution": ent["EXECUTION_SHA256"] == ENTRY_EXECUTION_SHA256,
        "exit_contract": strategy["exit_contract_sha256"] == EXIT_CONTRACT_SHA256,
        "exit_execution": exe["EXIT_EXECUTION_SHA256"] == EXIT_EXECUTION_SHA256,
        "portfolio": _file_sha("src/research/simple_tech_entry_family/portfolio.py") == PORTFOLIO_SHA256,
        "universe_policy": policy_sha256() == UNIVERSE_POLICY_SHA256,
        "universe_membership": uni["membership_sha256"] == UNIVERSE_MEMBERSHIP_SHA256 and uni["resolved"],
        "position_size": size["POSITION_SIZE_POLICY_SHA256"] == POSITION_SIZE_SHA256 and size["resolved"],
        "cost": cost["COST_POLICY_SHA256"] == COST_SHA256,
        "runner": _file_sha("src/research/symbol_setup_baseline_complete_strategy_precommit/runner.py") == RUNNER_SHA256,
        "surface": _sha(dates) == SURFACE_SHA256 and len(dates) == 35,
        "original18": _sha(strategy and report["original18"]) == ORIGINAL18_SHA256,
        "extension17": _sha(report["extension17"]) == EXTENSION17_SHA256,
        "folds": _sha({"A": report["folds"]["FOLD_A"], "B": report["folds"]["FOLD_B"], "C": report["folds"]["FOLD_C"]}) == FOLD_SHA256,
        "no_prospective": all(str(d) <= "20260910" for d in dates),
    }
    return {"ok": all(checks.values()), "checks": checks, "dates": dates, "universe_sessions": report["universe_sessions"]}
