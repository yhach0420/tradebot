"""Uncompacted Capture boards. Same extract_board_row / event_t as P1 stream. No compact_tail."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from small_paper.v1r_live_dual_lane import canonical_symbol_key
from small_paper.v1r_native_entry_live import _BoardBuf, extract_board_row
from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def load_uncompacted_boards(capture_dir, universe: list[str]) -> dict[str, dict[str, np.ndarray]]:
    """Full-day per-symbol boards. Duplicate sequences skipped. Universe filtered. Never compacted."""
    uni = {canonical_symbol_key(s) for s in universe}
    bufs: dict[str, _BoardBuf] = {s: _BoardBuf() for s in uni}
    seen_seq: set[int] = set()
    for rec in iter_push(capture_dir):
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None:
            continue
        try:
            seq = int(rec.get("sequence") or pay.get("sequence") or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq and seq in seen_seq:
            continue
        if seq:
            seen_seq.add(seq)
        sym = canonical_symbol_key(_bare(rec.get("symbol")))
        if sym not in uni:
            continue
        recv = record_event_stamp(rec) or datetime.fromtimestamp(float(et), JST).isoformat(
            timespec="milliseconds"
        )
        pay["received_at"] = recv
        pay["recorded_at"] = recv
        pay["sequence"] = seq
        pay["__ingress_sequence__"] = seq
        pay["__ingress_received_at__"] = recv
        row = extract_board_row(pay, float(et))
        bufs[sym].append(row)
    return {s: b.view() for s, b in bufs.items()}


def last_bid_at_or_before(board: dict[str, np.ndarray], t0: float) -> Optional[float]:
    t = board.get("t")
    if t is None or t.size == 0:
        return None
    i = int(np.searchsorted(t, float(t0), side="right") - 1)
    if i < 0:
        return None
    bid = float(board["bid"][i])
    if not np.isfinite(bid) or bid <= 0:
        return None
    return bid


def ticks_in_wait(board: dict[str, np.ndarray], t0: float, wait_sec: float) -> dict[str, Any]:
    t = board.get("t") if board else None
    out = {"n": 0, "first_t": None, "last_t": None, "min_t": None, "max_t": None}
    if t is None or getattr(t, "size", 0) == 0:
        return out
    lim_t = float(t0) + float(wait_sec)
    i0 = int(np.searchsorted(t, float(t0), side="left"))
    n = 0
    first = last = None
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < float(t0):
            continue
        if ti > lim_t + 1e-12:
            break
        n += 1
        if first is None:
            first = ti
        last = ti
    out["n"] = n
    out["first_t"] = first
    out["last_t"] = last
    if t.size:
        out["min_t"] = float(t[0])
        out["max_t"] = float(t[-1])
    return out
