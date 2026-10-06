"""Relative displayed-depth distances and favorable migration. No qty alpha. No span."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.full_causal_strategy_architecture_from_information_object_v1.geometry import (
    level_px_qty,
    valid_full_depth,
)

K_DEEP = tuple(range(2, 11))


def relative_bid(bid_px: list[float]) -> Optional[np.ndarray]:
    b1 = float(bid_px[0])
    out = np.empty(9, dtype=float)
    for i, k in enumerate(K_DEEP):
        d = b1 - float(bid_px[k - 1])
        if not (d == d) or d <= 0.0:
            return None
        out[i] = d
    return out


def relative_ask(ask_px: list[float]) -> Optional[np.ndarray]:
    a1 = float(ask_px[0])
    out = np.empty(9, dtype=float)
    for i, k in enumerate(K_DEEP):
        d = float(ask_px[k - 1]) - a1
        if not (d == d) or d <= 0.0:
            return None
        out[i] = d
    return out


def bid_geometry_contracts(prev: np.ndarray, cur: np.ndarray) -> bool:
    le = np.all(cur <= prev)
    lt = np.any(cur < prev)
    return bool(le and lt)


def ask_geometry_expands(prev: np.ndarray, cur: np.ndarray) -> bool:
    ge = np.all(cur >= prev)
    gt = np.any(cur > prev)
    return bool(ge and gt)


def favorable_migration(prev_bid: np.ndarray, cur_bid: np.ndarray, prev_ask: np.ndarray, cur_ask: np.ndarray) -> bool:
    return bid_geometry_contracts(prev_bid, cur_bid) and ask_geometry_expands(prev_ask, cur_ask)


def thesis_valid(bid_rel: np.ndarray, ask_rel: np.ndarray, bid_base: np.ndarray, ask_base: np.ndarray) -> bool:
    return bool(np.all(bid_rel <= bid_base) and np.all(ask_rel >= ask_base))


def snapshot_rel(pay: dict[str, Any]) -> dict[str, Any]:
    bid_px, bid_qty = level_px_qty(pay, "Buy")
    ask_px, ask_qty = level_px_qty(pay, "Sell")
    valid = valid_full_depth(bid_px, bid_qty, ask_px, ask_qty)
    if not valid:
        return {"VALID_FULL_DEPTH": False, "UNKNOWN": True, "bid_rel": None, "ask_rel": None}
    br = relative_bid([float(x) for x in bid_px])  # type: ignore[arg-type]
    ar = relative_ask([float(x) for x in ask_px])  # type: ignore[arg-type]
    if br is None or ar is None:
        return {"VALID_FULL_DEPTH": False, "UNKNOWN": True, "bid_rel": None, "ask_rel": None}
    return {"VALID_FULL_DEPTH": True, "UNKNOWN": False, "bid_rel": br, "ask_rel": ar}


def translated_same(bid_px: list[float], ask_px: list[float], shift: float) -> bool:
    br0 = relative_bid(bid_px)
    ar0 = relative_ask(ask_px)
    br1 = relative_bid([x + shift for x in bid_px])
    ar1 = relative_ask([x + shift for x in ask_px])
    if br0 is None or ar0 is None or br1 is None or ar1 is None:
        return False
    return bool(np.allclose(br0, br1) and np.allclose(ar0, ar1))


def onset_false_to_true(prev_known: Optional[bool], cur_known: Optional[bool]) -> bool:
    if prev_known is None or cur_known is None:
        return False
    return (prev_known is False) and (cur_known is True)


def reset_known_on_unknown(cur_known: Optional[bool]) -> Optional[bool]:
    if cur_known is None:
        return None
    return bool(cur_known)


def scan_pairs(
    t: np.ndarray,
    valid: np.ndarray,
    bid: np.ndarray,
    ask: np.ndarray,
    flatten_t: float,
) -> dict[str, Any]:
    last: Optional[bool] = None
    onsets: list[tuple[int, int]] = []
    valid_pair_n = 0
    unknown_pair_n = 0
    bid_contract_n = 0
    ask_expand_n = 0
    joint_n = 0
    n = int(t.size)
    for i in range(1, n):
        if float(t[i]) + 1e-12 >= float(flatten_t):
            break
        if not bool(valid[i - 1]) or not bool(valid[i]):
            unknown_pair_n += 1
            last = None
            continue
        valid_pair_n += 1
        pb = bid[i - 1]
        cb = bid[i]
        pa = ask[i - 1]
        ca = ask[i]
        bc = bid_geometry_contracts(pb, cb)
        ae = ask_geometry_expands(pa, ca)
        if bc:
            bid_contract_n += 1
        if ae:
            ask_expand_n += 1
        joint = bool(bc and ae)
        if joint:
            joint_n += 1
        if onset_false_to_true(last, joint):
            onsets.append((int(i), int(i - 1)))
        last = reset_known_on_unknown(joint)
    return {
        "onsets": onsets,
        "VALID_CONSECUTIVE_PAIR_N": int(valid_pair_n),
        "UNKNOWN_PAIR_N": int(unknown_pair_n),
        "BID_GEOMETRY_CONTRACT_EVENT_N": int(bid_contract_n),
        "ASK_GEOMETRY_EXPAND_EVENT_N": int(ask_expand_n),
        "JOINT_FAVORABLE_MIGRATION_EVENT_N": int(joint_n),
        "FALSE_TO_TRUE_SIGNAL_N": int(len(onsets)),
    }


def first_baseline_loss_i(
    t: np.ndarray,
    valid: np.ndarray,
    bid: np.ndarray,
    ask: np.ndarray,
    *,
    fill_t: float,
    flatten_t: float,
    bid_base: np.ndarray,
    ask_base: np.ndarray,
) -> Optional[int]:
    i0 = int(np.searchsorted(t, float(fill_t), side="right"))
    n = int(t.size)
    for i in range(i0, n):
        ti = float(t[i])
        if ti + 1e-12 <= float(fill_t):
            continue
        if ti + 1e-12 > float(flatten_t):
            return None
        if not bool(valid[i]):
            continue
        if not thesis_valid(bid[i], ask[i], bid_base, ask_base):
            return int(i)
    return None
