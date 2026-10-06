"""Implementation identity for frozen V3. Does not mutate frozen_strategy()."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research.new_full_strategy_architecture_redesign_v1.spec import dumps_sha256, frozen_strategy
from research.new_full_strategy_implementation_and_dev_eval_v1 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    ARCHITECTURE_CHANGED,
    BASE_ECONOMIC_RUN_N,
    ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS,
    G6_CAUSAL_RERUN_N_MAX,
    IMPLEMENTATION_FIX_ONLY,
    OLD_ST_RCA_CONTINUED,
    PARENT_ANALYSIS_ID,
    PINNED_V3_SHA256,
    POST_RESULT_ARCHITECTURE_CHANGE,
    V3_HASH_CHANGED,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "quotes.py",
    "engine.py",
    "integrity.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def v3_sha256() -> str:
    return dumps_sha256(frozen_strategy())


def v3_hash_unchanged() -> bool:
    return v3_sha256() == PINNED_V3_SHA256


def implementation_flags() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ANALYSIS_ID": PARENT_ANALYSIS_ID,
        "FULL_STRATEGY_SPEC_SHA256_V3": v3_sha256(),
        "PINNED_V3_SHA256": PINNED_V3_SHA256,
        "V3_HASH_UNCHANGED": v3_hash_unchanged(),
        "V3_HASH_CHANGED": V3_HASH_CHANGED,
        "ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS": ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS,
        "POST_RESULT_ARCHITECTURE_CHANGE": POST_RESULT_ARCHITECTURE_CHANGE,
        "ARCHITECTURE_CHANGED": ARCHITECTURE_CHANGED,
        "IMPLEMENTATION_FIX_ONLY": IMPLEMENTATION_FIX_ONLY,
        "BASE_ECONOMIC_RUN_N": BASE_ECONOMIC_RUN_N,
        "G6_CAUSAL_RERUN_N_MAX": G6_CAUSAL_RERUN_N_MAX,
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "ANOTHER_PRECOMMIT_RUN": ANOTHER_PRECOMMIT_RUN,
    }


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()
