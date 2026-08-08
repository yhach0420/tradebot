"""Execution arms: aggressive / passive-bid / inside-spread — conservative fill only."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x10_risk_universe.tick import jpx_tick_size_yen
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint.board import (
    BOARD_FRESHNESS_SEC,
    MIN_QTY,
    first_valid_quote,
)
from research.e1_x30_absolute_rise_entry_v2.labels import _scan_episode
from research.e1_x33c_baseline_economics.quotes import (
    last_both_at_or_before,
    last_valid_at_or_before,
)

from . import (
    ARM_AGGRESSIVE,
    ARM_INSIDE,
    ARM_PASSIVE,
    FILL_EVIDENCE,
    HORIZONS_ALL,
)


def _bps(num: float, den: float) -> float:
    return (num / den - 1.0) * 10000.0


def _row_ask_ok(board: dict[str, np.ndarray], i: int) -> bool:
    if board["special"][i]:
        return False
    fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
    if fresh > BOARD_FRESHNESS_SEC + 1e-12:
        return False
    qty = board["ask_qty"][i]
    if not np.isfinite(qty) or qty < MIN_QTY:
        return False
    ask = board["ask"][i]
    return bool(np.isfinite(ask) and ask > 0)


def snapshot_t0(
    board: dict[str, np.ndarray],
    signal_t: float,
) -> dict[str, Any]:
    """First valid ask + bid at/after signal (mapping window 5s)."""
    qa = first_valid_quote(board, signal_t, side="ask")
    qb = first_valid_quote(board, signal_t, side="bid")
    if qa["status"] != "OK" or qb["status"] != "OK":
        return {
            "ok": False,
            "reason": qa["status"] if qa["status"] != "OK" else qb["status"],
        }
    ask = float(qa["price"])
    bid = float(qb["price"])
    if ask <= 0 or bid <= 0 or ask < bid:
        return {"ok": False, "reason": "INVALID_BOOK"}
    mid = (ask + bid) / 2.0
    return {
        "ok": True,
        "ask": ask,
        "bid": bid,
        "mid": mid,
        "ask_event_t": float(qa["event_time"]),
        "bid_event_t": float(qb["event_time"]),
        "spread_bps": (ask - bid) / mid * 10000.0,
    }


def inside_limit_price(bid: float, ask: float) -> dict[str, Any]:
    """Single fixed rule: bid + 1 JPX tick, only if strictly inside spread."""
    tick = float(jpx_tick_size_yen(bid))
    lim = bid + tick
    # round to tick grid conservatively
    n = round(lim / tick)
    lim = n * tick
    if lim <= bid + 1e-12 or lim >= ask - 1e-12:
        return {
            "ok": False,
            "reason": "NO_INSIDE_TICK",
            "tick": tick,
            "limit": None,
        }
    return {"ok": True, "tick": tick, "limit": float(lim)}


def find_ask_cross_fill(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    wait_sec: float,
    limit_price: float,
    sess_end: float,
) -> dict[str, Any]:
    """
    Conservative fill: first future snapshot with
      Sell1.Price <= limit AND qty>=100 AND freshness OK AND not special.
    fill_price = limit_price (no price improvement).
    Does NOT use last/trade touch or queue position.
    """
    t = board["t"]
    if t.size == 0:
        return {"filled": False, "reason": "NO_BOARD"}
    lim_t = min(float(t0) + float(wait_sec), float(sess_end))
    i0 = int(np.searchsorted(t, t0, side="left"))
    saw_book = False
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < t0:
            continue
        if ti > lim_t + 1e-12:
            break
        saw_book = True
        if board["special"][i]:
            continue  # not evidence of fill
        fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
        if fresh > BOARD_FRESHNESS_SEC + 1e-12:
            continue
        qty = board["ask_qty"][i]
        if not np.isfinite(qty) or qty < MIN_QTY:
            continue
        ask = float(board["ask"][i])
        if not np.isfinite(ask) or ask <= 0:
            continue
        if ask <= limit_price + 1e-12:
            return {
                "filled": True,
                "fill_price": float(limit_price),
                "fill_t": ti,
                "cross_ask": ask,
                "evidence": FILL_EVIDENCE,
                "waited_sec": float(ti - t0),
            }
    return {
        "filled": False,
        "reason": "NO_ASK_CROSS_IN_WINDOW" if saw_book else "NO_BOARD_IN_WINDOW",
        "fill_evidence_note": "PASSIVE_QUEUE_FILL_UNOBSERVABLE — queue/touch not used",
    }


def _horizon_returns(
    board: dict[str, np.ndarray],
    *,
    entry_price: float,
    entry_t: float,
    mid_t: float,
    date: str,
    session: str,
) -> dict[str, Any]:
    sess_end = session_end_epoch(date, session)
    ep = _scan_episode(board, ask=entry_price, ask_t=entry_t, sess_end=sess_end)
    out: dict[str, Any] = {
        "mfe": ep.get("mfe"),
        "mae": ep.get("mae"),
    }
    for H in HORIZONS_ALL:
        out[f"ret_{H}"] = None
        out[f"ret_{H}_valid"] = False
        out[f"mid_{H}"] = None
        out[f"mid_{H}_valid"] = False
        t_h = entry_t + float(H)
        if t_h > sess_end + 1e-9:
            continue
        if ep.get(f"return_{H}_valid"):
            out[f"ret_{H}"] = float(ep[f"return_{H}"])
            out[f"ret_{H}_valid"] = True
        j_both = last_both_at_or_before(board, t_h, t_min=entry_t)
        if j_both is not None and mid_t > 0:
            mid_h = (float(board["ask"][j_both]) + float(board["bid"][j_both])) / 2.0
            out[f"mid_{H}"] = _bps(mid_h, mid_t)
            out[f"mid_{H}_valid"] = True
    return out


def evaluate_aggressive(
    board: dict[str, np.ndarray],
    *,
    date: str,
    session: str,
    signal_t: float,
    snap: dict[str, Any],
) -> dict[str, Any]:
    """Arm A: first valid ask now — matches X33C / evaluate_long_at_signal."""
    qa = first_valid_quote(board, signal_t, side="ask")
    if qa["status"] != "OK":
        return {"arm": ARM_AGGRESSIVE, "signal_ok": False, "filled": False, "reason": qa["status"]}
    entry = float(qa["price"])
    entry_t = float(qa["event_time"])
    mid_t = float(snap["mid"])
    sess_end = session_end_epoch(date, session)
    # X33C / evaluate_long_at_signal requires scan ok (executable bid path exists)
    ep = _scan_episode(board, ask=entry, ask_t=entry_t, sess_end=sess_end)
    if not ep.get("ok"):
        return {"arm": ARM_AGGRESSIVE, "signal_ok": False, "filled": False, "reason": "NO_BID_PATH"}
    hr = _horizon_returns(
        board, entry_price=entry, entry_t=entry_t, mid_t=mid_t, date=date, session=session,
    )
    return {
        "arm": ARM_AGGRESSIVE,
        "signal_ok": True,
        "filled": True,
        "fill_price": entry,
        "fill_t": entry_t,
        "limit_price": None,
        "aggressive_ask": entry,
        "entry_spread_saved_bps": 0.0,
        "waited_sec": float(entry_t - signal_t),
        "evidence": "AGGRESSIVE_ASK",
        **hr,
    }


def evaluate_passive_or_inside(
    board: dict[str, np.ndarray],
    *,
    date: str,
    session: str,
    signal_t: float,
    snap: dict[str, Any],
    arm: str,
    wait_sec: float,
) -> dict[str, Any]:
    sess_end = session_end_epoch(date, session)
    ask0 = float(snap["ask"])
    bid0 = float(snap["bid"])
    mid_t = float(snap["mid"])

    if arm == ARM_PASSIVE:
        limit = bid0
        setup_ok = True
        setup_reason = None
        tick = None
    elif arm == ARM_INSIDE:
        ins = inside_limit_price(bid0, ask0)
        if not ins["ok"]:
            return {
                "arm": arm,
                "signal_ok": True,
                "filled": False,
                "reason": ins["reason"],
                "limit_price": None,
                "tick": ins.get("tick"),
                "aggressive_ask": ask0,
                "entry_spread_saved_bps": None,
                "opportunity_zero": True,
            }
        limit = float(ins["limit"])
        setup_ok = True
        setup_reason = None
        tick = ins["tick"]
    else:
        raise ValueError(arm)

    fill = find_ask_cross_fill(
        board, t0=signal_t, wait_sec=wait_sec, limit_price=limit, sess_end=sess_end,
    )
    base = {
        "arm": arm,
        "signal_ok": True,
        "limit_price": limit,
        "tick": tick,
        "aggressive_ask": ask0,
        "setup_ok": setup_ok,
        "setup_reason": setup_reason,
        "wait_sec": float(wait_sec),
        "fill_evidence_rule": FILL_EVIDENCE,
        "no_queue_assumption": True,
    }
    if not fill.get("filled"):
        return {
            **base,
            "filled": False,
            "reason": fill.get("reason"),
            "opportunity_zero": True,
            "entry_spread_saved_bps": None,
        }

    entry = float(fill["fill_price"])
    entry_t = float(fill["fill_t"])
    saved = (ask0 - entry) / ask0 * 10000.0
    hr = _horizon_returns(
        board, entry_price=entry, entry_t=entry_t, mid_t=mid_t, date=date, session=session,
    )
    return {
        **base,
        "filled": True,
        "fill_price": entry,
        "fill_t": entry_t,
        "cross_ask": fill.get("cross_ask"),
        "waited_sec": fill.get("waited_sec"),
        "evidence": fill.get("evidence"),
        "entry_spread_saved_bps": float(saved),
        "opportunity_zero": False,
        **hr,
    }


def evaluate_signal_all_arms(
    board: dict[str, np.ndarray],
    *,
    date: str,
    session: str,
    signal_t: float,
    wait_sec: float,
) -> Optional[dict[str, Any]]:
    """
    Signal set = X33C aggressive-eligible (valid ask). Bid required only for
    passive/inside limit placement; missing bid ⇒ those arms unfilled (0).
    """
    qa = first_valid_quote(board, signal_t, side="ask")
    if qa["status"] != "OK":
        return None
    ask0 = float(qa["price"])
    qb = first_valid_quote(board, signal_t, side="bid")
    if qb["status"] == "OK":
        bid0 = float(qb["price"])
        mid0 = (ask0 + bid0) / 2.0
        snap = {
            "ok": True,
            "ask": ask0,
            "bid": bid0,
            "mid": mid0,
            "ask_event_t": float(qa["event_time"]),
            "bid_event_t": float(qb["event_time"]),
            "spread_bps": (ask0 - bid0) / mid0 * 10000.0 if mid0 > 0 else None,
        }
    else:
        # mid fallback for aggressive path diagnostics only
        snap = {
            "ok": True,
            "ask": ask0,
            "bid": None,
            "mid": ask0,
            "ask_event_t": float(qa["event_time"]),
            "bid_event_t": None,
            "spread_bps": None,
            "bid_missing": True,
        }

    agg = evaluate_aggressive(
        board, date=date, session=session, signal_t=signal_t, snap=snap,
    )
    if not agg.get("filled"):
        return None

    if snap.get("bid") is None:
        no_bid = {
            "signal_ok": True,
            "filled": False,
            "reason": "NO_BID_FOR_LIMIT",
            "opportunity_zero": True,
            "aggressive_ask": ask0,
            "entry_spread_saved_bps": None,
            "limit_price": None,
        }
        pas = {**no_bid, "arm": ARM_PASSIVE}
        ins = {**no_bid, "arm": ARM_INSIDE}
    else:
        pas = evaluate_passive_or_inside(
            board, date=date, session=session, signal_t=signal_t, snap=snap,
            arm=ARM_PASSIVE, wait_sec=wait_sec,
        )
        ins = evaluate_passive_or_inside(
            board, date=date, session=session, signal_t=signal_t, snap=snap,
            arm=ARM_INSIDE, wait_sec=wait_sec,
        )
    return {
        "date": date,
        "symbol": None,
        "session": session,
        "signal_t": float(signal_t),
        "ask0": ask0,
        "bid0": snap.get("bid"),
        "mid0": snap.get("mid"),
        "spread_bps": snap.get("spread_bps"),
        "aggressive": agg,
        "passive": pas,
        "inside": ins,
        "wait_sec": float(wait_sec),
    }
