"""Pin corrected DROP_TOP = CAUSAL_EX_TOP1. No retune. Stress sealed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.e4_x2_z3_causal_concentration_recheck_v1 import (
    ANALYSIS_ID,
    FOCUS_CANDIDATE,
    PRIOR_ANALYSIS_ID,
)
from research.simple_full_strategy_discovery_v1 import (
    CANDIDATE_N,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    MIN_PF,
    POSITION_CAP,
    SHARES,
    WAIT_SEC,
)
from research.simple_full_strategy_discovery_v1.spec import candidate_ids, spec_sha256 as prior_spec_sha256

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PRIOR_ANALYSIS_ID": PRIOR_ANALYSIS_ID,
        "FOCUS_CANDIDATE": FOCUS_CANDIDATE,
        "PRIOR_SPEC_SHA256": prior_spec_sha256(),
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
        "PRIMARY_METRIC": "FULL_CAUSAL_PORTFOLIO_ECONOMICS",
        "DROP_TOP_CORRECTION": "CAUSAL_EX_TOP1_PNL replaces POSTHOC DROP_TOP_SYMBOL_PNL",
        "THRESHOLD_RETUNE": False,
        "CANDIDATE_N": int(CANDIDATE_N),
        "CANDIDATE_IDS": candidate_ids(),
        "SHARES": int(SHARES),
        "POSITION_CAP": int(POSITION_CAP),
        "WAIT_SEC": float(WAIT_SEC),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "STRESS_SEALED": True,
        "HOLDOUT_SEALED": True,
        "MIN_PF": float(MIN_PF),
        "SIZING": False,
        "LODO_FOLD_LOCAL_TOP_SYMBOL": True,
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
