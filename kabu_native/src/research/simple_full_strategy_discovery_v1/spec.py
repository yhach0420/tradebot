"""Pin 75-candidate Full Causal grid. No extra ENTRY/EXEC/EXIT. Stress sealed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.simple_full_strategy_discovery_v1 import (
    ANALYSIS_ID,
    CANDIDATE_N,
    DEVELOPMENT_DAYS,
    ENTRY_IDS,
    ENTRY_NAMES,
    EXEC_IDS,
    EXEC_NAMES,
    EXIT_IDS,
    EXIT_NAMES,
    MAX_RESEARCH_DATE,
    MIN_PF,
    MIN_TOTAL_TRADES,
    MIN_TRADES_PER_DAY,
    MIN_TRADING_DAYS_WITH_FILL,
    POSITION_CAP,
    SHARES,
    WAIT_SEC,
)
from research.simple_full_strategy_discovery_v1.entries import ENTRY_RULE_TEXT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "entries.py",
    "execution.py",
    "exits.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def candidate_ids() -> list[str]:
    out = []
    for e in ENTRY_IDS:
        for x in EXEC_IDS:
            for z in EXIT_IDS:
                out.append(f"{e}_{x}_{z}")
    return out


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
            "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
            "PRIMARY_METRIC": "FULL_CAUSAL_PORTFOLIO_ECONOMICS",
            "ENTRY_N": 5,
            "EXEC_N": 3,
            "EXIT_N": 5,
            "CANDIDATE_N": int(CANDIDATE_N),
            "ENTRY_RULES": dict(ENTRY_RULE_TEXT),
            "ENTRY_NAMES": dict(ENTRY_NAMES),
            "EXEC_NAMES": dict(EXEC_NAMES),
            "EXIT_NAMES": dict(EXIT_NAMES),
            "CANDIDATE_IDS": candidate_ids(),
            "SHARES": int(SHARES),
            "POSITION_CAP": int(POSITION_CAP),
            "SAME_SYMBOL": True,
            "OCCUPANCY": True,
            "SLOT_RELEASE": True,
            "REENTRY": True,
            "WAIT_SEC": float(WAIT_SEC),
            "SESSION": "AM",
            "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "STRESS_SEALED": True,
            "HOLDOUT_SEALED": True,
            "MIN_TOTAL_TRADES": int(MIN_TOTAL_TRADES),
            "MIN_TRADING_DAYS_WITH_FILL": int(MIN_TRADING_DAYS_WITH_FILL),
            "MIN_TRADES_PER_DAY": float(MIN_TRADES_PER_DAY),
            "MIN_PF": float(MIN_PF),
            "SIZING": False,
            "ML": False,
            "EXTRA_CANDIDATE_FORBIDDEN": True,
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
