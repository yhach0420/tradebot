"""Pinned review contract. No new ENTRY/EXIT/replay/Stress/future."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.existing_architecture_exhaustion_review_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    NEXT_OR_RECONCILE,
    NEXT_STATE_SPACE,
    STATUS_ENUM,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "inventory.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "NEW_ENTRY": False,
        "NEW_EXIT": False,
        "NEW_THRESHOLD": False,
        "STANDALONE_OPEN_STRENGTH": False,
        "NEW_REPLAY": False,
        "NEW_PNL_SIMULATION": False,
        "NEW_CANDIDATE": False,
        "STRESS_OPEN": False,
        "SIZING": False,
        "FUTURE_DATA": False,
        "PNL_USED_FOR_NEXT": False,
        "OPEN_STRENGTH_STANDALONE_CLASSIFICATION": "NEW_DERIVATIVE_NOT_PREEXISTING",
        "STATUS_ENUM": list(STATUS_ENUM),
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_C": CASE_C,
        "NEXT_IF_OR_SLEEVE_PARTIAL": NEXT_OR_RECONCILE,
        "NEXT_IF_EXHAUSTED": NEXT_STATE_SPACE,
        "STRESS_DAYS_SEALED": ["20260828", "20260831", "20260901", "20260902"],
        "MAX_NEW_DATA_DATE": "NONE",
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
