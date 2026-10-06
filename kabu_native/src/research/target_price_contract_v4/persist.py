"""M4 persistence helpers. Continuity cuts, inter-event audit, next-event diagnostic."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.executable_board import (
    STATE_NOT_OPENED,
    STATE_PREOPEN_ITAYOSE,
)
from research.executable_target_v2_coverage_audit.session_sot import tse_phase_epoch
from research.target_price_contract_v3.marks import _blocked_itayose_special
from research.target_price_contract_v4 import INTERVAL_BUCKETS

CONT_PHASES = frozenset({"AM_CONTINUOUS", "PM_CONTINUOUS_ZARABA"})


def interval_bucket(dt: float) -> str:
    x = float(dt)
    for lo, hi, name in INTERVAL_BUCKETS:
        if hi == float("inf"):
            if x >= lo:
                return name
            continue
        if lo <= x < hi:
            return name
    return ">300s"


def _valid_quote(board: dict[str, np.ndarray], j: int) -> bool:
    if _blocked_itayose_special(board, j):
        return False
    bid = float(board["bid"][j]) if np.isfinite(board["bid"][j]) else float("nan")
    ask = float(board["ask"][j]) if np.isfinite(board["ask"][j]) else float("nan")
    return bool(np.isfinite(bid) and np.isfinite(ask) and bid > 0 and ask > bid + 1e-12)


def reversal_cut_times(t: np.ndarray) -> list[float]:
    if t is None or t.size < 2:
        return []
    out: list[float] = []
    for i in range(1, int(t.size)):
        if float(t[i]) + 1e-6 < float(t[i - 1]):
            out.append(float(t[i]))
    return out


def last_cut_at_or_before(cuts: list[float], t_at: float) -> Optional[float]:
    hit = None
    for c in cuts:
        if float(c) <= float(t_at) + 1e-12:
            if hit is None or float(c) > float(hit):
                hit = float(c)
    return hit


def m4_t_lo(
    *,
    session_lo: Optional[float],
    reversal_cuts: list[float],
    restart_cuts: list[float],
    t_at: float,
) -> tuple[Optional[float], str]:
    """Lower bound for walking last valid quote. No MAX_MARK_AGE_SEC."""
    candidates: list[tuple[float, str]] = []
    if session_lo is not None:
        candidates.append((float(session_lo), "session"))
    rev = last_cut_at_or_before(reversal_cuts, t_at)
    if rev is not None:
        candidates.append((float(rev), "time_reversal"))
    rst = last_cut_at_or_before(restart_cuts, t_at)
    if rst is not None:
        candidates.append((float(rst), "seq_reset"))
    if not candidates:
        return None, "none"
    t_lo, reason = max(candidates, key=lambda x: x[0])
    return float(t_lo), reason


def _px_at(board: dict[str, np.ndarray], j: int) -> Optional[float]:
    px = board.get("last_px")
    if px is None or j >= int(px.size):
        return None
    v = float(px[j])
    return v if np.isfinite(v) else None


def _state_at(board: dict[str, np.ndarray], j: int) -> str:
    st = board.get("board_execution_state")
    if st is None or j >= int(getattr(st, "size", 0) or 0):
        return ""
    return str(st[j] or "")


def symbol_persist_audit(
    day: str,
    symbol: str,
    board: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Inter-event durations from valid quotes, plus next-event diagnostic for dt>5s.

    Next-event stats are diagnostic only. Never a label eligibility gate.
    Session-boundary intervals (lunch / AM close / auction) are counted separately
    and are not treated as Capture gaps or as persistence evidence.
    """
    t = board.get("t")
    empty = {
        "n_valid_quotes": 0,
        "n_intervals_all": 0,
        "n_same_session": 0,
        "n_cross_session": 0,
        "buckets": {},
        "bucket_symbols": {},
        "next_gt5": {},
    }
    if t is None or t.size < 2:
        return empty
    n = int(t.size)
    buckets: Counter = Counter()
    bucket_in_sym: set[str] = set()
    n_all = n_same = n_cross = n_valid = 0
    next_gt5 = Counter()
    for i in range(n - 1):
        if not _valid_quote(board, i):
            continue
        n_valid += 1
        dt = float(t[i + 1]) - float(t[i])
        if dt < 0:
            continue
        n_all += 1
        p0 = tse_phase_epoch(day, float(t[i]))
        p1 = tse_phase_epoch(day, float(t[i + 1]))
        same = p0 in CONT_PHASES and p0 == p1
        if not same:
            n_cross += 1
            continue
        n_same += 1
        bname = interval_bucket(dt)
        buckets[bname] += 1
        bucket_in_sym.add(bname)
        if dt <= 5.0 + 1e-12:
            continue
        # Diagnostic: next event after a >5s carry. Not a mark gate.
        next_gt5["n"] += 1
        nxt_valid = _valid_quote(board, i + 1)
        nxt_blocked = _blocked_itayose_special(board, i + 1)
        next_gt5["next_valid_continuous_quote"] += int(bool(nxt_valid))
        next_gt5["next_itayose_or_special"] += int(bool(nxt_blocked))
        bid0 = float(board["bid"][i])
        ask0 = float(board["ask"][i])
        bid1 = float(board["bid"][i + 1]) if np.isfinite(board["bid"][i + 1]) else float("nan")
        ask1 = float(board["ask"][i + 1]) if np.isfinite(board["ask"][i + 1]) else float("nan")
        bid_same = np.isfinite(bid1) and abs(bid1 - bid0) <= 1e-6
        ask_same = np.isfinite(ask1) and abs(ask1 - ask0) <= 1e-6
        if bid_same and ask_same:
            next_gt5["bid_ask_unchanged"] += 1
        elif (not bid_same) and ask_same:
            next_gt5["bid_changed_ask_same"] += 1
        elif bid_same and (not ask_same):
            next_gt5["ask_changed_bid_same"] += 1
        else:
            next_gt5["bid_and_ask_changed"] += 1
        st0 = _state_at(board, i)
        st1 = _state_at(board, i + 1)
        next_gt5["session_state_unchanged"] += int(st0 == st1)
        next_gt5["session_state_changed"] += int(st0 != st1)
        px0 = _px_at(board, i)
        px1 = _px_at(board, i + 1)
        if px0 is None or px1 is None:
            next_gt5["current_price_missing"] += 1
        elif abs(px0 - px1) <= 1e-6:
            next_gt5["current_price_unchanged"] += 1
        else:
            next_gt5["current_price_changed"] += 1
        if st1 in {STATE_NOT_OPENED, STATE_PREOPEN_ITAYOSE}:
            next_gt5["next_not_opened_or_preopen"] += 1
    return {
        "n_valid_quotes": n_valid,
        "n_intervals_all": n_all,
        "n_same_session": n_same,
        "n_cross_session": n_cross,
        "buckets": dict(buckets),
        "bucket_symbols": sorted(bucket_in_sym),
        "next_gt5": dict(next_gt5),
    }


def merge_day_persist(parts: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: Counter = Counter()
    bucket_syms: dict[str, set[str]] = defaultdict(set)
    next_gt5: Counter = Counter()
    n_valid = n_all = n_same = n_cross = 0
    n_rev = 0
    for p in parts:
        n_valid += int(p.get("n_valid_quotes") or 0)
        n_all += int(p.get("n_intervals_all") or 0)
        n_same += int(p.get("n_same_session") or 0)
        n_cross += int(p.get("n_cross_session") or 0)
        n_rev += int(p.get("n_reversals") or 0)
        sym = str(p.get("symbol") or "")
        for k, v in (p.get("buckets") or {}).items():
            buckets[k] += int(v)
            if int(v) > 0 and sym:
                bucket_syms[k].add(sym)
        for k, v in (p.get("next_gt5") or {}).items():
            next_gt5[k] += int(v)
    return {
        "n_valid_quotes": n_valid,
        "n_intervals_all": n_all,
        "n_same_session": n_same,
        "n_cross_session": n_cross,
        "n_reversals": n_rev,
        "buckets": dict(buckets),
        "bucket_symbol_n": {k: len(v) for k, v in bucket_syms.items()},
        "next_gt5": dict(next_gt5),
    }
