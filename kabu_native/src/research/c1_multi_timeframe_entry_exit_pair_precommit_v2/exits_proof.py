"""Load exact Z1-Z5 source predicates. Prove Z4 peak is a finite-high guard only."""
from __future__ import annotations

import inspect
from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.exits_registry import prove_exit_library
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import (
    EXPECTED_EXIT_FILE_SHA256,
    EXIT_IDS,
    EXIT_SOURCE_KEYS,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.isolation import EXIT_SOURCE_FILE
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.spec import dumps_sha256, file_sha256
from research.simple_full_strategy_discovery_v1.exits import technical_fire_i


def _slice_branch(src: str, key: str) -> str:
    marker = f'if exit_id == "{key}":'
    start = src.find(marker)
    if start < 0:
        raise RuntimeError(f"EXIT_BRANCH_MISSING:{key}")
    rest = src[start:]
    nxt = None
    for other in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        if other == key:
            continue
        needle = f'\n    if exit_id == "{other}":'
        pos = rest.find(needle)
        if pos > 0 and (nxt is None or pos < nxt):
            nxt = pos
    tail = rest.find("\n    return None")
    end = len(rest)
    if nxt is not None:
        end = min(end, nxt)
    if tail > 0:
        end = min(end, tail)
    return rest[:end].strip("\n")


def prove_exit_predicates() -> dict[str, Any]:
    file_hash = file_sha256(EXIT_SOURCE_FILE) if EXIT_SOURCE_FILE.is_file() else ""
    fire_src = inspect.getsource(technical_fire_i)
    z2 = _slice_branch(fire_src, "Z2")
    z4 = _slice_branch(fire_src, "Z4")
    z5 = _slice_branch(fire_src, "Z5")
    z2_pred = "c < lo" in z2 and "low, i - 1" in z2
    z4_fire_pred = "c < lo" in z4 and "low, i - 1" in z4
    peak_in_threshold = (
        ("c < peak" in z4)
        or ("c > peak" in z4)
        or ("lo < peak" in z4)
        or ("peak <" in z4)
        or ("peak >" in z4 and "peak is None" not in z4)
    )
    # peak is assigned and used only as None-guard / max accumulator
    peak_guard = "if peak is None:" in z4 and "peak =" in z4
    peak_value_used = peak_in_threshold
    z5_is_min = 'technical_fire_i("Z1"' in z5 and 'technical_fire_i("Z2"' in z5 and "min(cands)" in z5
    rows = prove_exit_library()
    hash_ok = file_hash == EXPECTED_EXIT_FILE_SHA256
    return {
        "ok": bool(hash_ok and z2_pred and z4_fire_pred and peak_guard and z5_is_min and len(rows) == 5),
        "SOURCE_PATH": "src/research/simple_full_strategy_discovery_v1/exits.py",
        "SOURCE_FILE_SHA256": file_hash,
        "EXPECTED_EXIT_FILE_SHA256": EXPECTED_EXIT_FILE_SHA256,
        "HASH_MATCH": hash_ok,
        "SOURCE_FUNCTION": technical_fire_i.__name__,
        "SOURCE_MODULE": technical_fire_i.__module__,
        "EXIT_IDS": list(EXIT_IDS),
        "EXIT_SOURCE_KEYS": dict(EXIT_SOURCE_KEYS),
        "Z2_BRANCH": z2,
        "Z4_BRANCH": z4,
        "Z5_BRANCH": z5,
        "Z2_PREDICATE": "finite(close[i]) and finite(low[i-1]) and close[i] < low[i-1]",
        "Z4_FIRE_PREDICATE_AFTER_GUARD": "finite(close[i]) and finite(low[i-1]) and close[i] < low[i-1]",
        "Z4_PEAK_VALUE_USED_IN_EXIT_THRESHOLD": bool(peak_value_used),
        "Z4_PEAK_ONLY_ACTS_AS_FINITE_HIGH_SEEN_GUARD": bool(peak_guard and not peak_value_used),
        "Z5_IS_EARLIEST_OF_Z1_AND_Z2": z5_is_min,
        "Z2_Z4_FIRE_PREDICATE_TEXT_MATCH": z2_pred and z4_fire_pred,
        "LIBRARY_ROWS": [
            {
                "EXIT_ID": r["EXIT_ID"],
                "SOURCE_KEY": r["SOURCE_KEY"],
                "SOURCE_SHA256": r["SOURCE_SHA256"],
                "SOURCE_FUNCTION": r["SOURCE_FUNCTION"],
                "SOURCE_PATH": r["SOURCE_PATH"],
            }
            for r in rows
        ],
        "FIRE_SRC_SHA256": dumps_sha256(fire_src),
    }


def z2_z4_source_domain_equivalence(bar_contract: dict[str, Any], predicates: dict[str, Any]) -> dict[str, Any]:
    finite_high = bool(bar_contract.get("VALID_COMPLETED_BAR_REQUIRES_FINITE_HIGH"))
    peak_unused = predicates.get("Z4_PEAK_VALUE_USED_IN_EXIT_THRESHOLD") is False
    peak_guard = predicates.get("Z4_PEAK_ONLY_ACTS_AS_FINITE_HIGH_SEEN_GUARD") is True
    same_pred = bool(predicates.get("Z2_Z4_FIRE_PREDICATE_TEXT_MATCH"))
    proven = bool(
        bar_contract.get("ok")
        and predicates.get("ok")
        and finite_high
        and peak_unused
        and peak_guard
        and same_pred
    )
    why = (
        "On a non-empty suffix of canonical completed bars, the first index has finite High "
        "because _finish refuses non-finite High. Z4 therefore sets peak on that first bar "
        "before the fire check. peak is never compared to Close/Low; it only skips while no "
        "finite High has been seen. After the guard, Z4's fire predicate is the same as Z2: "
        "finite(close[i]) and finite(low[i-1]) and close[i] < low[i-1], including low[i-1] "
        "from before the suffix. Empty suffix: both return None."
    )
    return {
        "proven": proven,
        "VALID_COMPLETED_BAR_REQUIRES_FINITE_HIGH": finite_high,
        "Z4_PEAK_VALUE_USED_IN_EXIT_THRESHOLD": bool(predicates.get("Z4_PEAK_VALUE_USED_IN_EXIT_THRESHOLD")),
        "Z4_PEAK_ONLY_ACTS_AS_FINITE_HIGH_SEEN_GUARD": peak_guard,
        "Z2_Z4_FIRE_PREDICATE_TEXT_MATCH": same_pred,
        "GUARD_BEHAVIORALLY_REDUNDANT_ON_CANONICAL_DOMAIN": proven,
        "WHY": why if proven else "Source invariant incomplete; do not drop Z4 from DEV coincidence.",
    }
