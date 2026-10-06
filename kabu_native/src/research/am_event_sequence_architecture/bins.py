"""Causal 180 x 1s bins from valid continuous-board events. No future fill."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_event_sequence_architecture import (
    AVAIL_IDX,
    SEQUENCE_CHANNEL_N,
    SEQUENCE_LEN,
    SEQUENCE_WINDOW_SEC,
)
from research.entry_sequence_representation.sequence import (
    continuous_valid_mask,
    itayose_mask,
    special_mask,
)
from research.raw_event_information_audit.events import extract_raw_indices


def _spread_bps(bid: float, ask: float) -> float:
    mid = 0.5 * (bid + ask)
    if mid <= 0:
        return 0.0
    return (ask - bid) / mid * 10000.0


def _imbalance(bq: float, aq: float) -> float:
    den = bq + aq
    if den <= 0:
        return 0.0
    return (bq - aq) / den


def bin_index(t: float, t0: float) -> int:
    lo = float(t0) - float(SEQUENCE_WINDOW_SEC)
    rel = float(t) - lo
    if rel <= 0:
        return 0
    k = int(np.floor(rel))
    if k >= int(SEQUENCE_LEN):
        return int(SEQUENCE_LEN) - 1
    return k


def bin_sequence(
    *,
    board: dict[str, np.ndarray],
    t0: float,
    t_lo: Optional[float],
) -> tuple[np.ndarray, dict[str, int]]:
    acc = {
        "event_time_after_t0_n": 0,
        "session_carry_n": 0,
        "itayose_event_use_n": 0,
        "special_event_use_n": 0,
        "future_event_use_n": 0,
        "same_timestamp_after_decision_use_n": 0,
    }
    x = np.zeros((int(SEQUENCE_LEN), int(SEQUENCE_CHANNEL_N)), dtype=np.float32)
    t = np.asarray(board.get("t") if board.get("t") is not None else [], dtype=float)
    n = int(t.size)
    if n == 0:
        return x, acc
    valid = continuous_valid_mask(board)
    itay = itayose_mask(board)
    specm = special_mask(board)
    bid = np.asarray(board["bid"][:n], dtype=float)
    ask = np.asarray(board["ask"][:n], dtype=float)
    bq = np.asarray(board["bid_qty"][:n], dtype=float)
    aq = np.asarray(board["ask_qty"][:n], dtype=float)
    idx, raw_acc = extract_raw_indices(t=t, valid=valid, itay=itay, specm=specm, t0=t0, t_lo=t_lo)
    for k in acc:
        if k in raw_acc:
            acc[k] += int(raw_acc.get(k) or 0)
    if int(idx.size) == 0:
        return x, acc

    abs_bq = np.zeros(int(SEQUENCE_LEN), dtype=np.float64)
    abs_aq = np.zeros(int(SEQUENCE_LEN), dtype=np.float64)
    have_state = np.zeros(int(SEQUENCE_LEN), dtype=bool)
    prev_mid = None
    prev_imb = None
    prev_sp = None
    prev_bq = None
    prev_aq = None
    for i in idx:
        ti = float(t[i])
        if ti > float(t0) + 1e-12:
            acc["event_time_after_t0_n"] += 1
            acc["future_event_use_n"] += 1
            acc["same_timestamp_after_decision_use_n"] += 1
            continue
        k = bin_index(ti, float(t0))
        bb = float(bid[i])
        aa = float(ask[i])
        qb = float(bq[i])
        qa = float(aq[i])
        mid = 0.5 * (bb + aa)
        imb = _imbalance(qb, qa)
        sp = _spread_bps(bb, aa)
        x[k, 0] += 1.0
        if prev_mid is not None and prev_mid > 0 and mid > 0:
            x[k, 1] += np.float32((mid - prev_mid) / prev_mid * 10000.0)
        if prev_imb is not None:
            x[k, 2] += np.float32(abs(imb - prev_imb))
        if prev_sp is not None and abs(sp - prev_sp) > 1e-8:
            x[k, 3] += 1.0
        if prev_bq is not None:
            abs_bq[k] += abs(qb - prev_bq)
        if prev_aq is not None:
            abs_aq[k] += abs(qa - prev_aq)
        x[k, 6] = np.float32(sp)
        x[k, 7] = np.float32(imb)
        x[k, 8] = np.float32(np.log1p(max(qb, 0.0)))
        x[k, 9] = np.float32(np.log1p(max(qa, 0.0)))
        have_state[k] = True
        prev_mid = mid
        prev_imb = imb
        prev_sp = sp
        prev_bq = qb
        prev_aq = qa
    x[:, 4] = np.log1p(abs_bq).astype(np.float32)
    x[:, 5] = np.log1p(abs_aq).astype(np.float32)

    last = None
    for k in range(int(SEQUENCE_LEN)):
        if have_state[k]:
            last = x[k, 6:10].copy()
            x[k, AVAIL_IDX] = 1.0
        elif last is not None:
            x[k, 6:10] = last
            x[k, AVAIL_IDX] = 1.0
        else:
            x[k, 6:10] = 0.0
            x[k, AVAIL_IDX] = 0.0
    return x, acc
