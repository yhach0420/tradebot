"""Lock the single Breakout Continuation rule. No grid. No holdout in digest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_entry_breakout_continuation_v1 import (
    ANALYSIS_ID,
    BOARD_FRESHNESS_SEC,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FAMILY,
    FORBIDDEN_INPUT_DAYS,
    HIGH_WINDOW,
    HORIZONS_SEC,
    LABEL_DEV,
    LABEL_HOLDOUT,
    LABEL_STRESS,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    MIN_ASK_QTY_SCREEN,
    PARAMETER_SEARCH_PERFORMED,
    PROSPECTIVE_HARVEST_SUSPENDED,
    SESSION,
    STRESS_DAYS,
    TRUE_OOS,
    VOLUME_WINDOW,
)
from research.new_entry_breakout_continuation_v1.rule import EXACT_RULE_TEXT

SOURCE_FILES = (
    "spec.py",
    "rule.py",
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
            "FAMILY": FAMILY,
            "SESSION": SESSION,
            "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
            "HIGH_WINDOW": int(HIGH_WINDOW),
            "VOLUME_WINDOW": int(VOLUME_WINDOW),
            "VOLUME_MULTIPLIER": None,
            "VWAP_DISTANCE_THRESHOLD": None,
            "FIRST_CROSS": True,
            "BOARD_AS_ALPHA": False,
            "PARAMETER_SEARCH_PERFORMED": PARAMETER_SEARCH_PERFORMED,
            "HORIZONS_SEC": list(HORIZONS_SEC),
            "BOARD_FRESHNESS_SEC": float(BOARD_FRESHNESS_SEC),
            "FRESHNESS_CLOCK": "BidTime/AskTime then canonical ingress. Never CurrentPriceTime.",
            "ENTRY_REFERENCE": "fresh executable Ask1 at SIGNAL_T0",
            "MARKOUT": "(Bid_h / Ask_t0 - 1) * 10000",
            "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
            "LOCKED_HOLDOUT_DAYS": list(LOCKED_HOLDOUT_DAYS),
            "STRESS_DAYS": list(STRESS_DAYS),
            "LABEL_DEV": LABEL_DEV,
            "LABEL_HOLDOUT": LABEL_HOLDOUT,
            "LABEL_STRESS": LABEL_STRESS,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "FORBIDDEN_INPUT_DAYS": list(FORBIDDEN_INPUT_DAYS),
            "TRUE_OOS": TRUE_OOS,
            "CERTIFIED": CERTIFIED,
            "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
            "MIN_ASK_QTY_SCREEN": float(MIN_ASK_QTY_SCREEN),
            "EXIT_DESIGN": False,
            "SIZING": False,
            "ML": False,
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
