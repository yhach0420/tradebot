"""Pin FDR rule + DEV coverage/alpha gates. No holdout/stress numbers. No execution alternatives."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_entry_breakout_continuation_v1.rule import EXACT_RULE_TEXT as BREAKOUT_RULE
from research.new_entry_failed_breakdown_reclaim_v1 import (
    ANALYSIS_ID,
    BOARD_FRESHNESS_SEC,
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FAMILY,
    FIRST_CROSS_ADDED,
    HORIZONS_SEC,
    LOW_WINDOW,
    MAX_RESEARCH_DATE,
    MIN_DAILY_EVALUABLE_N,
    MIN_DAYS_WITH_DAILY_N,
    MIN_DEV_TOTAL_N,
)
from research.new_entry_failed_breakdown_reclaim_v1.rule import EXACT_RULE_TEXT
from research.new_entry_vwap_rejection_reclaim_v1.rule import EXACT_RULE_TEXT as VWAP_RULE

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "rule.py",
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


def duplicate_architecture() -> dict[str, Any]:
    vs_breakout = EXACT_RULE_TEXT != BREAKOUT_RULE
    vs_vwap = EXACT_RULE_TEXT != VWAP_RULE
    return {
        "DUPLICATE_VS_BREAKOUT": not vs_breakout,
        "DUPLICATE_VS_VWAP_RECLAIM": not vs_vwap,
        "DUPLICATE_ARCHITECTURE": (not vs_breakout) or (not vs_vwap),
        "why": (
            "FDR is prior-5-low breakdown on bar i-1 then close reclaim of High[i-1] on bar i. "
            "Not Close>max(prior5 High)+volume+VWAP. Not same-bar Low<VWAP Close>VWAP Close>Open."
        ),
    }


def canonical_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY": FAMILY,
            "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
            "LOW_WINDOW": int(LOW_WINDOW),
            "FIRST_CROSS_ADDED": bool(FIRST_CROSS_ADDED),
            "PRIMARY_METRIC": "MID_MARKOUT",
            "ASK_BID_IS_GATE": False,
            "HORIZONS_SEC": list(HORIZONS_SEC),
            "BOARD_FRESHNESS_SEC": float(BOARD_FRESHNESS_SEC),
            "FRESHNESS_CLOCK": "BidTime/AskTime then canonical ingress. Never CurrentPriceTime.",
            "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
            "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "STRESS_SEALED": True,
            "HOLDOUT_SEALED": True,
            "MIN_DEV_TOTAL_N": int(MIN_DEV_TOTAL_N),
            "MIN_DAILY_EVALUABLE_N": int(MIN_DAILY_EVALUABLE_N),
            "MIN_DAYS_WITH_DAILY_N": int(MIN_DAYS_WITH_DAILY_N),
            "TRIM_P": 0.05,
            "WINSOR_P": 0.05,
            "CLUSTER_IS_HARD_GATE": False,
            "PARAMETER_CHANGE_N": 0,
            "EXECUTION_DESIGN": False,
            "EXIT_DESIGN": False,
            "SIZING": False,
            "ML": False,
            "G1_G14": [
                "G1_MID180_GT_0",
                "G2_MID300_GT_0",
                "G3_POS_DAY_180_GE_NEG",
                "G4_POS_DAY_300_GE_NEG",
                "G5_EX_BEST_180_GE_0",
                "G6_EX_BEST_300_GE_0",
                "G7_DROP_TOP_180_GE_0",
                "G8_DROP_TOP_300_GE_0",
                "G9_LIFT180_GT_0",
                "G10_LIFT300_GT_0",
                "G11_TRIM5_180_GE_0",
                "G12_TRIM5_300_GE_0",
                "G13_WINSOR5_180_GE_0",
                "G14_WINSOR5_300_GE_0",
            ],
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


def rule_sha256() -> str:
    return hashlib.sha256(EXACT_RULE_TEXT.encode("utf-8")).hexdigest()


def signal_set_sha256(rows: list[dict[str, Any]]) -> str:
    keys = sorted(
        (
            str(r.get("date") or ""),
            str(r.get("symbol") or ""),
            int(r.get("i") or 0),
            round(float(r.get("t0") or 0.0), 6),
        )
        for r in rows
    )
    blob = json.dumps(keys, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
