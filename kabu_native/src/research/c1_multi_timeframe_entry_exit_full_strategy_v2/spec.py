"""Pinned Full Strategy V2 contract. Uniform 40-pair rerun. Raw ENTRY stream invariance."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_entry_exit_full_strategy_v2 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_E,
    DEV_CLASSIFICATION,
    DEVELOPMENT_DAYS,
    DROPPED_EXIT_IDS,
    EXECUTION_ID,
    FROZEN_PAIRS,
    KEPT_EXIT_IDS,
    MAX_RESEARCH_DATE,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_C,
    NON_VWAP_CANDIDATE_IDS,
    POSITION_CAP,
    PRECOMMIT_ANALYSIS_ID,
    PRIOR_Z3_ONLY_ANALYSIS_ID,
    REQUIRED_V2_HASHES,
    SELECTED_STRATEGY_SYMBOL_FILTER,
    SHARES,
    TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY,
    VWAP_CANDIDATE_IDS,
    Z3_PREVIOUSLY_OBSERVED_PAIR_N,
    Z3_SLICE_PREVIOUSLY_OBSERVED,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import pair_id
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.equivalence import build_final_pairs
from research.c1_multi_timeframe_full_strategy_v1.spec import parse_candidate_id
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
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)
from research.systematic_state_transition_full_strategy_v1 import CANARY_EXPECTED, CANARY_ID, CANARY_SOURCE
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "prior_pin.py",
    "streams.py",
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
    return list(FROZEN_PAIRS)


def parse_pair_id(cid: str) -> dict[str, Any]:
    marker = f"__{EXECUTION_ID}__"
    if marker not in cid:
        raise RuntimeError(f"BAD_PAIR_ID:{cid}")
    entry_id, exit_id = cid.split(marker, 1)
    if exit_id in DROPPED_EXIT_IDS or exit_id == "Z4_TRAILING_STRUCTURE":
        raise RuntimeError(f"Z4_PAIR:{cid}")
    if exit_id not in KEPT_EXIT_IDS:
        raise RuntimeError(f"UNKNOWN_EXIT:{cid}")
    meta = parse_candidate_id(entry_id)
    return {
        "CANDIDATE_ID": cid,
        "ENTRY_ID": entry_id,
        "EXECUTION": EXECUTION_ID,
        "EXIT_ID": exit_id,
        "HTF_ID": meta["HTF_ID"],
        "STATE_ID": meta["STATE_ID"],
        "VWAP_ENTRY_GATE": bool(meta["VWAP_ENTRY_GATE"]),
    }


def frozen_pairs() -> list[dict[str, Any]]:
    rows = build_final_pairs(list(KEPT_EXIT_IDS))
    ids = [str(r["CANDIDATE_ID"]) for r in rows]
    if ids != list(FROZEN_PAIRS):
        raise RuntimeError("FROZEN_PAIR_DRIFT")
    if any("Z4_TRAILING_STRUCTURE" in x for x in ids):
        raise RuntimeError("Z4_IN_PAIRS")
    if any(pair_id(e, z) not in ids for e in RAW_CANDIDATE_IDS for z in KEPT_EXIT_IDS):
        raise RuntimeError("PAIR_COVERAGE")
    return rows


def canonical_spec() -> dict[str, Any]:
    lib = frozen_library()
    pairs = frozen_pairs()
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PRECOMMIT_ANALYSIS_ID": PRECOMMIT_ANALYSIS_ID,
        "DEV_CLASSIFICATION": DEV_CLASSIFICATION,
        "PRIMARY_DECISION_UNIT": "FULL_CAUSAL_STRATEGY_PAIR",
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SAME_SYMBOL+OCCUPANCY+SLOT_RELEASE+REENTRY",
        "PRIMARY_GOAL": "FULL_CAUSAL_PORTFOLIO_ECONOMICS",
        "ENTRY_FIRST_SELECTION": False,
        "EXIT_FIRST_SELECTION": False,
        "C1_BROADENS_ENTRY_POPULATION_CLAIM": False,
        "SAME_FAMILY_ONLY": True,
        "CROSS_FAMILY_GRID": False,
        "GLOBAL_VWAP_ENTRY_GATE": False,
        "VWAP_CANDIDATE_N": len(VWAP_CANDIDATE_IDS),
        "NON_VWAP_CANDIDATE_N": len(NON_VWAP_CANDIDATE_IDS),
        "VWAP_CANDIDATE_IDS": list(VWAP_CANDIDATE_IDS),
        "NON_VWAP_CANDIDATE_IDS": list(NON_VWAP_CANDIDATE_IDS),
        "ENTRY_N": 10,
        "ENTRY_IDS": [str(r["CANDIDATE_ID"]) for r in lib],
        "EXECUTION_ID": EXECUTION_ID,
        "FINAL_EXIT_N": 4,
        "FINAL_EXIT_IDS": list(KEPT_EXIT_IDS),
        "DROPPED_EXIT_IDS": list(DROPPED_EXIT_IDS),
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
        "FINAL_PAIR_N": 40,
        "PAIR_IDS": [str(r["CANDIDATE_ID"]) for r in pairs],
        "PAIR_RERUN_N": 40,
        "PRIOR_Z3_METRIC_REUSE_N": 0,
        "PRIOR_Z3_ONLY_ANALYSIS_ID": PRIOR_Z3_ONLY_ANALYSIS_ID,
        "Z3_SLICE_PREVIOUSLY_OBSERVED": bool(Z3_SLICE_PREVIOUSLY_OBSERVED),
        "Z3_PREVIOUSLY_OBSERVED_PAIR_N": int(Z3_PREVIOUSLY_OBSERVED_PAIR_N),
        "Z3_NOT_FRESH": True,
        "TRUE_OOS": False,
        "EXACT_ENTRY_RULE": exact_entry_rule(),
        "CANARY_ID": CANARY_ID,
        "CANARY_SOURCE": CANARY_SOURCE,
        "CANARY_EXPECTED": dict(CANARY_EXPECTED),
        "CANARY": canary_spec(),
        "EXECUTION": execution_contract(),
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
        "REQUIRED_V2_HASHES": dict(REQUIRED_V2_HASHES),
        "SELECTED_STRATEGY_SYMBOL_FILTER": SELECTED_STRATEGY_SYMBOL_FILTER,
        "TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY": TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY,
        "CAUSAL_EX_TOP1_PART_OF_STRATEGY": False,
        "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
        "WINNER_BLOCK_SYMBOL_FILTER_N": 0,
        "RAW_ENTRY_STREAM_EXIT_VARIANT_N": 4,
        "RAW_ENTRY_STREAM_HASH_UNIQUE_N": 1,
        "NEW_EXIT_CREATED": False,
        "EXIT6_CREATED": False,
        "EXIT_PARAMETER_RETUNE": False,
        "ENTRY_PARAMETER_RETUNE": False,
        "TIMEFRAME_RETUNE": False,
        "SYMBOL_FILTER_ADDED": False,
        "VWAP_FILTER_ADDED": False,
        "SIZING": False,
        "STRESS_SEALED": True,
        "HOLDOUT_SEALED": True,
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_C": CASE_C,
        "CASE_E": CASE_E,
        "CASE_CANARY": CASE_CANARY,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_B": NEXT_IF_B,
        "NEXT_IF_C": NEXT_IF_C,
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
