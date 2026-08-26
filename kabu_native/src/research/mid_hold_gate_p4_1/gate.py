"""Research wrap: evaluate precommitted MID_HOLD_NO_PROGRESS_V1 on Dual Lane.

Does not edit Dual Lane source. Early Guard / 600_DECISION / Control unchanged.
"""
from __future__ import annotations

from typing import Any, Optional

from research.mid_hold_gate_p4_1 import (
    CANDIDATE_ID,
    CHECKPOINTS_SEC,
    DECISION_HORIZON_SEC,
    EXIT_REASON,
    GUARD_MONITOR_TO,
)
from small_paper.v1r_live_dual_lane import (
    BOARD_FRESH_SEC,
    MIN_BUY1_QTY,
    LanePosition,
)


def _tick_valid(pos: LanePosition, i: int) -> bool:
    if i < 0 or i >= len(pos.t):
        return False
    if pos.special and pos.special[i]:
        return False
    if pos.bid_qty and float(pos.bid_qty[i]) < MIN_BUY1_QTY - 1e-12:
        return False
    if pos.fresh_sec and float(pos.fresh_sec[i]) > BOARD_FRESH_SEC + 1e-12:
        return False
    if not pos.bid:
        return False
    px = float(pos.bid[i])
    return px > 0 and px == px


def _last_valid_idx(pos: LanePosition) -> Optional[int]:
    for i in range(len(pos.t) - 1, -1, -1):
        if _tick_valid(pos, i):
            return i
    return None


def _mfe_and_leak(pos: LanePosition, *, until_t: float) -> tuple[Optional[float], int]:
    mx = None
    leak = 0
    fill_t = float(pos.fill_time)
    for i, ti in enumerate(pos.t):
        t = float(ti)
        if t + 1e-12 < fill_t:
            continue
        if t > float(until_t) + 1e-12:
            leak += 1
            continue
        if not _tick_valid(pos, i):
            continue
        b = float(pos.bid[i])
        mx = b if mx is None or b > mx else mx
    return mx, leak


def _executable_now(pos: LanePosition) -> bool:
    if not pos.t:
        return False
    return _tick_valid(pos, len(pos.t) - 1)


def _mh_decision(pos: LanePosition) -> dict[str, Any]:
    i = len(pos.t) - 1
    px = float(pos.bid[i])
    off = float(pos.t[i]) - float(pos.fill_time)
    ret = (px / float(pos.fill_price) - 1.0) * 10000.0 if pos.fill_price else 0.0
    return {
        "exit": True,
        "lane": pos.lane,
        "symbol": pos.symbol,
        "reason": EXIT_REASON,
        "triggered_guard": False,
        "extended": False,
        "exit_off": off,
        "exit_time": float(pos.t[i]),
        "exit_ret_bps": float(ret),
        "exit_price": px,
        "arch": "E",
        "execution": "FIRST_VALID_EXECUTABLE_BUY1_AT_OR_AFTER_TRIGGER",
        "guard": None,
        "continuation": None,
        "mid_hold_candidate": CANDIDATE_ID,
        "first_trigger_checkpoint": getattr(pos, "mh_first_trigger", None),
    }


def _ensure_fields(pos: LanePosition) -> None:
    if not hasattr(pos, "mh_eval_done"):
        pos.mh_eval_done = set()
    if not hasattr(pos, "mh_armed"):
        pos.mh_armed = False
    if not hasattr(pos, "mh_first_trigger"):
        pos.mh_first_trigger = None
    if not hasattr(pos, "mh_stopped"):
        pos.mh_stopped = False


def attach_mid_hold_gate(
    dual: Any,
    *,
    live_exit: bool,
    precommit_sha: str,
    date: str,
) -> None:
    """Bind precommitted Gate onto this Dual Lane instance only."""
    orig_eval = dual._evaluate
    dual.mh_records = []
    dual.mh_leak_n = 0
    dual.mh_live_exit = bool(live_exit)
    dual.mh_precommit_sha = str(precommit_sha)

    def _evaluate(pos: LanePosition) -> Optional[dict[str, Any]]:
        orig = orig_eval(pos)
        try:
            return _after_orig(pos, orig)
        except Exception:
            return orig

    def _after_orig(pos: LanePosition, orig: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if pos.lane != "primary":
            return orig
        _ensure_fields(pos)
        if orig and orig.get("exit"):
            return orig

        if pos.mh_armed:
            if getattr(pos, "traced_600_decision", False):
                return orig
            off_now = float(pos.t[-1] - pos.fill_time) if pos.t else 0.0
            if off_now + 1e-9 >= DECISION_HORIZON_SEC:
                return orig
            if _executable_now(pos):
                return _mh_decision(pos)
            return orig

        if pos.mh_stopped:
            return orig
        if getattr(pos, "traced_600_decision", False):
            return orig
        if not pos.t:
            return orig
        off = float(pos.t[-1] - pos.fill_time)
        if off + 1e-9 >= DECISION_HORIZON_SEC:
            return orig
        if off + 1e-9 < GUARD_MONITOR_TO:
            return orig

        if getattr(pos, "traced_guard_trigger", False):
            return orig

        event_t = float(pos.t[-1])
        due = [h for h in CHECKPOINTS_SEC if off + 1e-9 >= float(h) and int(h) not in pos.mh_eval_done]
        if not due:
            return orig

        last_i = _last_valid_idx(pos)
        mx, leak = _mfe_and_leak(pos, until_t=event_t)
        dual.mh_leak_n += int(leak)
        fill_px = float(pos.fill_price or 0.0)
        bid_now = float(pos.bid[last_i]) if last_i is not None else None
        ret = (bid_now / fill_px - 1.0) if (bid_now is not None and fill_px > 0) else None
        mfe = (float(mx) / fill_px - 1.0) if (mx is not None and fill_px > 0) else None
        evaluable = ret is not None and mfe is not None and leak == 0
        gate_true = bool(evaluable and ret < 0.0 and mfe <= 0.0)

        triggered_h = None
        for h in due:
            pos.mh_eval_done.add(int(h))
            rec = {
                "date": date or pos.date,
                "symbol": pos.symbol,
                "fill_time": pos.fill_time,
                "fill_price": pos.fill_price,
                "checkpoint": int(h),
                "off": off,
                "event_t": event_t,
                "current_bid": bid_now,
                "current_bid_return": ret,
                "executable_mfe": mfe,
                "gate_true": bool(gate_true and triggered_h is None),
                "evaluable": evaluable,
                "leak_n": int(leak),
                "live_exit": bool(live_exit),
                "uneval_reason": None if evaluable else ("FUTURE_LEAK" if leak else "NO_VALID_BID"),
            }
            if gate_true and triggered_h is None:
                triggered_h = int(h)
                rec["first_trigger"] = True
            else:
                rec["first_trigger"] = False
            dual.mh_records.append(rec)
            if triggered_h is not None:
                break

        if triggered_h is None:
            return orig

        pos.mh_first_trigger = triggered_h
        pos.mh_stopped = True
        if not live_exit:
            return orig
        pos.mh_armed = True
        if _executable_now(pos):
            return _mh_decision(pos)
        return orig

    dual._evaluate = _evaluate  # type: ignore[method-assign]
