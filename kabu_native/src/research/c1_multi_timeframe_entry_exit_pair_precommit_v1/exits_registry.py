"""Load the five pre-existing Simple-Full EXIT mechanisms from source. Do not reconstruct."""
from __future__ import annotations

import inspect
from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1 import (
    EXIT_IDS,
    EXIT_RESOLVE_FUNCTION,
    EXIT_SOURCE_FUNCTION,
    EXIT_SOURCE_KEYS,
    EXIT_SOURCE_MODULE,
    EXIT_SOURCE_PATH,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.isolation import EXIT_SOURCE_FILE
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import dumps_sha256, file_sha256
from research.simple_full_strategy_discovery_v1 import BOARD_FRESHNESS_SEC, EXIT_NAMES, MIN_QTY, SHARES
from research.simple_full_strategy_discovery_v1 import exits as exits_mod
from research.simple_full_strategy_discovery_v1.exits import (
    first_causal_bid,
    last_session_bid,
    resolve_exit,
    technical_fire_i,
)


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
    branch = rest[:end].strip("\n")
    if marker not in branch:
        raise RuntimeError(f"EXIT_BRANCH_UNPROVEN:{key}")
    return branch


def _constants() -> dict[str, Any]:
    return {
        "BOARD_FRESHNESS_SEC": float(BOARD_FRESHNESS_SEC),
        "MIN_QTY": float(MIN_QTY),
        "SHARES": int(SHARES),
        "THRESHOLD_PARAMETERS": None,
        "NOTE": (
            "Z1-Z5 branches in technical_fire_i have no tunable numeric thresholds. "
            "Z3 consecutive-weakness uses a boolean prev_weak latch, not a retunable N."
        ),
    }


def prove_exit_library() -> list[dict[str, Any]]:
    if not EXIT_SOURCE_FILE.is_file():
        raise RuntimeError("EXIT_SOURCE_MISSING")
    fire_src = inspect.getsource(technical_fire_i)
    resolve_src = inspect.getsource(resolve_exit)
    bid_src = inspect.getsource(first_causal_bid)
    sess_src = inspect.getsource(last_session_bid)
    post_src = inspect.getsource(exits_mod._post_fill_bars)
    file_hash = file_sha256(EXIT_SOURCE_FILE)
    fire_hash = dumps_sha256(fire_src)
    resolve_hash = dumps_sha256(resolve_src)
    bid_hash = dumps_sha256(bid_src)
    sess_hash = dumps_sha256(sess_src)
    post_hash = dumps_sha256(post_src)
    if technical_fire_i.__module__ != EXIT_SOURCE_MODULE:
        raise RuntimeError("EXIT_MODULE_MISMATCH")
    if technical_fire_i.__name__ != EXIT_SOURCE_FUNCTION:
        raise RuntimeError("EXIT_FUNCTION_MISMATCH")
    if resolve_exit.__name__ != EXIT_RESOLVE_FUNCTION:
        raise RuntimeError("EXIT_RESOLVE_MISMATCH")
    if tuple(EXIT_NAMES[k] for k in ("Z1", "Z2", "Z3", "Z4", "Z5")) != EXIT_IDS:
        raise RuntimeError("EXIT_NAME_MAP_MISMATCH")
    constants = _constants()
    rows = []
    for exit_id in EXIT_IDS:
        key = EXIT_SOURCE_KEYS[exit_id]
        branch = _slice_branch(fire_src, key)
        rows.append(
            {
                "EXIT_ID": exit_id,
                "SOURCE_PATH": EXIT_SOURCE_PATH,
                "SOURCE_MODULE": EXIT_SOURCE_MODULE,
                "SOURCE_FUNCTION": EXIT_SOURCE_FUNCTION,
                "SOURCE_KEY": key,
                "SOURCE_RESOLVE_FUNCTION": EXIT_RESOLVE_FUNCTION,
                "SOURCE_BID_FUNCTION": "first_causal_bid",
                "SOURCE_SESSION_FUNCTION": "last_session_bid",
                "SOURCE_POST_FILL_FUNCTION": "_post_fill_bars",
                "SOURCE_FILE_SHA256": file_hash,
                "SOURCE_FUNCTION_SHA256": fire_hash,
                "SOURCE_RESOLVE_SHA256": resolve_hash,
                "SOURCE_BID_SHA256": bid_hash,
                "SOURCE_SESSION_SHA256": sess_hash,
                "SOURCE_POST_FILL_SHA256": post_hash,
                "SOURCE_BRANCH_SHA256": dumps_sha256(branch),
                "SOURCE_SHA256": dumps_sha256(
                    {
                        "EXIT_ID": exit_id,
                        "SOURCE_KEY": key,
                        "FILE": file_hash,
                        "FUNCTION": fire_hash,
                        "BRANCH": dumps_sha256(branch),
                        "RESOLVE": resolve_hash,
                    }
                ),
                "SOURCE_BRANCH": branch,
                "CONSTANTS": constants,
                "TRIGGER_TIMEFRAME": (
                    "completed 1m bars: _post_fill_bars(raw['finalize_t'], fill_t) "
                    "returns indices with finalize_t > fill_t"
                ),
                "FIRST_FIRE_SEMANTICS": (
                    "technical_fire_i walks post-fill completed 1m indices in time order "
                    "and returns the first matching bar index, or None"
                ),
                "BID_EXECUTION_SEMANTICS": (
                    "On fire, event_t = raw['finalize_t'][fire_i]; first_causal_bid searches "
                    "the first fresh valid Bid1 at/after event_t, fill = observed Bid1, 100-share SELL"
                ),
                "SESSION_CLOSE_SEMANTICS": (
                    "resolve_exit: if technical Bid miss/absent, or last_session_bid is earlier "
                    "than technical exit_t, operational SESSION_CLOSE via last_session_bid"
                ),
                "PARAMETER_CHANGED": False,
                "IMPLEMENTATION_CHANGED": False,
                "NEW_EXIT": False,
                "SEARCHED_THIS_RUN": False,
            }
        )
    if len(rows) != 5:
        raise RuntimeError("EXIT_N")
    if [r["EXIT_ID"] for r in rows] != list(EXIT_IDS):
        raise RuntimeError("EXIT_ID_ORDER")
    if any(r["NEW_EXIT"] or r["PARAMETER_CHANGED"] or r["IMPLEMENTATION_CHANGED"] for r in rows):
        raise RuntimeError("EXIT_MUTATION")
    return rows
