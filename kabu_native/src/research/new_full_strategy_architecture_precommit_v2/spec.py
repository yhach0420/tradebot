"""Pinned V2 causal-hardening contract. No economics. No core retune."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_full_strategy_architecture_precommit_v2 import (
    ANALYSIS_ID,
    ARBITRARY_BREADTH_DELAY_ADDED,
    BOARD_PRIMARY_ALPHA,
    CASE_FROZEN,
    CASE_UNRESOLVED,
    CORE_ARCHITECTURE_CHANGED,
    NEW_ALPHA_PRIMITIVE,
    NEW_ARCHITECTURE_ECONOMICS_RUN,
    NEW_ARCHITECTURE_PNL_READ_N,
    NEW_REPLAY,
    NEW_THRESHOLD,
    NEXT_IF_FROZEN,
    NEXT_IF_UNRESOLVED,
    PARENT_ANALYSIS_ID,
    PARENT_ARCHITECTURE_ID,
    PARENT_SPEC_SHA256,
    Q1_NEW_LOGIC_COMPLETION_DIRECT,
    Q2_BLOCKING_WITHOUT_THIS_RUN,
    Q3_OLD_RCA_AS_PURPOSE,
    STOP_SESSION_CLOSE,
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

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "source_audit.py",
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
        "PARENT_ANALYSIS_ID": PARENT_ANALYSIS_ID,
        "PARENT_ARCHITECTURE_ID": PARENT_ARCHITECTURE_ID,
        "PARENT_SPEC_SHA256": PARENT_SPEC_SHA256,
        "ADDENDUM_ONLY": True,
        "NO_PNL": True,
        "NO_REPLAY": True,
        "NO_ECONOMICS": True,
        "Q1": Q1_NEW_LOGIC_COMPLETION_DIRECT,
        "Q2": Q2_BLOCKING_WITHOUT_THIS_RUN,
        "Q3": Q3_OLD_RCA_AS_PURPOSE,
        "CORE_ARCHITECTURE_CHANGED": CORE_ARCHITECTURE_CHANGED,
        "NEW_ALPHA_PRIMITIVE": NEW_ALPHA_PRIMITIVE,
        "NEW_THRESHOLD": NEW_THRESHOLD,
        "ARBITRARY_BREADTH_DELAY_ADDED": ARBITRARY_BREADTH_DELAY_ADDED,
        "VWAP_ENTRY_USED": VWAP_ENTRY_USED,
        "VWAP_EXIT_USED": VWAP_EXIT_USED,
        "VWAP_FILTER_USED": VWAP_FILTER_USED,
        "BOARD_PRIMARY_ALPHA": BOARD_PRIMARY_ALPHA,
        "NEW_REPLAY": NEW_REPLAY,
        "NEW_ARCHITECTURE_ECONOMICS_RUN": NEW_ARCHITECTURE_ECONOMICS_RUN,
        "NEW_ARCHITECTURE_PNL_READ_N": NEW_ARCHITECTURE_PNL_READ_N,
        "SHARES": int(SHARES),
        "CAP": int(POSITION_CAP),
        "MA_PREDICATE_UNCHANGED": True,
        "N_UP_DELTA_CONCEPT_UNCHANGED": True,
        "ONSET_FALSE_TO_TRUE_UNCHANGED": True,
        "X1_IDENTITY_UNCHANGED": True,
        "Z_MA_TREND_LOSS_CONCEPT_UNCHANGED": True,
        "SAME_SYMBOL_UNCHANGED": True,
        "REENTRY_CONCEPT_UNCHANGED": True,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS_SEALED": list(STRESS_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "CASE_FROZEN": CASE_FROZEN,
        "CASE_UNRESOLVED": CASE_UNRESOLVED,
        "NEXT_IF_FROZEN": NEXT_IF_FROZEN,
        "NEXT_IF_UNRESOLVED": NEXT_IF_UNRESOLVED,
        "SESSION_CLOSE_STOP_ID": STOP_SESSION_CLOSE,
        "REQUIRED_GATES": [
            "CAUSAL_UNIVERSE_PASS",
            "CAUSAL_BREADTH_CLOCK_PASS",
            "COMPARABLE_SET_PASS",
            "SIMULTANEOUS_SIGNAL_PASS",
            "X1_PENDING_PASS",
            "QUOTE_FRESHNESS_PASS",
            "EXIT_CAUSAL_PASS",
            "SESSION_CLOSE_CAUSAL_PASS",
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
