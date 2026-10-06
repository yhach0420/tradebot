"""Harvest frozen pre-upside floor-break exits on the full B1 candidate stream."""
from __future__ import annotations

import gc
import json
from typing import Any, Optional

from replay.pnl_yen import compute_pnl_yen_100
from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_redesign.branch_u_bb_harvest import first_causal_bid_evidence
from research.simple_tech_redesign.branch_u_causal_harvest import replay_causal_day
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_spec import CANDIDATE_EXIT_REASON, TRIGGER_NAME
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_harvest import recover_p2_reference
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v24_harvest import _finite

PATH_CACHE = RESEARCH_CACHE / "entry_anchored_floor_break_candidate_v1"
PX_EPS = 1e-9
STATE_ARMED = "STRUCTURAL_EXIT_ARMED"
STATE_LOCKED = "UPSIDE_BREAK_LOCKED"
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


def _seq(floor_t: Optional[float], upside_t: Optional[float]) -> str:
    if floor_t is not None and upside_t is not None and abs(float(floor_t) - float(upside_t)) <= PX_EPS:
        return "C_SAME_COMPLETED_BAR"
    if floor_t is not None and (upside_t is None or float(floor_t) < float(upside_t) - PX_EPS):
        return "B_FLOOR_BREAK_FIRST"
    if upside_t is not None and (floor_t is None or float(upside_t) < float(floor_t) - PX_EPS):
        return "A_UPSIDE_BREAK_FIRST"
    return "D_NEITHER_BEFORE_SESSION_CLOSE"


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
    t0: float,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    control_exit: dict[str, Any],
    leak: dict[str, Any],
) -> dict[str, Any]:
    ref = recover_p2_reference(tf1, float(t0))
    ctrl = dict(control_exit) if control_exit else None
    out: dict[str, Any] = {
        "reference_ok": bool(ref.get("ok")),
        "reference_blocker": ref.get("blocker"),
        "setup_low": ref.get("setup_low"),
        "setup_high": ref.get("setup_high"),
        "signal_bar_i": ref.get("signal_bar_i"),
        "state_end": STATE_ARMED,
        "prefill_upside": False,
        "prefill_floor": False,
        "floor_t": None,
        "upside_t": None,
        "trigger_t": None,
        "rca_sequence": "D_NEITHER_BEFORE_SESSION_CLOSE",
        "postfill_sequence": "D_NEITHER_BEFORE_SESSION_CLOSE",
        "triggered": False,
        "locked": False,
        "would_trigger": False,
        "exit_miss_fallback_session_close": False,
        "treatment_exit": ctrl,
    }
    if not ref.get("ok"):
        leak["REFERENCE_FAIL_N"] = int(leak.get("REFERENCE_FAIL_N") or 0) + 1
        return out
    setup_low = float(ref["setup_low"])
    setup_high = float(ref["setup_high"])
    i_sig = int(ref["signal_bar_i"])
    fin = tf1.get("finalize_t")
    close = tf1.get("close")
    if fin is None or close is None:
        out["reference_ok"] = False
        out["reference_blocker"] = "TF1_MISSING"
        leak["REFERENCE_FAIL_N"] = int(leak.get("REFERENCE_FAIL_N") or 0) + 1
        return out
    state = STATE_ARMED
    floor_any = None
    upside_any = None
    pf_floor = None
    pf_up = None
    trigger_t = None
    for i in range(i_sig + 1, int(fin.size)):
        ft = float(fin[i]) if _finite(fin[i]) else None
        cl = float(close[i]) if _finite(close[i]) else None
        if ft is None or cl is None:
            continue
        if ft > float(sess_end) + PX_EPS:
            break
        before_fill = ft + PX_EPS < float(fill_t)
        up = cl > setup_high + PX_EPS
        dn = cl < setup_low - PX_EPS
        if up and upside_any is None:
            upside_any = ft
        if dn and floor_any is None:
            floor_any = ft
        if (not before_fill) and up and pf_up is None:
            pf_up = ft
        if (not before_fill) and dn and pf_floor is None:
            pf_floor = ft
        if before_fill and up:
            out["prefill_upside"] = True
        if before_fill and dn:
            out["prefill_floor"] = True
        if state == STATE_ARMED:
            if up:
                state = STATE_LOCKED
            elif dn:
                state = STATE_TRIGGERED
                trigger_t = ft
    out["floor_t"] = floor_any
    out["upside_t"] = upside_any
    out["trigger_t"] = trigger_t
    out["state_end"] = state
    out["locked"] = state == STATE_LOCKED
    out["would_trigger"] = state == STATE_TRIGGERED and trigger_t is not None
    out["rca_sequence"] = _seq(floor_any, upside_any)
    out["postfill_sequence"] = _seq(pf_floor, pf_up)

    if out["would_trigger"] and trigger_t is not None:
        bid_t = float(fill_t) if float(trigger_t) + PX_EPS < float(fill_t) else float(trigger_t)
        if bid_t + PX_EPS < float(fill_t):
            bid_t = float(fill_t)
        pack = first_causal_bid_evidence(
            board, meta, event_t=float(bid_t), fill_px=float(fill_px), sess_end=float(sess_end), leak=leak
        )
        extra = {
            "trigger_event_time": trigger_t,
            "trigger_name": TRIGGER_NAME,
            "prefill_floor": bool(out["prefill_floor"]),
            "prefill_upside": bool(out["prefill_upside"]),
            "bid_search_start_t": bid_t,
        }
        tx = _pack_exit(reason=CANDIDATE_EXIT_REASON, fill_px=float(fill_px), pack=pack, extra=extra)
        if pack.get("exit_t") is not None:
            tx["latency_sec"] = float(pack["exit_t"]) - float(bid_t)
            if float(pack["exit_t"]) + PX_EPS < float(bid_t):
                leak["FUTURE_QUOTE_CARRYBACK_N"] = int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0) + 1
            if float(pack["exit_t"]) + PX_EPS < float(fill_t):
                leak["PRE_FILL_EXIT_N"] = int(leak.get("PRE_FILL_EXIT_N") or 0) + 1
        if bool(tx.get("miss")):
            leak["CANDIDATE_EXIT_MISS_N"] = int(leak.get("CANDIDATE_EXIT_MISS_N") or 0) + 1
            out["treatment_exit"] = ctrl
            out["triggered"] = False
            out["exit_miss_fallback_session_close"] = True
        else:
            out["treatment_exit"] = tx
            out["triggered"] = True
    else:
        out["treatment_exit"] = ctrl
        out["triggered"] = False
    return out


def attach_row(row: dict[str, Any], packed: dict[str, Any], leak: dict[str, Any]) -> dict[str, Any]:
    rec = dict(row)
    rec["candidate_path"] = None
    if not rec.get("actual_filled"):
        rec["treatment_exit"] = None
        return rec
    t0 = _f(rec.get("t0"))
    fill_t = _f(rec.get("fill_t"))
    fill_px = _f(rec.get("fill_price"))
    ctrl = dict(rec.get("control_exit") or {})
    if t0 is None or fill_t is None or fill_px is None or not ctrl:
        leak["FILL_PACK_INCOMPLETE_N"] = int(leak.get("FILL_PACK_INCOMPLETE_N") or 0) + 1
        rec["candidate_path"] = {"reference_ok": False, "reference_blocker": "INCOMPLETE_FILL"}
        rec["treatment_exit"] = ctrl or None
        return rec
    sym = _bare(rec.get("symbol"))
    tf1 = ((packed.get("tf_by_sym") or {}).get(sym) or {}).get("tf1") or {}
    board = (packed.get("views") or {}).get(sym) or {}
    meta = (packed.get("metas") or {}).get(sym) or {}
    path = eval_candidate(
        tf1,
        board,
        meta,
        t0=float(t0),
        fill_t=float(fill_t),
        fill_px=float(fill_px),
        sess_end=float(packed["am_end"]),
        control_exit=ctrl,
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
    if str(day) >= "20260903" or day == str(today):
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
        "REFERENCE_FAIL_N": 0,
        "CANDIDATE_EXIT_MISS_N": 0,
        "FILL_PACK_INCOMPLETE_N": 0,
        "PRE_DECISION_EXIT_N": 0,
        "PRE_BAR_EXIT_N": 0,
        "PRE_FILL_EXIT_N": 0,
    }
    filled = [r for r in rows if r.get("actual_filled")]
    symbols = {_bare(r.get("symbol")) for r in filled if _bare(r.get("symbol"))}
    if filled:
        capture = find_capture_dir(day)
        if capture is None:
            return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
        print(f"{cohort} {day} floor-break-candidate symbols={len(symbols)} filled={len(filled)} signals={len(rows)}", flush=True)
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
                    "reference_ok": cp.get("reference_ok"),
                    "reference_blocker": cp.get("reference_blocker"),
                    "setup_low": cp.get("setup_low"),
                    "setup_high": cp.get("setup_high"),
                    "prefill_upside": cp.get("prefill_upside"),
                    "prefill_floor": cp.get("prefill_floor"),
                    "floor_t": cp.get("floor_t"),
                    "upside_t": cp.get("upside_t"),
                    "rca_sequence": cp.get("rca_sequence"),
                    "postfill_sequence": cp.get("postfill_sequence"),
                    "triggered": cp.get("triggered"),
                    "would_trigger": cp.get("would_trigger"),
                    "locked": cp.get("locked"),
                    "state_end": cp.get("state_end"),
                    "trigger_t": cp.get("trigger_t"),
                    "exit_miss_fallback_session_close": cp.get("exit_miss_fallback_session_close"),
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
