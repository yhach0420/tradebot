"""Pinned selection-surface mechanism RCA. Existing ST artifacts only. No new fold economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.selection_surface_mechanism_rca_v1 import (
    ANALYSIS_ID,
    CROSS_LINEAGE_SELECTION,
    FAILING_S1,
    FAILING_S4,
    L1_WIDE,
    NEW_CANDIDATE,
    NEW_ENTRY,
    NEW_EXIT,
    NEW_FOLD_ECONOMICS,
    NEW_FOLD_RUN,
    NEW_REPLAY,
    NEXT_EVIDENCE_GAP,
    PARENT_NEXT_REQUIRED,
    PARENT_PRIMARY_COMPONENT,
    PARENT_RESIDUAL_BOTTLENECK,
    PARENT_VERDICT_REQUIRED,
    RAW_CAPTURE_READ_N,
    ST_SPECIFIC,
    ST_WINNER_ID,
    VERDICT_INCONCLUSIVE,
)
from research.simple_full_strategy_discovery_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "sources.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "EXISTING_ARTIFACT_RCA_ONLY": True,
        "NEW_REPLAY": NEW_REPLAY,
        "NEW_FOLD_RUN": NEW_FOLD_RUN,
        "NEW_FOLD_ECONOMICS": NEW_FOLD_ECONOMICS,
        "NEW_ENTRY": NEW_ENTRY,
        "NEW_EXIT": NEW_EXIT,
        "NEW_CANDIDATE": NEW_CANDIDATE,
        "RAW_CAPTURE_READ_N": RAW_CAPTURE_READ_N,
        "ST_WINNER_ID": ST_WINNER_ID,
        "PARENT_VERDICT_REQUIRED": PARENT_VERDICT_REQUIRED,
        "PARENT_NEXT_REQUIRED": PARENT_NEXT_REQUIRED,
        "PARENT_PRIMARY_COMPONENT_UNCHANGED": PARENT_PRIMARY_COMPONENT,
        "PARENT_RESIDUAL_BOTTLENECK_SUBJECT": PARENT_RESIDUAL_BOTTLENECK,
        "STABILITY_COMPONENT_N": 4,
        "STABILITY_NOT_ONLY_RECURRENCE_AND_TRANSFER": True,
        "S1": "TRAIN_TOP3_N >= 3/5",
        "S2": "FULL_DEV_WINNER_POSITIVE_BLOCK_N >= 3/5",
        "S3": "FULL_DEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL >= 0",
        "S4": "FOLD_SELECTED_TEST_TOTAL_PNL > 0",
        "S2_S3_USE_FULL_DEV_WINNER_BLOCKS_NOT_FOLD_SELECTED_STORED_FIELDS": True,
        "NO_WINNER_TEST_PNL_NOT_AUTO_ZERO": True,
        "N1_N2_REQUIRE_SAVED_WINNER_GATE_OR_RANK": True,
        "DO_NOT_DERIVE_G6_FROM_BASE_LEDGER_SUBTRACTION": True,
        "DO_NOT_RECONSTRUCT_MISSING_FOLD_METRICS": True,
        "ST_SPECIFIC": ST_SPECIFIC,
        "L1_WIDE": L1_WIDE,
        "CROSS_LINEAGE_SELECTION": CROSS_LINEAGE_SELECTION,
        "FAILING_S1": FAILING_S1,
        "FAILING_S4": FAILING_S4,
        "VERDICT_IF_INCONCLUSIVE": VERDICT_INCONCLUSIVE,
        "NEXT_IF_INCONCLUSIVE": NEXT_EVIDENCE_GAP,
        "RETROSPECTIVE_RCA_ONLY": True,
        "PROSPECTIVE_BENCHMARK": False,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS_SEALED": list(STRESS_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "FILTERS_FORBIDDEN": True,
        "GATE_RELAXATION_FORBIDDEN": True,
        "TOPK_CHANGE_FORBIDDEN": True,
        "ROBUST_SCORE_CHANGE_FORBIDDEN": True,
        "ST_RESCUE_FORBIDDEN": True,
    }


def spec_sha256() -> str:
    body = json.dumps(canonical_spec(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
