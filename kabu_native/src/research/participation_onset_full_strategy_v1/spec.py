"""Pin 3-candidate Participation Onset Full Strategy. No 4th EXIT. No VWAP filter. Stress sealed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.participation_onset_full_strategy_v1 import (
    ABOVE_VWAP_ENTRY_FILTER,
    ANALYSIS_ID,
    CANDIDATE_IDS,
    CANDIDATE_N,
    DEVELOPMENT_DAYS,
    ENTRY_NAMES,
    EXEC_NAMES,
    EXIT_NAMES,
    INDEPENDENT_ALPHA_CLAIM,
    MAX_RESEARCH_DATE,
    POSITION_CAP,
    RPFE_EPISODE_INDEPENDENCE_CLAIM,
    SHARES,
    VOLUME_PERCENTILE_THRESHOLD,
    WAIT_SEC,
)
from research.participation_onset_full_strategy_v1.entries import ENTRY_RULE_TEXT, already_executed_check

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
    return list(CANDIDATE_IDS)


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
        "PRIMARY_METRIC": "FULL_CAUSAL_PORTFOLIO_ECONOMICS",
        "RECOVERY_SEQUENCE_CLOSED": True,
        "ENTRY_N": 1,
        "EXEC_N": 1,
        "EXIT_N": 3,
        "CANDIDATE_N": int(CANDIDATE_N),
        "CANDIDATE_IDS": candidate_ids(),
        "ENTRY_RULE": ENTRY_RULE_TEXT,
        "ENTRY_NAMES": dict(ENTRY_NAMES),
        "EXEC_NAMES": dict(EXEC_NAMES),
        "EXIT_NAMES": dict(EXIT_NAMES),
        "T1_FEATURE": "volume_percentile_60s",
        "VOLUME_PERCENTILE_THRESHOLD": float(VOLUME_PERCENTILE_THRESHOLD),
        "THRESHOLD_SOURCE": "E1_X14_DESIGN_ONLY_Q80",
        "THRESHOLD_RETUNED": False,
        "ABOVE_VWAP_ENTRY_FILTER": bool(ABOVE_VWAP_ENTRY_FILTER),
        "INDEPENDENT_ALPHA_CLAIM": bool(INDEPENDENT_ALPHA_CLAIM),
        "RPFE_EPISODE_INDEPENDENCE_CLAIM": bool(RPFE_EPISODE_INDEPENDENCE_CLAIM),
        "X1": "first fresh valid Ask1 at/after t0; 100-share marketable BUY; fill=observed Ask1",
        "Z3": "2 consecutive completed 1m bars Close<Open AND Close<Close[k-1]; first-fire",
        "ZP": "first post-fill evaluable 10s grid with volume_percentile_60s < frozen T1 threshold; missing does not fire",
        "ZH": "first-fire of Z3 or ZP",
        "EXIT_PENDING": "after trigger, first fresh valid Bid1 SELL 100; slot release on actual fill",
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
