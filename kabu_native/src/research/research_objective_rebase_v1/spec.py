"""Pinned rebase contract. Existing artifacts only. No new economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.research_objective_rebase_v1 import (
    ANALYSIS_ID,
    ANY_CANDIDATE_FAMILY_VOTE,
    C4_ANALYSIS_ID,
    C4_NEXT_REQUIRED,
    C4_PRIMARY_CLOSE_REASON,
    C4_VERDICT_REQUIRED,
    C5_CREATED,
    CANDIDATE_COUNT_WEIGHTED_VOTE,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CLASS_IDS,
    INDEPENDENT_INFORMATION_LINEAGE_N,
    LINEAGE_IDS,
    NEW_CANDIDATE,
    NEW_ENTRY,
    NEW_EXIT,
    NEW_FOLD_RUN,
    NEW_FULL_CAUSAL_RUN,
    NEW_PNL_SIMULATION,
    NEW_REPLAY,
    NEW_THRESHOLD,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_C,
    NEXT_IF_D,
    OVERLAY_ID,
    SIZING,
    STRESS_OPENED_THIS_RUN,
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

ARCHITECTURE_TO_LINEAGE = (
    {
        "ARCHITECTURE_ID": "SIMPLE_FULL_STRATEGY_DISCOVERY_V1",
        "LINEAGE_ID": "L1_TECHNICAL_PRICE_STATE",
        "INDEPENDENT_INFORMATION_VOTE": False,
        "NOTE": "Overlapping MA/BB/RCI/volume/VWAP technical state. Collapses into L1.",
    },
    {
        "ARCHITECTURE_ID": "SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1",
        "LINEAGE_ID": "L1_TECHNICAL_PRICE_STATE",
        "INDEPENDENT_INFORMATION_VOTE": False,
        "NOTE": "Same technical families as Simple Full, persist/handoff construction.",
    },
    {
        "ARCHITECTURE_ID": "C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2",
        "LINEAGE_ID": "L1_TECHNICAL_PRICE_STATE",
        "INDEPENDENT_INFORMATION_VOTE": False,
        "NOTE": "Same technical predicates on 3m/5m bars. Not a second information lineage.",
    },
    {
        "ARCHITECTURE_ID": "RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1",
        "LINEAGE_ID": "L2_RECOVERY_RECLAIM_PATH",
        "INDEPENDENT_INFORMATION_VOTE": True,
        "NOTE": "Next-bar reclaim / acceptance path. Distinct from L1 state architecture.",
    },
    {
        "ARCHITECTURE_ID": "PARTICIPATION_ONSET_FULL_STRATEGY_V1",
        "LINEAGE_ID": "L3_ACTIVITY_ONSET",
        "INDEPENDENT_INFORMATION_VOTE": True,
        "NOTE": "volume_percentile_60s onset. Distinct from L1 state architecture.",
    },
    {
        "ARCHITECTURE_ID": "C4_PORTFOLIO_CROWDING_FULL_STRATEGY_V2",
        "LINEAGE_ID": "O1_PORTFOLIO_CROWDING_OVERLAY",
        "INDEPENDENT_INFORMATION_VOTE": False,
        "NOTE": "Admission overlay on ST/PERSIST streams. Does not increment lineage N.",
    },
)


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "EXISTING_ARTIFACT_SYNTHESIS_ONLY": True,
        "NEW_REPLAY": NEW_REPLAY,
        "NEW_CANDIDATE": NEW_CANDIDATE,
        "NEW_ENTRY": NEW_ENTRY,
        "NEW_EXIT": NEW_EXIT,
        "NEW_THRESHOLD": NEW_THRESHOLD,
        "NEW_PNL_SIMULATION": NEW_PNL_SIMULATION,
        "NEW_FULL_CAUSAL_RUN": NEW_FULL_CAUSAL_RUN,
        "NEW_FOLD_RUN": NEW_FOLD_RUN,
        "SIZING": SIZING,
        "STRESS_OPEN": STRESS_OPENED_THIS_RUN,
        "C5_CREATED": C5_CREATED,
        "CANDIDATE_COUNT_WEIGHTED_VOTE": CANDIDATE_COUNT_WEIGHTED_VOTE,
        "ANY_CANDIDATE_FAMILY_VOTE": ANY_CANDIDATE_FAMILY_VOTE,
        "INDEPENDENT_INFORMATION_LINEAGE_N": INDEPENDENT_INFORMATION_LINEAGE_N,
        "LINEAGE_IDS": list(LINEAGE_IDS),
        "OVERLAY_ID": OVERLAY_ID,
        "CLASS_IDS": list(CLASS_IDS),
        "C4_ANALYSIS_ID": C4_ANALYSIS_ID,
        "C4_VERDICT_REQUIRED": C4_VERDICT_REQUIRED,
        "C4_NEXT_REQUIRED": C4_NEXT_REQUIRED,
        "C4_PRIMARY_CLOSE_REASON": C4_PRIMARY_CLOSE_REASON,
        "C4_PRIMARY_CLOSE_REASON_NOT": "I2_ZERO",
        "ARCHITECTURE_TO_LINEAGE": list(ARCHITECTURE_TO_LINEAGE),
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_C": CASE_C,
        "CASE_D": CASE_D,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_B": NEXT_IF_B,
        "NEXT_IF_C": NEXT_IF_C,
        "NEXT_IF_D": NEXT_IF_D,
        "GATES_UNCHANGED": ["Coverage", "G1-G6", "Robust Score", "S1-S6"],
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS_SEALED": list(STRESS_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "REVIVE_FORBIDDEN": [
            "REVIVE_SIMPLE_FULL",
            "REVIVE_E4",
            "REVIVE_RECOVERY",
            "REVIVE_PARTICIPATION_ONSET",
            "REVIVE_STATE_TRANSITION",
            "REVIVE_C1",
            "REVIVE_C4",
            "REVIVE_PFQ",
            "REVIVE_OR",
            "REVIVE_DYNAMIC_ANCHOR",
            "REVIVE_X9",
        ],
        "E1_X9_CLOSED": "NO_STABLE_UNIVERSE_REGIME_SEPARATION",
        "EXIT6_CREATED": False,
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
