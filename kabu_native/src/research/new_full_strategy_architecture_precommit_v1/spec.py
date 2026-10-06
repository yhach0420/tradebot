"""Pinned precommit contract. No economics. No old ST RCA continuation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_full_strategy_architecture_precommit_v1 import (
    ANALYSIS_ID,
    BOARD_PRIMARY_ALPHA,
    CASE_A,
    CASE_B,
    CLOSED_LINEAGE_IDS,
    FORBIDDEN_NEXT_RUNS,
    NEW_ARCHITECTURE_ECONOMICS_RUN,
    NEW_ARCHITECTURE_PNL_READ_N,
    NEW_REPLAY,
    NEXT_IF_A,
    NEXT_IF_B,
    OLD_RCA_ENDPOINT,
    OLD_RCA_VERDICT,
    OLD_ST_RCA_CONTINUED,
    Q1_NEW_LOGIC_COMPLETION_DIRECT,
    Q2_BLOCKING_WITHOUT_THIS_RUN,
    Q3_OLD_RCA_AS_PURPOSE,
    RAW_CAPTURE_READ_N,
    VWAP_ENTRY_USED,
    VWAP_EXIT_USED,
    VWAP_FILTER_USED,
)
from research.simple_full_strategy_discovery_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    POSITION_CAP,
    SHARES,
    STRESS_DAYS,
)
from research.systematic_state_transition_library_precommit_v1.spec import (
    execution_contract,
    portfolio_contract,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "closed.py",
    "proposals.py",
    "eligibility.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def dumps_sha256(obj: Any) -> str:
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PRECOMMIT_ONLY": True,
        "NO_PNL": True,
        "NO_REPLAY": True,
        "NO_ECONOMICS": True,
        "Q1": Q1_NEW_LOGIC_COMPLETION_DIRECT,
        "Q2": Q2_BLOCKING_WITHOUT_THIS_RUN,
        "Q3": Q3_OLD_RCA_AS_PURPOSE,
        "OLD_RCA_ENDPOINT": OLD_RCA_ENDPOINT,
        "OLD_RCA_VERDICT": OLD_RCA_VERDICT,
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "FORBIDDEN_NEXT_RUNS": list(FORBIDDEN_NEXT_RUNS),
        "CLOSED_LINEAGE_IDS": list(CLOSED_LINEAGE_IDS),
        "VWAP_ENTRY_USED": VWAP_ENTRY_USED,
        "VWAP_EXIT_USED": VWAP_EXIT_USED,
        "VWAP_FILTER_USED": VWAP_FILTER_USED,
        "BOARD_PRIMARY_ALPHA": BOARD_PRIMARY_ALPHA,
        "NEW_REPLAY": NEW_REPLAY,
        "NEW_ARCHITECTURE_ECONOMICS_RUN": NEW_ARCHITECTURE_ECONOMICS_RUN,
        "NEW_ARCHITECTURE_PNL_READ_N": NEW_ARCHITECTURE_PNL_READ_N,
        "RAW_CAPTURE_READ_N": RAW_CAPTURE_READ_N,
        "SHARES": int(SHARES),
        "CAP": int(POSITION_CAP),
        "EXECUTION_BASE": execution_contract(),
        "PORTFOLIO_BASE": portfolio_contract(),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS_SEALED": list(STRESS_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_B": NEXT_IF_B,
        "MAX_PROPOSAL_N": 3,
        "SELECTION_PRIORITY": [
            "fewest_discretionary_degrees_of_freedom",
            "fewest_distinct_signal_primitives",
            "simplest_causal_state_machine",
            "highest_expected_coverage_plausibility",
            "lowest_implementation_ambiguity",
            "lexicographic_ARCHITECTURE_ID",
        ],
    }


def spec_sha256() -> str:
    return dumps_sha256(canonical_spec())


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
