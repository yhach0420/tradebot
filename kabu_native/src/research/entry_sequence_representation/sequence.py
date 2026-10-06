"""Causal 180s / 5s grid snapshots. Last valid continuous board t<=mark. No future fill."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.executable_board import PREOPEN_ITAYOSE_SIGNS, SPECIAL_QUOTE_SIGNS
from research.entry_sequence_representation import (
    CHANNELS,
    MARK_OFFSETS,
    N_MARKS,
    SEQ_FEATURES,
    SEQUENCE_FEATURE_N,
)
from research.target_price_contract_v3.marks import ITAYOSE_STATES, SPECIAL_STATES


def continuous_valid_mask(board: dict[str, np.ndarray]) -> np.ndarray:
    t = board.get("t")
    if t is None or t.size == 0:
        return np.asarray([], dtype=bool)
    n = int(t.size)
    bid = np.asarray(board["bid"][:n], dtype=float)
    ask = np.asarray(board["ask"][:n], dtype=float)
    bq = np.asarray(board["bid_qty"][:n], dtype=float) if board.get("bid_qty") is not None else np.full(n, np.nan)
    aq = np.asarray(board["ask_qty"][:n], dtype=float) if board.get("ask_qty") is not None else np.full(n, np.nan)
    good = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid + 1e-12)
    good &= np.isfinite(bq) & np.isfinite(aq) & (bq > 0) & (aq > 0)
    spec = board.get("special")
    if spec is not None and int(spec.size) >= n:
        good &= ~np.asarray(spec[:n], dtype=bool)
    st = board.get("board_execution_state")
    if st is not None and int(st.size) >= n:
        s = np.asarray(st[:n], dtype=object)
        blocked = np.zeros(n, dtype=bool)
        for name in ITAYOSE_STATES | SPECIAL_STATES:
            blocked |= s == name
        good &= ~blocked
    for key, bad in (("ask_sign", PREOPEN_ITAYOSE_SIGNS | SPECIAL_QUOTE_SIGNS), ("bid_sign", PREOPEN_ITAYOSE_SIGNS | SPECIAL_QUOTE_SIGNS)):
        arr = board.get(key)
        if arr is None or int(getattr(arr, "size", 0) or 0) < n:
            continue
        a = np.asarray(arr[:n], dtype=object)
        blocked = np.zeros(n, dtype=bool)
        for name in bad:
            blocked |= a == name
        good &= ~blocked
    return good


def itayose_mask(board: dict[str, np.ndarray]) -> np.ndarray:
    t = board.get("t")
    n = int(t.size) if t is not None else 0
    out = np.zeros(n, dtype=bool)
    st = board.get("board_execution_state")
    if st is not None and int(st.size) >= n and n:
        s = np.asarray(st[:n], dtype=object)
        for name in ITAYOSE_STATES:
            out |= s == name
    ask_s = board.get("ask_sign")
    if ask_s is not None and int(getattr(ask_s, "size", 0) or 0) >= n and n:
        a = np.asarray(ask_s[:n], dtype=object)
        for name in PREOPEN_ITAYOSE_SIGNS:
            out |= a == name
    return out


def special_mask(board: dict[str, np.ndarray]) -> np.ndarray:
    t = board.get("t")
    n = int(t.size) if t is not None else 0
    out = np.zeros(n, dtype=bool)
    spec = board.get("special")
    if spec is not None and int(spec.size) >= n and n:
        out |= np.asarray(spec[:n], dtype=bool)
    st = board.get("board_execution_state")
    if st is not None and int(st.size) >= n and n:
        s = np.asarray(st[:n], dtype=object)
        for name in SPECIAL_STATES:
            out |= s == name
    ask_s = board.get("ask_sign")
    if ask_s is not None and int(getattr(ask_s, "size", 0) or 0) >= n and n:
        a = np.asarray(ask_s[:n], dtype=object)
        for name in SPECIAL_QUOTE_SIGNS:
            out |= a == name
    return out


def _last_idx(t: np.ndarray, mark: float) -> int:
    if t.size == 0:
        return -1
    i = int(np.searchsorted(t, float(mark), side="right") - 1)
    return i


def snapshot_at(
    *,
    t: np.ndarray,
    valid: np.ndarray,
    itay: np.ndarray,
    specm: np.ndarray,
    mark: float,
    t0: float,
    t_lo: Optional[float],
) -> tuple[int, dict[str, int]]:
    """Last valid continuous board with event_time<=mark in the same session. Do not emit forbidden states."""
    flags = {
        "future_event_use": 0,
        "session_carry": 0,
        "itayose_state_use": 0,
        "special_state_use": 0,
        "sequence_after_t0": 0,
    }
    i = _last_idx(t, mark)
    lo = float(t_lo) if t_lo is not None else float("-inf")
    while i >= 0:
        ti = float(t[i])
        if ti > float(mark) + 1e-12:
            i -= 1
            continue
        if ti > float(t0) + 1e-12:
            i -= 1
            continue
        if ti < lo - 1e-12:
            return -1, flags
        if bool(valid[i]):
            # Never emit itayose/special/pre-open. Walk back; missing if none remain.
            if bool(itay[i]) or bool(specm[i]):
                i -= 1
                continue
            if ti > float(mark) + 1e-12:
                flags["future_event_use"] = 1
                return -1, flags
            if ti > float(t0) + 1e-12:
                flags["sequence_after_t0"] = 1
                return -1, flags
            if ti < lo - 1e-12:
                flags["session_carry"] = 1
                return -1, flags
            return i, flags
        i -= 1
    return -1, flags


def flatten_sequence(
    *,
    board: dict[str, np.ndarray],
    t0: float,
    t_lo: Optional[float],
) -> tuple[list[float], dict[str, int]]:
    acc = {
        "future_event_use_n": 0,
        "session_carry_n": 0,
        "itayose_state_use_n": 0,
        "special_state_use_n": 0,
        "sequence_after_t0_n": 0,
        "grid_marks": int(N_MARKS),
    }
    t = np.asarray(board.get("t") if board.get("t") is not None else [], dtype=float)
    n = int(t.size)
    empty = [0.0] * int(SEQUENCE_FEATURE_N)
    if n == 0:
        return empty, acc
    valid = continuous_valid_mask(board)
    itay = itayose_mask(board)
    specm = special_mask(board)
    bid = np.asarray(board["bid"][:n], dtype=float)
    ask = np.asarray(board["ask"][:n], dtype=float)
    bq = np.asarray(board["bid_qty"][:n], dtype=float)
    aq = np.asarray(board["ask_qty"][:n], dtype=float)
    i0, _f0 = snapshot_at(t=t, valid=valid, itay=itay, specm=specm, mark=float(t0), t0=float(t0), t_lo=t_lo)
    mid0 = None
    if i0 >= 0:
        mid0 = 0.5 * (float(bid[i0]) + float(ask[i0]))
        if not (np.isfinite(mid0) and mid0 > 0):
            mid0 = None
    out: list[float] = []
    for off in MARK_OFFSETS:
        mark = float(t0) - float(off)
        i, fl = snapshot_at(t=t, valid=valid, itay=itay, specm=specm, mark=mark, t0=float(t0), t_lo=t_lo)
        acc["future_event_use_n"] += int(fl.get("future_event_use") or 0)
        acc["session_carry_n"] += int(fl.get("session_carry") or 0)
        acc["itayose_state_use_n"] += int(fl.get("itayose_state_use") or 0)
        acc["special_state_use_n"] += int(fl.get("special_state_use") or 0)
        acc["sequence_after_t0_n"] += int(fl.get("sequence_after_t0") or 0)
        if i < 0:
            out.extend([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            continue
        b = float(bid[i])
        a = float(ask[i])
        mid = 0.5 * (b + a)
        qb = float(bq[i])
        qa = float(aq[i])
        den = qb + qa
        imb = (qb - qa) / den if den > 0 else 0.0
        spread = (a - b) / mid * 10000.0 if mid > 0 else 0.0
        rel = ((mid / mid0 - 1.0) * 10000.0) if (mid0 is not None and mid0 > 0 and mid > 0) else 0.0
        src_t = float(t[i])
        if src_t > mark + 1e-12:
            acc["future_event_use_n"] += 1
        if src_t > float(t0) + 1e-12:
            acc["sequence_after_t0_n"] += 1
        if t_lo is not None and src_t < float(t_lo) - 1e-12:
            acc["session_carry_n"] += 1
        if bool(itay[i]):
            acc["itayose_state_use_n"] += 1
        if bool(specm[i]):
            acc["special_state_use_n"] += 1
        age = float(mark) - src_t
        out.extend(
            [
                float(rel),
                float(spread),
                float(imb),
                float(np.log1p(max(qb, 0.0))),
                float(np.log1p(max(qa, 0.0))),
                float(age),
                1.0,
            ]
        )
    if len(out) != int(SEQUENCE_FEATURE_N):
        raise RuntimeError(f"sequence length {len(out)} != {SEQUENCE_FEATURE_N}")
    return out, acc


def seq_to_row(vec: list[float]) -> dict[str, float]:
    xs = list(vec)
    if len(xs) < int(SEQUENCE_FEATURE_N):
        xs.extend([0.0] * (int(SEQUENCE_FEATURE_N) - len(xs)))
    return {name: float(xs[i]) for i, name in enumerate(SEQ_FEATURES)}


def row_to_vec(row: dict[str, Any]) -> list[float]:
    seq = row.get("seq")
    if isinstance(seq, list) and len(seq) == int(SEQUENCE_FEATURE_N):
        return [float(x) for x in seq]
    return [float(row.get(n) or 0.0) for n in SEQ_FEATURES]
