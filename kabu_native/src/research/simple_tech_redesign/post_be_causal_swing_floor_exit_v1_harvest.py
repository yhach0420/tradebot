"""Harvest POST_BE_CONFIRMED_SWING_FLOOR_V1 on the full B1 occupancy candidate stream."""
from __future__ import annotations

import gc
import json
from typing import Any, Optional, Sequence

import numpy as np

from replay.pnl_yen import compute_pnl_yen_100
from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_redesign.branch_u_bb_harvest import first_causal_bid_evidence
from research.simple_tech_redesign.branch_u_causal_harvest import replay_causal_day
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign.exit_lifecycle_harvest import walk_break_even
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_spec import (
    BE_PARITY_TOL_SEC,
    CANDIDATE_EXIT_REASON,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    TRIGGER_NAME,
)
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v24_harvest import _finite

PATH_CACHE = RESEARCH_CACHE / "post_be_causal_swing_floor_exit_v1"
PX_EPS = 1e-9
STATE_UNPROVEN = "UNPROVEN"
STATE_PROVEN_ARMED = "PROVEN_ARMED"
STATE_FLOOR_ACTIVE = "STRUCTURE_FLOOR_ACTIVE"
STATE_TRIGGERED = "TRIGGERED"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _seq(arr: Any) -> list[Optional[float]]:
    if arr is None:
        return []
    out: list[Optional[float]] = []
    n = int(getattr(arr, "size", 0) or 0)
    for i in range(n):
        out.append(float(arr[i]) if _finite(arr[i]) else None)
    return out


def is_confirmed_swing_low(low_jm1: Optional[float], low_j: Optional[float], low_jp1: Optional[float]) -> bool:
    if low_jm1 is None or low_j is None or low_jp1 is None:
        return False
    return float(low_j) < float(low_jm1) and float(low_j) < float(low_jp1)


def walk_post_be_swing_floor(
    *,
    low: Sequence[Optional[float]],
    close: Sequence[Optional[float]],
    minute_epoch: Sequence[Optional[float]],
    finalize_t: Sequence[Optional[float]],
    first_be_t: float,
    sess_end: float,
) -> dict[str, Any]:
    n = min(len(low), len(close), len(minute_epoch), len(finalize_t))
    floor: Optional[float] = None
    state = STATE_PROVEN_ARMED
    trigger_t: Optional[float] = None
    trigger_close: Optional[float] = None
    trigger_floor: Optional[float] = None
    first_floor_t: Optional[float] = None
    first_floor_px: Optional[float] = None
    ratchet_n = 0
    pivot_n = 0
    backdate_n = 0
    last_confirm_t: Optional[float] = None
    for i in range(n):
        ft = finalize_t[i]
        if ft is None:
            continue
        if float(ft) > float(sess_end) + PX_EPS:
            break
        if i >= 2:
            j = i - 1
            if is_confirmed_swing_low(low[j - 1], low[j], low[i]):
                confirm_t = float(ft)
                center_t = minute_epoch[j]
                if center_t is None:
                    continue
                if confirm_t + PX_EPS < float(center_t):
                    backdate_n += 1
                    continue
                if float(center_t) <= float(first_be_t) + PX_EPS:
                    continue
                pivot_n += 1
                last_confirm_t = confirm_t
                pl = float(low[j]) if low[j] is not None else None
                if pl is None:
                    continue
                if floor is None:
                    floor = pl
                    state = STATE_FLOOR_ACTIVE
                    first_floor_t = confirm_t
                    first_floor_px = pl
                elif pl > float(floor) + PX_EPS:
                    floor = pl
                    ratchet_n += 1
        if state == STATE_FLOOR_ACTIVE and floor is not None:
            cl = close[i]
            if cl is not None and float(cl) < float(floor) - PX_EPS:
                trigger_t = float(ft)
                trigger_close = float(cl)
                trigger_floor = float(floor)
                state = STATE_TRIGGERED
                break
    return {
        "state_end": state,
        "swing_floor": floor,
        "floor_formed": floor is not None,
        "first_floor_t": first_floor_t,
        "first_floor_px": first_floor_px,
        "ratchet_n": int(ratchet_n),
        "post_be_pivot_n": int(pivot_n),
        "last_confirm_t": last_confirm_t,
        "triggered": state == STATE_TRIGGERED and trigger_t is not None,
        "trigger_t": trigger_t,
        "trigger_close": trigger_close,
        "trigger_floor": trigger_floor,
        "pivot_backdate_n": int(backdate_n),
    }


def _pack_exit(*, reason: str, fill_px: float, pack: dict[str, Any], extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    miss = bool(pack.get("miss"))
    exit_bid = pack.get("exit_bid")
    yen = None
    if (not miss) and _finite(exit_bid) and _finite(fill_px):
        yen = float(compute_pnl_yen_100(float(fill_px), float(exit_bid), side="long"))
    body = {
        "reason": reason,
        "exit_t": pack.get("exit_t"),
        "exit_bid": float(exit_bid) if _finite(exit_bid) else None,
        "pnl_yen_100": yen,
        "miss": bool(miss),
        "freshness_evidence": pack.get("freshness_evidence"),
        "latency_sec": None,
    }
    if extra:
        body.update(extra)
    return body


def eval_candidate(
    tf1: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    control_exit: dict[str, Any],
    u_be_t: Optional[float],
    u_be_reached: Optional[bool],
    leak: dict[str, Any],
) -> dict[str, Any]:
    ctrl = dict(control_exit) if control_exit else None
    out: dict[str, Any] = {
        "state_end": STATE_UNPROVEN,
        "be_reached": False,
        "be_t": None,
        "be_bid": None,
        "be_yen": None,
        "be_flag_match": True,
        "be_time_abs_diff": None,
        "swing_floor": None,
        "floor_formed": False,
        "first_floor_t": None,
        "first_floor_px": None,
        "ratchet_n": 0,
        "post_be_pivot_n": 0,
        "triggered": False,
        "would_trigger": False,
        "trigger_t": None,
        "trigger_close": None,
        "trigger_floor": None,
        "exit_miss_fallback_session_close": False,
        "pivot_backdate_n": 0,
        "treatment_exit": ctrl,
        "tf1_ok": True,
    }
    be = walk_break_even(
        board, meta, fill_t=float(fill_t), fill_px=float(fill_px), sess_end=float(sess_end), pullback_low=None, leak=leak
    )
    be_reached = bool(be.get("break_even_reached"))
    be_t = _f(be.get("be_t"))
    out["be_reached"] = be_reached
    out["be_t"] = be_t
    out["be_bid"] = be.get("be_bid")
    out["be_yen"] = be.get("be_yen")
    if u_be_reached is not None and bool(u_be_reached) != be_reached:
        out["be_flag_match"] = False
        leak["BE_REACHED_FLAG_MISMATCH_N"] = int(leak.get("BE_REACHED_FLAG_MISMATCH_N") or 0) + 1
    if u_be_t is not None and be_t is not None:
        diff = abs(float(u_be_t) - float(be_t))
        out["be_time_abs_diff"] = diff
        if diff > float(BE_PARITY_TOL_SEC):
            leak["BE_TIME_DRIFT_N"] = int(leak.get("BE_TIME_DRIFT_N") or 0) + 1
    if not be_reached or be_t is None:
        out["state_end"] = STATE_UNPROVEN
        out["treatment_exit"] = ctrl
        return out
    out["state_end"] = STATE_PROVEN_ARMED
    fin = tf1.get("finalize_t") if tf1 else None
    if fin is None or int(getattr(fin, "size", 0) or 0) <= 0:
        out["tf1_ok"] = False
        leak["TF1_MISSING_N"] = int(leak.get("TF1_MISSING_N") or 0) + 1
        out["treatment_exit"] = ctrl
        return out
    walked = walk_post_be_swing_floor(
        low=_seq(tf1.get("low")),
        close=_seq(tf1.get("close")),
        minute_epoch=_seq(tf1.get("minute_epoch")),
        finalize_t=_seq(fin),
        first_be_t=float(be_t),
        sess_end=float(sess_end),
    )
    out.update({k: walked[k] for k in walked})
    back_n = int(walked.get("pivot_backdate_n") or 0)
    if back_n:
        leak["PIVOT_BACKDATE_N"] = int(leak.get("PIVOT_BACKDATE_N") or 0) + back_n
    out["would_trigger"] = bool(walked.get("triggered"))
    trigger_t = _f(walked.get("trigger_t"))
    if trigger_t is not None and float(trigger_t) + PX_EPS < float(fill_t):
        leak["PRE_FILL_EXIT_N"] = int(leak.get("PRE_FILL_EXIT_N") or 0) + 1
        out["treatment_exit"] = ctrl
        out["triggered"] = False
        return out
    if trigger_t is not None and float(trigger_t) + PX_EPS < float(be_t):
        leak["PRE_BE_TRIGGER_N"] = int(leak.get("PRE_BE_TRIGGER_N") or 0) + 1
        out["treatment_exit"] = ctrl
        out["triggered"] = False
        return out
    if not bool(walked.get("triggered")) or trigger_t is None:
        out["treatment_exit"] = ctrl
        out["triggered"] = False
        return out
    pack = first_causal_bid_evidence(
        board, meta, event_t=float(trigger_t), fill_px=float(fill_px), sess_end=float(sess_end), leak=leak
    )
    extra = {
        "trigger_event_time": trigger_t,
        "trigger_name": TRIGGER_NAME,
        "swing_floor": walked.get("swing_floor"),
        "trigger_close": walked.get("trigger_close"),
        "bid_search_start_t": trigger_t,
        "be_t": be_t,
    }
    tx = _pack_exit(reason=CANDIDATE_EXIT_REASON, fill_px=float(fill_px), pack=pack, extra=extra)
    if pack.get("exit_t") is not None:
        tx["latency_sec"] = float(pack["exit_t"]) - float(trigger_t)
        if float(pack["exit_t"]) + PX_EPS < float(trigger_t):
            leak["FUTURE_QUOTE_CARRYBACK_N"] = int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0) + 1
        if float(pack["exit_t"]) + PX_EPS < float(fill_t):
            leak["PRE_FILL_EXIT_N"] = int(leak.get("PRE_FILL_EXIT_N") or 0) + 1
    if bool(tx.get("miss")):
        leak["CANDIDATE_EXIT_MISS_N"] = int(leak.get("CANDIDATE_EXIT_MISS_N") or 0) + 1
        out["treatment_exit"] = ctrl
        out["triggered"] = False
        out["exit_miss_fallback_session_close"] = True
        return out
    out["treatment_exit"] = tx
    out["triggered"] = True
    out["state_end"] = STATE_TRIGGERED
    return out


def attach_row(row: dict[str, Any], packed: dict[str, Any], leak: dict[str, Any]) -> dict[str, Any]:
    rec = dict(row)
    rec["candidate_path"] = None
    if not rec.get("actual_filled"):
        rec["treatment_exit"] = None
        return rec
    fill_t = _f(rec.get("fill_t") or rec.get("fill_time"))
    fill_px = _f(rec.get("fill_price"))
    ctrl = dict(rec.get("control_exit") or {})
    if fill_t is None or fill_px is None or not ctrl:
        leak["FILL_PACK_INCOMPLETE_N"] = int(leak.get("FILL_PACK_INCOMPLETE_N") or 0) + 1
        rec["candidate_path"] = {"tf1_ok": False, "state_end": STATE_UNPROVEN, "triggered": False}
        rec["treatment_exit"] = ctrl or None
        return rec
    rec["fill_t"] = float(fill_t)
    sym = _bare(rec.get("symbol"))
    tf1 = ((packed.get("tf_by_sym") or {}).get(sym) or {}).get("tf1") or {}
    board = (packed.get("views") or {}).get(sym) or {}
    meta = (packed.get("metas") or {}).get(sym) or {}
    u_be_t = _f(rec.get("first_break_even_time"))
    u_flag = rec.get("break_even_reached")
    u_be_reached = None if u_flag is None else bool(u_flag)
    if u_be_reached is None and u_be_t is not None:
        u_be_reached = True
    path = eval_candidate(
        tf1,
        board,
        meta,
        fill_t=float(fill_t),
        fill_px=float(fill_px),
        sess_end=float(packed["am_end"]),
        control_exit=ctrl,
        u_be_t=u_be_t,
        u_be_reached=u_be_reached,
        leak=leak,
    )
    rec["candidate_path"] = path
    rec["treatment_exit"] = path.get("treatment_exit")
    return rec


def _tid(row: dict[str, Any]) -> Optional[tuple[str, str, float]]:
    t0 = _f(row.get("t0") or row.get("signal_time"))
    if t0 is None:
        return None
    return str(row.get("date") or ""), _bare(row.get("symbol")), float(t0)


def incremental_causes(day: str, occ: dict[str, Any]) -> list[dict[str, Any]]:
    ctrl_ids = set()
    for t in list((occ.get("control") or {}).get("trades") or []):
        key = _tid(t)
        if key is not None:
            ctrl_ids.add(key)
    last_exit: Optional[dict[str, Any]] = None
    out: list[dict[str, Any]] = []
    treat = dict(occ.get("treatment") or {})
    treat_ids = set()
    for t in list(treat.get("trades") or []):
        key = _tid(t)
        if key is not None:
            treat_ids.add(key)
    incr = treat_ids - ctrl_ids
    admitted: set[tuple[str, str, float]] = set()
    for ev in list(treat.get("event_log") or []):
        kind = str(ev[1]) if len(ev) > 1 else ""
        if kind == "EXIT":
            last_exit = {
                "exit_t": ev[0],
                "release_symbol": ev[2] if len(ev) > 2 else None,
                "release_t0": ev[3] if len(ev) > 3 else None,
                "release_reason": ev[4] if len(ev) > 4 else None,
            }
            continue
        if kind != "ADMIT" or len(ev) < 4:
            continue
        key = (str(day), _bare(ev[2]), float(ev[3]))
        admitted.add(key)
        if key not in incr:
            continue
        cause = dict(last_exit or {})
        cause.update({"date": day, "symbol": _bare(ev[2]), "t0": float(ev[3]), "admit_t": ev[0]})
        out.append(cause)
    for key in sorted(incr):
        if key in admitted:
            continue
        out.append({"date": key[0], "symbol": key[1], "t0": key[2], "release_symbol": None, "note": "ADMIT_NOT_IN_LOG"})
    return out


def _arm_public(arm: dict[str, Any]) -> dict[str, Any]:
    body = dict(arm or {})
    body.pop("candidates", None)
    return body


def harvest_day(day: str, *, cohort: str, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE) or day == str(today):
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    ubody = load_u_day(day, cohort=cohort)
    if not ubody.get("ok") and not list(ubody.get("rows") or []):
        return {"ok": False, "blocker": f"u_cache_missing:{day}", "date": day}
    rows = [dict(r) for r in list(ubody.get("rows") or [])]
    leak = {
        "FUTURE_QUOTE_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "CANDIDATE_EXIT_MISS_N": 0,
        "FILL_PACK_INCOMPLETE_N": 0,
        "PRE_DECISION_EXIT_N": 0,
        "PRE_BAR_EXIT_N": 0,
        "PRE_FILL_EXIT_N": 0,
        "PRE_BE_TRIGGER_N": 0,
        "PIVOT_BACKDATE_N": 0,
        "BE_REACHED_FLAG_MISMATCH_N": 0,
        "BE_TIME_DRIFT_N": 0,
        "TF1_MISSING_N": 0,
    }
    filled = [r for r in rows if r.get("actual_filled")]
    symbols = {_bare(r.get("symbol")) for r in filled if _bare(r.get("symbol"))}
    if filled:
        capture = find_capture_dir(day)
        if capture is None:
            return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
        print(f"{cohort} {day} swing-floor symbols={len(symbols)} filled={len(filled)} signals={len(rows)}", flush=True)
        packed = stream_day(day, capture, symbols, leak)
        rows = [attach_row(r, packed, leak) for r in rows]
        del packed
        gc.collect()
    occ = replay_causal_day(rows)
    if not occ.get("control_sot_ok"):
        return {"ok": False, "blocker": f"occupancy_sot_fail:{day}", "date": day, "leak": leak}
    causes = incremental_causes(day, occ)
    slim = []
    for r in rows:
        cp = dict(r.get("candidate_path") or {})
        slim.append(
            {
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "t0": r.get("t0"),
                "fill_role": r.get("fill_role"),
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
                "actual_filled": r.get("actual_filled"),
                "executable_signal": r.get("executable_signal"),
                "path_type": r.get("path_type"),
                "control_exit": r.get("control_exit"),
                "treatment_exit": r.get("treatment_exit"),
                "candidate_path": {
                    "state_end": cp.get("state_end"),
                    "be_reached": cp.get("be_reached"),
                    "be_t": cp.get("be_t"),
                    "be_flag_match": cp.get("be_flag_match"),
                    "be_time_abs_diff": cp.get("be_time_abs_diff"),
                    "swing_floor": cp.get("swing_floor"),
                    "floor_formed": cp.get("floor_formed"),
                    "first_floor_t": cp.get("first_floor_t"),
                    "ratchet_n": cp.get("ratchet_n"),
                    "post_be_pivot_n": cp.get("post_be_pivot_n"),
                    "triggered": cp.get("triggered"),
                    "would_trigger": cp.get("would_trigger"),
                    "trigger_t": cp.get("trigger_t"),
                    "trigger_close": cp.get("trigger_close"),
                    "trigger_floor": cp.get("trigger_floor"),
                    "exit_miss_fallback_session_close": cp.get("exit_miss_fallback_session_close"),
                    "pivot_backdate_n": cp.get("pivot_backdate_n"),
                    "tf1_ok": cp.get("tf1_ok"),
                }
                if cp
                else None,
            }
        )
    body = {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "spec_sha": spec_sha,
        "rows": slim,
        "leak": leak,
        "incremental_causes": causes,
        "control": _arm_public(dict(occ.get("control") or {})),
        "treatment": _arm_public(dict(occ.get("treatment") or {})),
        "control_sot_ok": occ.get("control_sot_ok"),
        "treatment_sot_ok": occ.get("treatment_sot_ok"),
        "leftover_ok": occ.get("leftover_ok"),
        "pre_divergence_ok": occ.get("pre_divergence_ok"),
    }
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return body
