"""Diagnose the frozen failure. Do not repair it."""
from __future__ import annotations

import json
import math

from research.symbol_setup_baseline_complete_development.verify import identity
from research.symbol_setup_baseline_failure_decomposition import (
    ANALYSIS_ID,
    CASE_PARITY,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    NEXT_STOP,
)
from research.symbol_setup_baseline_failure_decomposition.decide import analyze
from research.symbol_setup_baseline_failure_decomposition.publish import publish
from research.symbol_setup_baseline_failure_decomposition.scan import PopulationMismatch, scan

FROZEN_ECONOMICS = {
    "trade_n": 7,
    "net_pnl_yen": -31500.0,
    "PF": 0.46153846153846156,
    "win_n": 1,
    "loss_n": 6,
    "mean_trade_yen": -4500.0,
    "median_trade_yen": -3200.0,
    "mean_bps": -44.85126981663367,
    "median_bps": -33.16138540899072,
}


def _groups(pre: dict) -> dict[str, dict[str, str]]:
    original = set(pre["original18"])
    extension = set(pre["extension17"])
    folds = {name: set(days) for name, days in pre["folds"].items()}
    out = {}
    for day in list(original) + list(extension):
        lineage = "ORIGINAL18" if day in original else "EXTENSION17"
        fold = next(name for name, days in folds.items() if day in days)
        out[day] = {"lineage": lineage, "fold": fold}
    return out


def _base(verdict: str, nxt: str, **extra: object) -> dict:
    return {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
        "failed_strategy_mutated": False,
        "new_strategy_candidate_n": 0,
        "new_complete_strategy_run": False,
        "parameter_search": {
            "EMA_PARAMETER_SEARCH": False,
            "RCI_PARAMETER_SEARCH": False,
            "BB_PARAMETER_SEARCH": False,
            "VOLUME_PARAMETER_SEARCH": False,
            "BOARD_THRESHOLD_SEARCH": False,
            "FILL_WAIT_SEARCH": False,
            "EXIT_K_SEARCH": False,
            "TIMEFRAME_STRATEGY_SEARCH": False,
            "STRUCTURE_LOOKBACK_SEARCH": False,
        },
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "no_repair": {
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "NEW_STRATEGY_CANDIDATE_N": 0,
            "NEW_COMPLETE_STRATEGY_RUN": False,
        },
        **extra,
    }


def main() -> int:
    before = identity()
    if not before["ok"]:
        publish(_base("SYMBOL_SETUP_BASELINE_COMPLETE_STRATEGY_IDENTITY_MISMATCH_V1", NEXT_STOP, identity_before_ok=False, checks=before["checks"]))
        print(json.dumps({"VERDICT": "SYMBOL_SETUP_BASELINE_COMPLETE_STRATEGY_IDENTITY_MISMATCH_V1", "NEXT": NEXT_STOP}), flush=True)
        return 2
    pre_path = (
        __import__("pathlib").Path(__file__).resolve().parents[3]
        / "results/research/symbol_setup_baseline_complete_strategy_precommit_v1/report.json"
    )
    failed_path = (
        __import__("pathlib").Path(__file__).resolve().parents[3]
        / "results/research/symbol_setup_baseline_complete_strategy_development_v1/report.json"
    )
    pre = json.loads(pre_path.read_text(encoding="utf-8"))
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    primary = failed["primary"]

    def _close(got: object, exp: float) -> bool:
        try:
            return math.isclose(float(got), float(exp), rel_tol=0.0, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False

    # Bind the published failure exactly. Do not rewrite that file.
    checks = {
        "trade_n": primary["trade_n"] == 7,
        "net_pnl_yen": _close(primary["net_pnl_yen"], -31500.0),
        "PF": _close(primary["PF"], 0.46153846153846156),
        "win_n": primary["win_n"] == 1,
        "loss_n": primary["loss_n"] == 6,
        "mean_trade_yen": _close(primary["mean_net_trade_yen"], -4500.0),
        "median_trade_yen": _close(primary["median_net_trade_yen"], -3200.0),
        "mean_bps": _close(primary["mean_net_bps"], -44.85126981663367),
        "median_bps": _close(primary["median_net_bps"], -33.16138540899072),
        "sha": failed["complete_strategy_sha256"] == COMPLETE_STRATEGY_SHA256,
    }
    if not all(checks.values()):
        publish(_base(CASE_PARITY, NEXT_STOP, population_parity=False, frozen_bind=checks))
        print(json.dumps({"VERDICT": CASE_PARITY, "NEXT": NEXT_STOP, "bind": checks}), flush=True)
        return 2
    try:
        scanned = scan(before["universe_sessions"], _groups(pre))
    except PopulationMismatch as exc:
        publish(_base(CASE_PARITY, NEXT_STOP, population_parity=False, mismatch=exc.detail, frozen_failure=FROZEN_ECONOMICS))
        print(json.dumps({"VERDICT": CASE_PARITY, "NEXT": NEXT_STOP, "mismatch": exc.detail}, default=str), flush=True)
        return 2
    after = identity()
    analysis = analyze(scanned)
    report = _base(
        analysis["verdict"],
        analysis["next"],
        identity_before_ok=True,
        identity_after_ok=bool(after["ok"]),
        failed_strategy_mutated=not bool(after["ok"]),
        population_parity=True,
        population={"technical_signal_n": 121, "board_pass_n": 44, "board_veto_n": 77, "passive_fill_n": 7, "passive_expired_n": 37},
        frozen_failure=FROZEN_ECONOMICS,
        primary_deficiency=analysis["primary_deficiency"],
        analysis=analysis,
        signals=scanned["signals"],
        pending_rows=scanned["pending"],
        trades=scanned["trades"],
        structure_status="STRUCTURE_DIAGNOSTIC_NOT_RUN",
    )
    publish(report)
    print(json.dumps({"VERDICT": analysis["verdict"], "NEXT": analysis["next"], "PRIMARY": analysis["primary_deficiency"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
