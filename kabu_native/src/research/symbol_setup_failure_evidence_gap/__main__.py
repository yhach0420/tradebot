"""Resolve the evidence gap. Do not repair the frozen strategy."""
from __future__ import annotations

import json
import math
from pathlib import Path

from research.symbol_setup_baseline_complete_development.verify import identity
from research.symbol_setup_failure_evidence_gap import (
    ANALYSIS_ID,
    CANONICAL_STAGE_ORDER,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    HISTORICAL_VERDICT,
    NEXT_PARITY,
    NEXT_STOP,
    VERDICT_PARITY,
    VERDICT_PRICE_PARITY,
)
from research.symbol_setup_failure_evidence_gap.decide import analyze
from research.symbol_setup_failure_evidence_gap.publish import publish
from research.symbol_setup_failure_evidence_gap.scan import PopulationMismatch, PriceParityError, scan

RCA_STAGE_ORDER = (
    "MA_TREND",
    "BB_LOCATION",
    "RCI_REVERSAL",
    "PRICE_ACTION_TRIGGER",
    "VOLUME_PARTICIPATION",
    "BOARD_SUPPORT_VETO",
)
NATIVE = Path(__file__).resolve().parents[3]


def _groups(pre: dict) -> dict[str, dict[str, str]]:
    original = set(pre["original18"])
    extension = set(pre["extension17"])
    folds = {name: set(days) for name, days in pre["folds"].items()}
    out = {}
    for day in list(original) + list(extension):
        lineage = "ORIGINAL18" if day in original else "EXTENSION17"
        fold = next(name for name, days in folds.items() if day in days)
        out[str(day)] = {"lineage": lineage, "fold": fold}
    return out


def _close(got: object, exp: float) -> bool:
    try:
        return math.isclose(float(got), float(exp), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _base(verdict: str, nxt: str, **extra: object) -> dict:
    return {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
        "historical_verdict_preserved": HISTORICAL_VERDICT,
        "failed_strategy_mutated": False,
        "new_strategy_candidate_n": 0,
        "new_complete_strategy_run": False,
        "parameter_search": False,
        "execution_search": False,
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
        "stage_order_audit": {
            "CANONICAL_STAGE_ORDER": list(CANONICAL_STAGE_ORDER),
            "RCA_STAGE_ORDER": list(RCA_STAGE_ORDER),
            "ORDER_MATCH": list(CANONICAL_STAGE_ORDER) == list(RCA_STAGE_ORDER),
            "DISPLAY_ONLY_MISMATCH": False,
            "COMPUTATION_ORDER_MISMATCH": list(CANONICAL_STAGE_ORDER) != list(RCA_STAGE_ORDER),
            "note": "The failure-decomposition scanner filtered PRICE_ACTION before VOLUME. That was the computed funnel, not a display label. Harvest entry remains an AND of the same predicates and was not reordered.",
        },
        **extra,
    }


def main() -> int:
    before = identity()
    if not before["ok"]:
        publish(_base("SYMBOL_SETUP_BASELINE_COMPLETE_STRATEGY_IDENTITY_MISMATCH_V1", NEXT_STOP, identity_before_ok=False))
        print(json.dumps({"VERDICT": "SYMBOL_SETUP_BASELINE_COMPLETE_STRATEGY_IDENTITY_MISMATCH_V1", "NEXT": NEXT_STOP}), flush=True)
        return 2
    pre = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_precommit_v1/report.json").read_text(encoding="utf-8"))
    failed = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_development_v1/report.json").read_text(encoding="utf-8"))
    historical = json.loads((NATIVE / "results/research/symbol_setup_baseline_failure_decomposition_v1/report.json").read_text(encoding="utf-8"))
    primary = failed["primary"]
    checks = {
        "trade_n": primary["trade_n"] == 7,
        "net_pnl_yen": _close(primary["net_pnl_yen"], -31500.0),
        "PF": _close(primary["PF"], 0.46153846153846156),
        "sha": failed["complete_strategy_sha256"] == COMPLETE_STRATEGY_SHA256,
        "historical": historical.get("verdict") == HISTORICAL_VERDICT,
    }
    if not all(checks.values()):
        publish(_base(VERDICT_PARITY, NEXT_STOP, frozen_bind=checks))
        print(json.dumps({"VERDICT": VERDICT_PARITY, "NEXT": NEXT_STOP}), flush=True)
        return 2
    try:
        scanned = scan(before["universe_sessions"], _groups(pre))
    except PopulationMismatch as exc:
        publish(_base(VERDICT_PARITY, NEXT_PARITY, population_parity=False, mismatch=exc.detail))
        print(json.dumps({"VERDICT": VERDICT_PARITY, "NEXT": NEXT_PARITY}, default=str), flush=True)
        return 2
    except PriceParityError as exc:
        publish(_base(VERDICT_PRICE_PARITY, NEXT_STOP, price_parity=False, mismatch=exc.detail))
        print(json.dumps({"VERDICT": VERDICT_PRICE_PARITY, "NEXT": NEXT_STOP}, default=str), flush=True)
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
        corrected_technical_signal_n=scanned["technical"],
        corrected_board_pass_n=scanned["board"],
        price_decomposition_parity_fail_n=scanned["fails"],
        frozen_failure={"trade_n": 7, "net_pnl_yen": -31500.0, "PF": 0.46153846153846156},
        primary_deficiency=analysis["primary_deficiency"],
        analysis=analysis,
        signals=scanned["signals"],
    )
    publish(report)
    print(
        json.dumps(
            {
                "VERDICT": analysis["verdict"],
                "NEXT": analysis["next"],
                "CLASS": analysis["price_reference_classification"],
                "PRIMARY": analysis["primary_deficiency"],
                "ORDER_MATERIAL": analysis["order_effect"]["material_change"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
