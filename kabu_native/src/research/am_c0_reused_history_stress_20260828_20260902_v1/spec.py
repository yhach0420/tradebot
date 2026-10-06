"""Lock C0+C14 reused-history stress before economics. No refit. No search."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_c0_reused_history_stress_20260828_20260902_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    EXPECTED_C0_SPEC_SHA256,
    FORBIDDEN_INPUT_DAYS,
    LABEL,
    MAX_RESEARCH_DATE,
    MIN_AUGMENT_TRADE_N,
    PROSPECTIVE_HARVEST_SUSPENDED,
    STRESS_DAYS,
    TRUE_OOS,
)
from research.am_entry_architecture_final_reassessment import AUGMENT_MAX_PER_COHORT, CURRENT_PRIORITY, C0
from research.am_entry_information_expansion import RF_CLF_PARAMS, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import C14_ID, DEV_WAIT_SEC, FINAL_SELECTION_N, SESSION
from research.am_entry_research_final_decision import PROSPECTIVE_CHALLENGER_NAME, PROSPECTIVE_STATUS
from research.am_entry_research_final_decision.spec import canonical_c0_spec, spec_sha256 as c0_spec_sha256
from research.am_exit_research_final_decision import BEST_TESTED_EXIT_FOR_C0
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

SOURCE_FILES = (
    "spec.py",
    "isolation.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if obj is None:
        return None
    return str(obj)


def canonical_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "LABEL": LABEL,
            "STRESS_DAYS": list(STRESS_DAYS),
            "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "FORBIDDEN_INPUT_DAYS": list(FORBIDDEN_INPUT_DAYS),
            "TRUE_OOS": TRUE_OOS,
            "CERTIFIED": CERTIFIED,
            "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
            "SESSION": SESSION,
            "ENTRY": C0,
            "ENTRY_NAME": PROSPECTIVE_CHALLENGER_NAME,
            "ENTRY_STATUS": PROSPECTIVE_STATUS,
            "EXPECTED_C0_SPEC_SHA256": EXPECTED_C0_SPEC_SHA256,
            "EXIT": BEST_TESTED_EXIT_FOR_C0,
            "C14_ID": C14_ID,
            "FROZEN_MODEL": "EXPANDED_X14_RF",
            "TARGET": TARGET,
            "RF_PARAMS": dict(RF_CLF_PARAMS),
            "X14_BUNDLE": list(X14_BUNDLE),
            "B0_PRIMARY": "B0_TOP1",
            "B1_CONFIRM": "B1_ELIGIBLE",
            "B1_RANK_REQUIRED": False,
            "FALLBACK": False,
            "SCORE_BLEND": False,
            "CURRENT_PRIORITY": bool(CURRENT_PRIORITY),
            "CURRENT_TOPK": int(FINAL_SELECTION_N),
            "AUGMENT_MAX_PER_COHORT": int(AUGMENT_MAX_PER_COHORT),
            "POSITION_CAP": int(POSITION_CAP),
            "SAME_SYMBOL": True,
            "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
            "RUNTIME_WAIT_SEC": float(WAIT_SEC),
            "MIN_AUGMENT_TRADE_N": int(MIN_AUGMENT_TRADE_N),
            "NO_REFIT_ON_STRESS": True,
            "NO_THRESHOLD_SEARCH": True,
            "PM_ARCHITECTURE": False,
        }
    )


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


def live_c0_spec_sha256() -> str:
    return c0_spec_sha256(canonical_c0_spec())
