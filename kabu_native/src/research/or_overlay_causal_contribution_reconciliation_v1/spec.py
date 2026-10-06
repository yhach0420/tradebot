"""Pinned recon contract. Exact Production overlay. No standalone. No Stress/future."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.or_overlay_causal_contribution_reconciliation_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_E1,
    CASE_E2,
    NEXT_IF_E2,
    OR_ERA_DAYS,
    W27_OR_ERA_SESSIONS,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "audit.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "TREATMENT": "PBv2 cap4 + OR cap1 Production overlay",
        "CONTROL_A": "PBv2 cap4 + OR disabled; 5th slot unused",
        "CONTROL_B": "PBv2 cap5 + OR disabled",
        "STANDALONE_OPEN_STRENGTH": False,
        "NEW_ENTRY": False,
        "NEW_EXIT": False,
        "CAP_CHANGE_ON_TREATMENT": False,
        "NEW_REPLAY_FROM_CAPTURE": False,
        "STRESS_OPEN": False,
        "FUTURE_DATA": False,
        "SIZING": False,
        "POSTHOC_TOP_SYMBOL_SUBTRACTION": False,
        "OR_ERA_DAYS": list(OR_ERA_DAYS),
        "W27_OR_ERA_SESSIONS": [list(x) for x in W27_OR_ERA_SESSIONS],
        "CASE_A": CASE_A,
        "CASE_E1": CASE_E1,
        "CASE_E2": CASE_E2,
        "NEXT_IF_E2": NEXT_IF_E2,
        "DAY_HIGH_NEAR_PCT_CURRENT_SOURCE": 0.25,
        "CARRY_BACK_CURRENT_CONFIG": False,
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
