"""Harvest prospective Control admitted vs CAP-only blocked. Sealed AM only. No cascade."""
from __future__ import annotations

import json
from typing import Any, Optional

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare
from research.simple_tech_entry_family.harvest import load_day_cache, process_day, sealed_day_caps
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_redesign.branch_u_bb_harvest import replay_branch_u_day
from research.simple_tech_redesign.branch_u_causal_harvest import occupancy_replay, sot_parity
from research.simple_tech_redesign.branch_u_holdout_harvest import _b1_from_opps, inspect_capture_day
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_spec import (
    FORBIDDEN_INPUT_DAYS,
    WINDOW_DAYS,
)
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_harvest import occupancy_at_signals
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import (
    _f,
    _tid,
    _tid_s,
    extract_cap_inventory,
    session_clock,
)
from small_paper.v1r_primary_runtime import POSITION_CAP

DAY_CACHE = RESEARCH_CACHE / "precap_marginal_quality_prospective_v1"
SHARES = 100.0
FIRST5_N = int(POSITION_CAP)


def _bps(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or float(den) <= 0:
        return None
    return (float(num) / float(den) - 1.0) * 10000.0


def _pnl_bps(pnl: Optional[float], fill_px: Optional[float]) -> Optional[float]:
    if pnl is None or fill_px is None or float(fill_px) <= 0:
        return None
    notional = float(fill_px) * SHARES
    if notional <= 0:
        return None
    return float(pnl) / notional * 10000.0


def _t3_from_opp(opp: dict[str, Any]) -> dict[str, Any]:
    ema9 = _f(opp.get("ema9"))
    ema21 = _f(opp.get("ema21"))
    close = _f(opp.get("close"))
    lag3 = _f(opp.get("ema21_lag3"))
    rci = _f(opp.get("rci9"))
    rci_prev = _f(opp.get("rci9_prev"))
    bb = _f(opp.get("bb_lower"))
    return {
        "t3_ema9": ema9,
        "t3_ema21": ema21,
        "t3_ema21_lag3": lag3,
        "t3_ema21_slope_input": lag3,
        "t3_rci9": rci,
        "t3_rci9_prev": rci_prev,
        "t3_bb_lower": bb,
        "t3_close": close,
        "t3_ema_gap_bps": _bps(ema9, ema21),
        "t3_close_ema9_bps": _bps(close, ema9),
        "t3_trend_flag": opp.get("s1"),
        "t3_pullback_flag": opp.get("s2"),
        "t3_rci_cross_flag": opp.get("s3"),
        "t3_source": "PROCESS_DAY_OPP",
        "p2_setup_low": None,
        "p2_setup_high": None,
        "p2_bar0_low": _f(opp.get("low")),
        "p2_bar0_close": close,
        "p2_bar1_low": None,
        "p2_bar1_close": None,
        "p2_bar2_low": None,
        "p2_bar2_close": None,
        "p2_setup_low_status": "MISSING_TF1_NOT_RETAINED",
        "p2_setup_high_status": "MISSING_TF1_NOT_RETAINED",
        "t3_rci9_prev_status": None if rci_prev is not None else "MISSING_NOT_IN_OPP",
        "t3_ema21_slope_input_status": None if lag3 is not None else "MISSING_NOT_IN_OPP",
    }


def discover_window(*, today: str = TODAY) -> dict[str, Any]:
    planned = []
    usable = []
    excluded = []
    for day in list(WINDOW_DAYS):
        rec: dict[str, Any] = {
            "date": day,
            "complete": False,
            "ok": False,
            "capture_path": "",
            "universe_symbols": [],
            "observation_label": "MARGINAL_QUALITY_OBSERVATION_DAY1" if day == "20260907" else None,
        }
        if day in FORBIDDEN_INPUT_DAYS:
            rec["reason"] = "FORBIDDEN_INPUT"
            excluded.append(rec)
            planned.append(rec)
            continue
        if day == str(today):
            rec["reason"] = "ACTIVE_OR_INCOMPLETE_TODAY"
            excluded.append(rec)
            planned.append(rec)
            continue
        if day > str(today):
            rec["reason"] = "FUTURE_NOT_YET"
            excluded.append(rec)
            planned.append(rec)
            continue
        insp = inspect_capture_day(day, today=str(today))
        rec.update({k: v for k, v in insp.items() if k not in rec or rec.get(k) in (None, "", False)})
        rec["date"] = day
        rec["complete"] = bool(insp.get("complete"))
        rec["capture_path"] = str(insp.get("capture_path") or "")
        rec["reason"] = str(insp.get("reason") or rec.get("reason") or "")
        if not rec["complete"]:
            excluded.append(rec)
            planned.append(rec)
            continue
        caps = sealed_day_caps([day], str(today))
        cap = dict(caps[0] if caps else {})
        if not cap.get("ok"):
            rec["reason"] = "UNIVERSE_INCOMPLETE"
            rec["complete"] = False
            excluded.append(rec)
            planned.append(rec)
            continue
        rec["ok"] = True
        rec["reason"] = "COMPLETE_SEALED_AM"
        rec["capture_path"] = str(cap.get("capture_path") or rec.get("capture_path") or "")
        rec["universe_symbols"] = list(cap.get("universe_symbols") or [])
        rec["universe_n"] = int(cap.get("universe_n") or 0)
        rec["universe_source"] = cap.get("universe_source")
        usable.append(rec)
        planned.append(rec)
    return {
        "window_days": list(WINDOW_DAYS),
        "today": str(today),
        "usable": usable,
        "excluded": excluded,
        "planned": planned,
        "completed_days": [str(r["date"]) for r in usable],
    }


def _slim_exec_row(r: dict[str, Any]) -> dict[str, Any]:
    cx = dict(r.get("control_exit") or {})
    return {
        "date": r.get("date"),
        "symbol": _bare(r.get("symbol")),
        "t0": r.get("t0"),
        "fill_role": r.get("fill_role"),
        "path_type": r.get("path_type"),
        "executable_signal": bool(r.get("executable_signal")),
        "actual_filled": bool(r.get("actual_filled")),
        "fill_t": r.get("fill_t"),
        "fill_price": r.get("fill_price"),
        "control_exit": {
            "reason": cx.get("reason"),
            "exit_t": cx.get("exit_t"),
            "exit_bid": cx.get("exit_bid"),
            "pnl_yen_100": cx.get("pnl_yen_100"),
            "miss": cx.get("miss"),
        }
        if cx
        else None,
    }


def harvest_day(cap: dict[str, Any], *, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    day = str(cap.get("date") or "")
    if (not day) or day in FORBIDDEN_INPUT_DAYS or day == str(today) or day > str(today):
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY_OR_FUTURE", "date": day, "skip": True}
    if not cap.get("ok"):
        return {"ok": False, "blocker": str(cap.get("reason") or "NOT_SEALED"), "date": day, "skip": True}
    path = DAY_CACHE / f"day_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    capture_path = str(cap.get("capture_path") or "")
    universe = list(cap.get("universe_symbols") or [])
    if (not capture_path) or (not universe):
        return {"ok": False, "blocker": "CAPTURE_OR_UNIVERSE_MISSING", "date": day}
    if str(today) in capture_path.replace("\\", "/"):
        return {"ok": False, "blocker": "ACTIVE_CAPTURE_PATH", "date": day}
    b1_body = process_day(
        {
            "date": day,
            "capture_path": capture_path,
            "universe": universe,
            "spec_sha": v1_spec_sha256(),
        }
    )
    if not b1_body.get("ok"):
        return {"ok": False, "blocker": f"process_day:{b1_body.get('blocker')}", "date": day}
    opps = list(b1_body.get("opps") or [])
    signals = _b1_from_opps(opps)
    t3_by = {}
    for opp in opps:
        if not opp.get("s3"):
            continue
        key = _tid(opp, date=day)
        if key is None:
            continue
        t3_by[key] = _t3_from_opp(opp)
    u_body = replay_branch_u_day(
        {"date": day, "capture_path": capture_path, "signals": signals, "spec_sha": spec_sha}
    )
    if not u_body.get("ok"):
        return {"ok": False, "blocker": f"replay:{u_body.get('blocker')}", "date": day}
    rows = [_slim_exec_row(r) for r in list(u_body.get("rows") or [])]
    ctrl = occupancy_replay(rows, exit_key="control_exit")
    ctrl_sot = sot_parity(rows, exit_key="control_exit", ours=ctrl)
    leftover_ok = int(ctrl.get("open_leftover_n") or 0) == 0 and int(ctrl.get("pending_leftover_n") or 0) == 0
    if not ctrl_sot or not leftover_ok:
        return {
            "ok": False,
            "blocker": f"occupancy_sot_or_leftover day={day} sot={ctrl_sot} leftover={leftover_ok}",
            "date": day,
        }
    body = {
        "ok": True,
        "date": day,
        "rows": rows,
        "control": {
            "trades": list(ctrl.get("trades") or []),
            "event_log": list(ctrl.get("event_log") or []),
            "fill_n": ctrl.get("fill_n"),
            "cap_blocked": ctrl.get("cap_blocked"),
            "same_symbol_blocked": ctrl.get("same_symbol_blocked"),
            "accepted_entry_n": ctrl.get("accepted_entry_n"),
        },
        "control_sot_ok": True,
        "leftover_ok": True,
    }
    cap_inv = extract_cap_inventory(day, body)
    occ_state = occupancy_at_signals(day, list(ctrl.get("event_log") or []))
    ctrl_ids = {k for k in (_tid(t) for t in list(ctrl.get("trades") or [])) if k is not None}
    ctrl_by = {k: t for t in list(ctrl.get("trades") or []) if (k := _tid(t)) is not None}
    cap_only_ids = {k for k in (_tid(r) for r in list(cap_inv.get("cap_only") or [])) if k is not None}
    hyp_ids = {
        k
        for r in list(cap_inv.get("cap_only") or [])
        if bool(r.get("actual_filled")) and _f(r.get("session_close_pnl")) is not None
        for k in [_tid(r)]
        if k is not None
    }
    executable = [r for r in rows if bool(r.get("executable_signal"))]
    executable.sort(key=lambda r: (float(_f(r.get("t0")) or 0.0), _bare(r.get("symbol"))))
    n_exec = len(executable)
    packed = []
    for rank_i, r in enumerate(executable, start=1):
        key = _tid(r, date=day)
        if key is None:
            continue
        clock = session_clock(day, float(key[2]))
        occ = dict(occ_state.get(key) or {})
        t3 = dict(t3_by.get(key) or {"t3_source": "MISSING_OPP_JOIN"})
        fill_px = _f(r.get("fill_price"))
        admitted = key in ctrl_ids
        cap_only = key in cap_only_ids
        hyp = key in hyp_ids
        pnl = None
        if admitted:
            pnl = _f((ctrl_by.get(key) or {}).get("pnl_yen_100"))
        elif hyp:
            pnl = _f(dict(r.get("control_exit") or {}).get("pnl_yen_100"))
        third = "LAST"
        if n_exec > 0:
            if rank_i <= n_exec / 3.0:
                third = "FIRST"
            elif rank_i <= 2.0 * n_exec / 3.0:
                third = "MIDDLE"
        packed.append(
            {
                "date": day,
                "symbol": key[1],
                "t0": key[2],
                "trade_id": _tid_s(key),
                "fill_role": r.get("fill_role") or (ctrl_by.get(key) or {}).get("fill_role"),
                "path_type": r.get("path_type"),
                "executable_signal": True,
                "actual_filled": bool(r.get("actual_filled")),
                "fill_price": fill_px,
                "fill_t": r.get("fill_t"),
                "candidate_arrival_rank": rank_i,
                "executable_n_day": n_exec,
                "arrival_third": third,
                "first5": bool(rank_i <= int(FIRST5_N)),
                "control_admitted": admitted,
                "cap_only_blocked": cap_only,
                "hypothetical_fill": hyp,
                "session_close_pnl": pnl,
                "entry_notional": (float(fill_px) * SHARES) if fill_px is not None else None,
                "session_close_pnl_bps": _pnl_bps(pnl, fill_px),
                **clock,
                **occ,
                **t3,
            }
        )
    out = {
        "ok": True,
        "date": day,
        "spec_sha": spec_sha,
        "control_sot_ok": True,
        "leftover_ok": True,
        "cap_inventory": {
            "cap_only_n": len(list(cap_inv.get("cap_only") or [])),
            "multi_reason_n": len(list(cap_inv.get("multi_reason") or [])),
            "same_symbol_n": len(list(cap_inv.get("same_symbol") or [])),
            "cap_event_n": cap_inv.get("cap_event_n"),
        },
        "control": {
            "trades": list(ctrl.get("trades") or []),
            "fill_n": ctrl.get("fill_n"),
            "cap_blocked": ctrl.get("cap_blocked"),
        },
        "candidates": packed,
        "executable_n": n_exec,
        "admitted_n": sum(1 for c in packed if c.get("control_admitted")),
        "hyp_n": sum(1 for c in packed if c.get("hypothetical_fill")),
        "t3_join_n": sum(1 for c in packed if c.get("t3_source") == "PROCESS_DAY_OPP"),
        "b1_signal_n": len(signals),
        "events_n": b1_body.get("events_n"),
        "u_events_n": u_body.get("events_n"),
    }
    DAY_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(out), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return out


def harvest_window(discovery: dict[str, Any], *, spec_sha: str, today: str = TODAY) -> list[dict[str, Any]]:
    bodies = []
    for rec in list(discovery.get("usable") or []):
        body = harvest_day(rec, spec_sha=spec_sha, today=today)
        bodies.append(body)
    return bodies
