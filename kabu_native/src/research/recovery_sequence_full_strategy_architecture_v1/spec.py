"""Pin 6-candidate Recovery Sequence Full Strategy. No 7th. No E4. Stress sealed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.recovery_sequence_full_strategy_architecture_v1 import (
    ANALYSIS_ID,
    CANDIDATE_IDS,
    CANDIDATE_N,
    DEVELOPMENT_DAYS,
    ENTRY_NAMES,
    EXEC_NAMES,
    EXIT_NAMES,
    MAX_RESEARCH_DATE,
    POSITION_CAP,
    SHARES,
    WAIT_SEC,
)
from research.recovery_sequence_full_strategy_architecture_v1.entries import ENTRY_RULE_TEXT, already_executed_check

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "entries.py",
    "execution.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def candidate_ids() -> list[str]:
    return list(CANDIDATE_IDS)


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
        "PRIMARY_METRIC": "FULL_CAUSAL_PORTFOLIO_ECONOMICS",
        "E4_X2_Z3_CLOSED": True,
        "ENTRY_N": 3,
        "EXEC_N": 2,
        "EXIT_N": 1,
        "CANDIDATE_N": int(CANDIDATE_N),
        "CANDIDATE_IDS": candidate_ids(),
        "ENTRY_RULES": dict(ENTRY_RULE_TEXT),
        "ENTRY_NAMES": dict(ENTRY_NAMES),
        "EXEC_NAMES": dict(EXEC_NAMES),
        "EXIT_NAMES": dict(EXIT_NAMES),
        "X1": "first as-of-t0 fresh Ask1 marketable; fill=Ask1",
        "X2": "reference_mid at t0; first Ask1<=mid within 5s; fill=observed Ask1; not resting",
        "EXIT": "Z3 then EXIT_PENDING until first fresh Bid1; slot release on actual fill",
        "SHARES": int(SHARES),
        "POSITION_CAP": int(POSITION_CAP),
        "WAIT_SEC": float(WAIT_SEC),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "STRESS_SEALED": True,
        "HOLDOUT_SEALED": True,
        "QUEUE_ASSUMED_FILL_FORBIDDEN": True,
        "CAUSAL_EX_TOP1": True,
        "POSTHOC_DROP_TOP_FORBIDDEN": True,
        "SIZING": False,
        "ALREADY_EXECUTED": already_executed_check(),
    }


def spec_sha256(spec: dict[str, Any] | None = None) -> str:
    blob = json.dumps(spec or canonical_spec(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()
