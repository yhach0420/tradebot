"""Pinned Full Strategy contract. Frozen C1 10. Canary R2_X1_Z3. No identity change."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_full_strategy_v1 import (
    ANALYSIS_ID,
    C1_BROADENS_ENTRY_POPULATION_CLAIM,
    CANARY_EXPECTED,
    CANARY_ID,
    CANARY_SOURCE,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_D,
    CASE_E,
    DEVELOPMENT_DAYS,
    FOLD_BLOCKS,
    GLOBAL_VWAP_ENTRY_GATE,
    MAX_RESEARCH_DATE,
    NEXT_IF_A,
    NEXT_IF_B,
    NON_VWAP_CANDIDATE_IDS,
    POSITION_CAP,
    PRECOMMIT_ANALYSIS_ID,
    REQUIRED_PRECOMMIT_HASHES,
    SELECTED_STRATEGY_SYMBOL_FILTER,
    SHARES,
    TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY,
    VWAP_CANDIDATE_IDS,
)
from research.c1_multi_timeframe_precommit_v1 import RAW_CANDIDATE_IDS
from research.c1_multi_timeframe_precommit_v1.duplicate_audit import apply_prune, audit_raw_library
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library
from research.c1_multi_timeframe_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    dumps_sha256,
    economic_gates,
    exact_entry_rule,
    execution_contract,
    exit_contract,
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)

__all__ = ["dumps_sha256", "frozen_library", "candidate_ids", "canonical_spec", "spec_sha256", "source_sha256", "parse_candidate_id"]

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "precommit_pin.py",
    "entries.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def frozen_library() -> list[dict[str, Any]]:
    raw = build_raw_library()
    dup = audit_raw_library(raw)
    final, counts = apply_prune(raw, dup)
    if len(final) != 10 or int(counts["FINAL_CANDIDATE_N"]) != 10:
        raise RuntimeError("FROZEN_LIBRARY_N")
    if int(counts["DUPLICATE_CANDIDATE_N"]) != 0 or int(counts["CLOSED_LINEAGE_CANDIDATE_N"]) != 0:
        raise RuntimeError("FROZEN_LIBRARY_PRUNE")
    ids = [str(r["CANDIDATE_ID"]) for r in final]
    if ids != list(RAW_CANDIDATE_IDS):
        raise RuntimeError("FROZEN_LIBRARY_IDS")
    return final


def candidate_ids() -> list[str]:
    return [str(r["CANDIDATE_ID"]) for r in frozen_library()]


def parse_candidate_id(cid: str) -> dict[str, str]:
    if cid.startswith("MTF_3M__"):
        htf = "HTF_3M"
        sid = cid[len("MTF_3M__") :]
    elif cid.startswith("MTF_5M__"):
        htf = "HTF_5M"
        sid = cid[len("MTF_5M__") :]
    else:
        raise RuntimeError(f"BAD_CANDIDATE_ID:{cid}")
    vwap_gate = cid in VWAP_CANDIDATE_IDS
    return {
        "CANDIDATE_ID": cid,
        "HTF_ID": htf,
        "STATE_ID": sid,
        "VWAP_ENTRY_GATE": vwap_gate,
    }


def canonical_spec() -> dict[str, Any]:
    lib = frozen_library()
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PRECOMMIT_ANALYSIS_ID": PRECOMMIT_ANALYSIS_ID,
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
        "PRIMARY_METRIC": "FULL_CAUSAL_PORTFOLIO_ECONOMICS",
        "C1_BROADENS_ENTRY_POPULATION_CLAIM": C1_BROADENS_ENTRY_POPULATION_CLAIM,
        "SAME_FAMILY_ONLY": True,
        "CROSS_FAMILY_GRID": False,
        "GLOBAL_VWAP_ENTRY_GATE": GLOBAL_VWAP_ENTRY_GATE,
        "VWAP_CANDIDATE_N": len(VWAP_CANDIDATE_IDS),
        "NON_VWAP_CANDIDATE_N": len(NON_VWAP_CANDIDATE_IDS),
        "VWAP_CANDIDATE_IDS": list(VWAP_CANDIDATE_IDS),
        "NON_VWAP_CANDIDATE_IDS": list(NON_VWAP_CANDIDATE_IDS),
        "CANDIDATE_N": 10,
        "CANDIDATE_IDS": [str(r["CANDIDATE_ID"]) for r in lib],
        "CANDIDATE11": False,
        "CANDIDATE_BACKFILL": False,
        "EXACT_ENTRY_RULE": exact_entry_rule(),
        "CANARY_ID": CANARY_ID,
        "CANARY_SOURCE": CANARY_SOURCE,
        "CANARY_EXPECTED": dict(CANARY_EXPECTED),
        "CANARY": canary_spec(),
        "EXECUTION": execution_contract(),
        "EXIT": exit_contract(),
        "PORTFOLIO": portfolio_contract(),
        "SHARES": int(SHARES),
        "POSITION_CAP": int(POSITION_CAP),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "FOLD_BLOCKS": {k: list(v) for k, v in FOLD_BLOCKS.items()},
        "COVERAGE_GATES": coverage_gates(),
        "FOLD_COVERAGE_GATES": fold_coverage_gates(),
        "ECONOMIC_GATES": economic_gates(),
        "STABILITY_GATES": stability_gates(),
        "FOLD_ASSIGNMENT": fold_assignment(),
        "REQUIRED_PRECOMMIT_HASHES": dict(REQUIRED_PRECOMMIT_HASHES),
        "SELECTED_STRATEGY_SYMBOL_FILTER": SELECTED_STRATEGY_SYMBOL_FILTER,
        "TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY": TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY,
        "CAUSAL_EX_TOP1_PART_OF_STRATEGY": False,
        "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
        "WINNER_BLOCK_SYMBOL_FILTER_N": 0,
        "V7_SAME_BUCKET_JOIN_USED": False,
        "HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME": False,
        "HTF_VWAP_USES_CANONICAL_SESSION_VWAP": True,
        "STRESS_SEALED": True,
        "HOLDOUT_SEALED": True,
        "SIZING": False,
        "RETUNE": False,
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_C": CASE_C,
        "CASE_D": CASE_D,
        "CASE_E": CASE_E,
        "CASE_CANARY": CASE_CANARY,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_B": NEXT_IF_B,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def spec_sha256(spec: dict[str, Any] | None = None) -> str:
    return dumps_sha256(spec or canonical_spec())


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()
