"""Generic completed-1m suffix-domain EXIT fire-index audit. No fills. No PnL."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import (
    EXIT_IDS,
    EXIT_SOURCE_KEYS,
    MISMATCH_EXAMPLE_CAP,
    PAIRWISE_EXIT_PAIRS,
)
from research.simple_full_strategy_discovery_v1 import exits as exits_mod
from research.simple_full_strategy_discovery_v1.exits import technical_fire_i


def _fill_candidates(finalize: np.ndarray) -> list[float]:
    n = int(finalize.size)
    if n == 0:
        return [0.0]
    out = [float(finalize[0]) - 1.0]
    seen: set[float] = set()
    for x in finalize:
        fx = float(x)
        if fx not in seen:
            seen.add(fx)
            out.append(fx)
    return out


def fire_on_suffix(
    key: str,
    *,
    idxs: list[int],
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    vwap: np.ndarray,
) -> int | None:
    return technical_fire_i(
        key,
        idxs=idxs,
        open_=open_,
        high=high,
        low=low,
        close=close,
        vwap=vwap,
    )


def audit_suffix_domain(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [EXIT_SOURCE_KEYS[z] for z in EXIT_IDS]
    pair_stats: dict[tuple[str, str], dict[str, Any]] = {}
    for a, b in PAIRWISE_EXIT_PAIRS:
        pair_stats[(a, b)] = {
            "EXIT_A": a,
            "EXIT_B": b,
            "SUFFIX_CASE_N": 0,
            "FIRE_INDEX_MATCH_N": 0,
            "FIRE_INDEX_MISMATCH_N": 0,
            "NONE_MATCH_N": 0,
            "BOTH_FIRE_SAME_I_N": 0,
            "examples": [],
            "cause_n": {},
        }
    suffix_n = 0
    array_n = 0
    for rec in rows:
        array_n += 1
        open_ = rec["open"]
        high = rec["high"]
        low = rec["low"]
        close = rec["close"]
        vwap = rec["vwap"]
        finalize = rec["finalize_t"]
        day = rec["day"]
        symbol = rec["symbol"]
        n = int(close.size)
        for fill_t in _fill_candidates(finalize):
            idxs = exits_mod._post_fill_bars(finalize, float(fill_t))
            suffix_start = int(idxs[0]) if idxs else n
            fires: dict[str, int | None] = {}
            for z, key in zip(EXIT_IDS, keys):
                fires[z] = fire_on_suffix(
                    key,
                    idxs=idxs,
                    open_=open_,
                    high=high,
                    low=low,
                    close=close,
                    vwap=vwap,
                )
            suffix_n += 1
            for a, b in PAIRWISE_EXIT_PAIRS:
                st = pair_stats[(a, b)]
                st["SUFFIX_CASE_N"] += 1
                fa = fires[a]
                fb = fires[b]
                if fa is None and fb is None:
                    st["NONE_MATCH_N"] += 1
                    st["FIRE_INDEX_MATCH_N"] += 1
                    continue
                if fa is not None and fb is not None and int(fa) == int(fb):
                    st["BOTH_FIRE_SAME_I_N"] += 1
                    st["FIRE_INDEX_MATCH_N"] += 1
                    continue
                st["FIRE_INDEX_MISMATCH_N"] += 1
                if fa is None:
                    cause = f"{a}_NONE_{b}_FIRE"
                elif fb is None:
                    cause = f"{a}_FIRE_{b}_NONE"
                else:
                    cause = "DIFFERENT_FIRE_INDEX"
                st["cause_n"][cause] = int(st["cause_n"].get(cause) or 0) + 1
                if len(st["examples"]) < int(MISMATCH_EXAMPLE_CAP):
                    st["examples"].append(
                        {
                            "cause": cause,
                            "symbol": symbol,
                            "day": day,
                            "suffix_start": suffix_start,
                            "exit_a_fire_i": fa,
                            "exit_b_fire_i": fb,
                            "bar_n": n,
                        }
                    )
    pairwise = []
    examples = []
    complete = True
    for a, b in PAIRWISE_EXIT_PAIRS:
        st = pair_stats[(a, b)]
        if int(st["SUFFIX_CASE_N"]) != int(suffix_n):
            complete = False
        pairwise.append(
            {
                "EXIT_A": a,
                "EXIT_B": b,
                "SUFFIX_CASE_N": int(st["SUFFIX_CASE_N"]),
                "FIRE_INDEX_MATCH_N": int(st["FIRE_INDEX_MATCH_N"]),
                "FIRE_INDEX_MISMATCH_N": int(st["FIRE_INDEX_MISMATCH_N"]),
                "NONE_MATCH_N": int(st["NONE_MATCH_N"]),
                "BOTH_FIRE_SAME_I_N": int(st["BOTH_FIRE_SAME_I_N"]),
                "CAUSE_N": dict(st["cause_n"]),
                "EXAMPLE_N": len(st["examples"]),
                "EXAMPLES_TRUNCATED": int(st["FIRE_INDEX_MISMATCH_N"]) > len(st["examples"]),
            }
        )
        for ex in st["examples"]:
            examples.append({"EXIT_A": a, "EXIT_B": b, **ex})
    return {
        "ok": complete and suffix_n > 0 and array_n > 0 and len(pairwise) == 10,
        "SUFFIX_CASE_N": int(suffix_n),
        "ARRAY_N": int(array_n),
        "PAIR_COMPARISON_N": len(pairwise),
        "ALL_10_PAIRS_COMPLETED": len(pairwise) == 10 and complete,
        "pairwise": pairwise,
        "mismatch_examples": examples,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
    }
