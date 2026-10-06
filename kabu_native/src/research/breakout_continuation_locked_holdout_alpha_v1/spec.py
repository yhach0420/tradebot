"""Pin frozen Breakout rule + PRIMARY selection + holdout gates. No holdout numbers in digest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.breakout_continuation_locked_holdout_alpha_v1 import (
    ANALYSIS_ID,
    BOARD_FRESHNESS_SEC,
    DEVELOPMENT_DAYS,
    HIGH_WINDOW,
    HORIZONS_SEC,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    MIN_DAILY_EVALUABLE_N,
    MIN_DAYS_WITH_DAILY_N,
    MIN_HOLDOUT_TOTAL_N,
    PRIMARY_FAMILY,
    VOLUME_WINDOW,
    VWAP_HOLDOUT_ALLOWED,
)
from research.new_entry_breakout_continuation_v1.rule import EXACT_RULE_TEXT

SOURCE_FILES = (
    "__init__.py",
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
            "PRIMARY_FAMILY": PRIMARY_FAMILY,
            "PRIMARY_SELECTION_FROZEN_BEFORE_HOLDOUT": True,
            "VWAP_HOLDOUT_ALLOWED": VWAP_HOLDOUT_ALLOWED,
            "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
            "HIGH_WINDOW": int(HIGH_WINDOW),
            "VOLUME_WINDOW": int(VOLUME_WINDOW),
            "VOLUME_MULTIPLIER": None,
            "VWAP_DISTANCE_THRESHOLD": None,
            "FIRST_CROSS": "P1 true now and P1 at i-1 false",
            "PRIMARY_METRIC": "MID_MARKOUT",
            "ASK_BID_IS_GATE": False,
            "HORIZONS_SEC": list(HORIZONS_SEC),
            "BOARD_FRESHNESS_SEC": float(BOARD_FRESHNESS_SEC),
            "FRESHNESS_CLOCK": "BidTime/AskTime then canonical ingress. Never CurrentPriceTime.",
            "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
            "LOCKED_HOLDOUT_DAYS": list(LOCKED_HOLDOUT_DAYS),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "STRESS_SEALED": True,
            "MIN_HOLDOUT_TOTAL_N": int(MIN_HOLDOUT_TOTAL_N),
            "MIN_DAILY_EVALUABLE_N": int(MIN_DAILY_EVALUABLE_N),
            "MIN_DAYS_WITH_DAILY_N": int(MIN_DAYS_WITH_DAILY_N),
            "TRIM_P": 0.05,
            "WINSOR_P": 0.05,
            "PARAMETER_CHANGE_N": 0,
            "EXECUTION_DESIGN": False,
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
    rule = Path(__file__).resolve().parents[1] / "new_entry_breakout_continuation_v1" / "rule.py"
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    h.update(b"breakout_rule.py\0")
    h.update(rule.read_bytes() if rule.is_file() else b"MISSING")
    return h.hexdigest()


def rule_sha256() -> str:
    return hashlib.sha256(EXACT_RULE_TEXT.encode("utf-8")).hexdigest()
