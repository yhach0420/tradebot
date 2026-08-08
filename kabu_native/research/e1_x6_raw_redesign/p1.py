"""P1 freeze for Phase A: full decision-package registration before any economics.

Contains no future labels, no PnL, no per-trade results (verified by test:
P1 builds without any label/economics input).
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .evaluation_plan import PHASE_B_EVALUATION_PLAN
from .exits import EXIT_EVALUATION_ORDER, EXIT_PACKAGES, INVALIDATION_DEFINITIONS
from .features import (
    FEATURE_FORMULAS,
    FRESH_MAX_AGE_SEC,
    GRID_STEP_SEC,
    NO_ENTRY_TAIL_SEC,
    NOT_EVALUABLE_REASONS,
    SESSION_TIMES,
    SPREAD_MAX_BPS,
    WARMUP_SEC,
)
from .regime import REGIME_DEFINITIONS
from .setups import CHASE_REJECT, CONFIRMATION_LEVELS, SETUP_DEFINITIONS
from .store import sha256_file, sha256_obj

FORBIDDEN_INPUTS = [
    "entry_score_v2", "existing score", "score threshold", "score gap",
    "score slope/acceleration", "legacy ENTRY adoption", "E1_X5 decisions",
    "future MFE/MAE", "future PnL", "post-exit information",
    "date-specific conditions", "symbol-specific conditions",
    "7/22- or 7/31-only conditions", "result-driven time-of-day exclusion",
]


def _code_shas() -> dict[str, str]:
    pkg = Path(__file__).resolve().parent
    out = {}
    for fp in sorted(pkg.glob("*.py")):
        out[f"research/e1_x6_raw_redesign/{fp.name}"] = hashlib.sha256(fp.read_bytes()).hexdigest()
    tests = pkg.parents[1] / "tests" / "research" / "e1_x6_raw_redesign"
    if tests.is_dir():
        for fp in sorted(tests.glob("*.py")):
            out[f"tests/research/e1_x6_raw_redesign/{fp.name}"] = hashlib.sha256(fp.read_bytes()).hexdigest()
    return out


def _dependency_versions() -> dict[str, str]:
    import numpy

    out = {"python": sys.version.split()[0], "numpy": numpy.__version__}
    try:
        import openpyxl

        out["openpyxl"] = openpyxl.__version__
    except ImportError:
        pass
    try:
        import psutil

        out["psutil"] = psutil.__version__
    except ImportError:
        pass
    return out


def build_p1_lock(
    *,
    run_id: str,
    plan_doc_path: Path,
    source_manifest_sha256: str,
    protected_manifest_sha256: str,
    inventory_summary: dict[str, Any],
    field_usability: dict[str, Any],
    registry: list[dict[str, Any]],
    r1: dict[str, Any] | None = None,
    r2: dict[str, Any] | None = None,
    r3: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p1 = {
        "run_id": run_id,
        "plan_id": "E1_X6_PLAN_V3_RAW_FEATURE_REDESIGN",
        "phase": "PHASE_A_P1_FREEZE",
        "plan_doc_sha256": sha256_file(plan_doc_path) if plan_doc_path.is_file() else None,
        "source_manifest_sha256": source_manifest_sha256,
        "paper_protected_manifest_sha256": protected_manifest_sha256,
        "scope_note": (
            "E1_X6_NO_ROBUST_JOINT_STRATEGY applies ONLY to the 396 score-based "
            "strategies (stage-1 200 + stage-2 196); it is NOT the final verdict "
            "on ENTRY redesign. Plan 2.1 artifacts are preserved unchanged."
        ),
        "grid_spec": {
            "grid_step_sec": GRID_STEP_SEC,
            "sessions_jst": {k: [f"{a[0]:02d}:{a[1]:02d}", f"{b[0]:02d}:{b[1]:02d}"]
                             for k, (a, b) in SESSION_TIMES.items()},
            "warmup_sec": WARMUP_SEC,
            "no_entry_tail_sec": NO_ENTRY_TAIL_SEC,
            "freshness_max_age_sec": FRESH_MAX_AGE_SEC,
            "spread_max_bps": SPREAD_MAX_BPS,
            "one_eval_per_symbol_grid": True,
            "not_evaluable_reasons": list(NOT_EVALUABLE_REASONS),
            "am_pm_rolling_separated": True,
            "market_aggregation": "leave-one-out (entry symbol excluded)",
            "as_of_rule": "only timestamps <= t; no future interpolation; NaN never filled",
        },
        "forbidden_inputs": FORBIDDEN_INPUTS,
        "feature_formulas": FEATURE_FORMULAS,
        "field_usability": field_usability,
        "regime_definitions": REGIME_DEFINITIONS,
        "setup_state_machines": SETUP_DEFINITIONS,
        "state_machine_order": "IDLE -> SETUP -> TRIGGERED -> CONFIRM -> OPEN",
        "confirmation_levels": CONFIRMATION_LEVELS,
        "chase_reject": CHASE_REJECT,
        "exit_packages": EXIT_PACKAGES,
        "invalidation_definitions": INVALIDATION_DEFINITIONS,
        "exit_evaluation_order": EXIT_EVALUATION_ORDER,
        "candidate_registry_n": len(registry),
        "candidate_registry_sha256": sha256_obj(registry),
        "candidate_ids": [r["strategy_id"] for r in registry],
        "phase_b_evaluation_plan": PHASE_B_EVALUATION_PLAN,
        "inventory_summary": inventory_summary,
        "code_file_shas": _code_shas(),
        "dependency_versions": _dependency_versions(),
        "safety": {"submit": 0, "cancel": 0, "live": 0},
        "created_at_jst": datetime.now().astimezone().isoformat(),
    }
    if r1:
        from .evaluation_plan import (
            CAP5_CONVENTION,
            LODO_MODES,
            ROLLING_ORIGIN_5FOLD,
            SENS_722,
        )
        from .exit_pricing import (
            EXIT_B_TRAILING_FORMULA,
            EXIT_PRICE_BASIS,
            NO_PROGRESS_FORMULA,
        )
        from .replay_order import REPLAY_ORDER_CONTRACT
        from .windows import CENSOR_POLICY

        p1["phase"] = "PHASE_A_R1_P1_FREEZE"
        p1["r1"] = {
            "superseded_previous": r1.get("superseded_previous"),
            "coverage_method": {
                "old": "1 - raw event-row field missing rate (WRONG as as-of coverage; kept for diff only)",
                "new": "as-of 5s-grid coverage: universe x quality-valid grids denominator, "
                       "field-specific age<=30s, usable_ts=max(ingress,source), value validity",
            },
            "coverage_old_vs_new_diff": r1.get("coverage_diff"),
            "field_usability_r1": r1.get("field_usability_r1"),
            "replay_order_contract": REPLAY_ORDER_CONTRACT,
            "canonical_regression_audit": r1.get("canonical_regressions"),
            "analysis_mask_r1": r1.get("analysis_mask"),
            "censor_policy": CENSOR_POLICY,
            "tick_resolver": r1.get("tick_resolver"),
            "exit_price_basis": EXIT_PRICE_BASIS,
            "exit_b_trailing_formula": EXIT_B_TRAILING_FORMULA,
            "no_progress_formula": NO_PROGRESS_FORMULA,
            "rolling_origin_5fold": ROLLING_ORIGIN_5FOLD,
            "lodo_modes": LODO_MODES,
            "sens_722": SENS_722,
            "cap5_convention": CAP5_CONVENTION,
            "e1x5_base_binding": r1.get("base_binding"),
        }
    if r2:
        from .decision_coverage import (
            DENOMINATOR_DEFINITION_B,
            MKT_LOO_MIN,
            SNAPSHOT_FRESH_SEC,
        )

        p1["phase"] = "PHASE_A_R2_P1_FREEZE"
        p1["r2"] = {
            "r1_block_evidence": r2.get("r1_block_evidence"),
            "coverage_three_way": {
                "A_FULL_GRID_STATE_COVERAGE": (
                    "universe x all 5s grids; DIAGNOSTIC ONLY, never a USABLE/"
                    "UNUSABLE gate; R1 quote min 0.752485 preserved verbatim"
                ),
                "B_DECISION_QUOTE_COVERAGE": DENOMINATOR_DEFINITION_B
                + " Numerator: finite bid/ask, both >0, bid<=ask, spread healthy, "
                  "snapshot freshness pass, no source conflict. Gate: >=0.90 in "
                  "every included session (threshold NOT lowered).",
                "C_MARKET_CONTEXT_COVERAGE": (
                    f"share of decision opportunities with leave-one-out "
                    f"mkt_evaluable_n >= {MKT_LOO_MIN}; gate >=0.90 per included session"
                ),
            },
            "no_changes": [
                "universe (50/day) not reduced post-hoc",
                "0.90 thresholds not lowered",
                f"freshness {SNAPSHOT_FRESH_SEC:.0f}s not extended",
                "no liquidity screening from the 9-day results",
                "no session exclusion to pass coverage",
                "candidate / ENTRY / EXIT thresholds unchanged from R1",
            ],
            "due_semantics": (
                "ENTRY state machines evaluate a symbol-grid ONLY when >=1 raw "
                "PUSH of that symbol arrived in the grid (availability order, "
                "last state in grid, 1 evaluation per symbol per grid); "
                "NOT_DUE_NO_SYMBOL_UPDATE grids hold state and are excluded from "
                "the decision-coverage denominator; feature ledger keeps 30s "
                "as-of carry; late events never backfill past grids"
            ),
            "timestamp_policy": r2.get("timestamp_policy"),
            "source_semantics_proof": r2.get("source_semantics"),
            "incomplete_lookback_rule": (
                "NOT_EVALUABLE_INCOMPLETE_LOOKBACK is a per-opportunity "
                "ineligibility reason (300s continuous as-of lookback, no >30s "
                "carry, no gap/session/window crossing, no future events); it "
                "never makes the quote field UNUSABLE"
            ),
            "decision_gates": r2.get("decision_gates"),
            "tick_official": r2.get("tick_official"),
            "base_binding_r2": r2.get("base_binding_r2"),
        }
    if r3:
        from .decision_coverage_r3 import DENOMINATOR_DEFINITION_STRUCTURAL

        p1["phase"] = "PHASE_A_R3_P1_FREEZE"
        p1["r3"] = {
            "r2_block_evidence": r3.get("r2_block_evidence"),
            "structural_vs_spread": r3.get("structural_vs_spread"),
            "structural_denominator": DENOMINATOR_DEFINITION_STRUCTURAL,
            "decision_gates": r3.get("decision_gates"),
            "tick_official": r3.get("tick_official"),
            "base_binding_r3": r3.get("base_binding_r3"),
            "field_usability": r3.get("field_usability"),
            "no_changes": [
                "universe 50 unchanged",
                "freshness 30s unchanged",
                "coverage threshold 0.90 unchanged",
                "spread filter 50bps unchanged (strategy filter)",
                "17 included sessions / analysis mask unchanged",
                "24 candidate Strategy IDs / ENTRY / EXIT unchanged",
                "5bps cost / 100 shares / CAP5 unchanged",
                "E1_X5 BASE recut unchanged (binding only reconfirmed)",
            ],
        }
    p1["p1_sha256"] = sha256_obj({k: v for k, v in p1.items() if k != "p1_sha256"})
    return p1
