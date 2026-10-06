"""Harvest admitted vs CAP-only blocked with arrival rank. Frozen caches only."""
from __future__ import annotations

from typing import Any, Optional

from research.anchor_vs_event_driven.run_comparison import _bare
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_harvest import PATH_CACHE as PULLBACK_CACHE
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_spec import spec_sha256_anchored
from research.simple_tech_redesign.isolation import TODAY
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_spec import FIRST5_N
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import (
    _f,
    _tid,
    _tid_s,
    extract_cap_inventory,
    load_frozen_day,
    session_clock,
)
from research.simple_tech_redesign.v22_harvest import V22_CACHE
from research.simple_tech_redesign.v22_spec import spec_sha256_v22
from small_paper.v1r_primary_runtime import POSITION_CAP

SHARES = 100.0


def occupancy_at_signals(day: str, event_log: list[Any]) -> dict[tuple[str, str, float], dict[str, Any]]:
    pending = 0
    open_n = 0
    out: dict[tuple[str, str, float], dict[str, Any]] = {}
    cap = int(POSITION_CAP)
    for ev in list(event_log or []):
        if len(ev) < 4:
            continue
        kind = str(ev[1])
        t0 = _f(ev[3])
        if t0 is None:
            continue
        key = (str(day), _bare(ev[2]), float(t0))
        exposure = int(pending + open_n)
        if kind in {"ADMIT", "CAP", "SAME_SYMBOL"}:
            out[key] = {
                "active_positions": exposure,
                "open_positions": int(open_n),
                "pending": int(pending),
                "free_slots": max(0, cap - exposure),
                "CAP_state": "FULL" if exposure >= cap else "OPEN",
                "event_kind": kind,
            }
        if kind == "ADMIT":
            pending += 1
        elif kind == "FILL":
            pending = max(0, pending - 1)
            open_n += 1
        elif kind in {"EXPIRE", "FILL_NO_EXIT"}:
            pending = max(0, pending - 1)
        elif kind == "EXIT":
            open_n = max(0, open_n - 1)
    return out


def _v22_t3(row: dict[str, Any]) -> dict[str, Any]:
    st = row.get("state_t0")
    tf1 = dict((st or {}).get("tf1") or {}) if isinstance(st, dict) else {}
    return {
        "t3_ema9": _f(tf1.get("ema9")),
        "t3_ema21": _f(tf1.get("ema21")),
        "t3_rci9": _f(tf1.get("rci9")),
        "t3_bb_lower": _f(tf1.get("bb_lower")),
        "t3_close": _f(tf1.get("close")),
        "t3_ema_gap_bps": _f(tf1.get("ema_gap_bps")),
        "t3_close_ema9_bps": _f(tf1.get("close_ema9_bps")),
        "t3_trend_flag": tf1.get("trend"),
        "t3_pullback_flag": tf1.get("pullback"),
        "t3_rci_cross_flag": tf1.get("rci_cross"),
        "mfe_ask_bid_600": _f(row.get("mfe_ask_bid_600")),
        "mae_ask_bid_600": _f(row.get("mae_ask_bid_600")),
        "v22_joined": bool(tf1),
    }


def _p2_from_path(cp: dict[str, Any], ref: dict[str, Any], overlap: dict[str, Any]) -> dict[str, Any]:
    bars = list(ref.get("bars") or [])
    setup_low = _f(ref.get("setup_low"))
    setup_high = _f(ref.get("setup_high"))
    if setup_low is None:
        setup_low = _f(cp.get("setup_low"))
    if setup_high is None:
        setup_high = _f(cp.get("setup_high"))
    out: dict[str, Any] = {
        "p2_setup_low": setup_low,
        "p2_setup_high": setup_high,
        "p2_signal_bar_low": _f(ref.get("signal_bar_low")),
        "p2_signal_bar_close": _f(ref.get("signal_bar_close")),
        "p2_bb_lower_at_signal": _f(overlap.get("bb_lower_at_signal")),
        "p2_reference_ok": bool(ref.get("ok")) if ref else bool(cp.get("reference_ok")),
        "t3_rci9_prev": None,
        "t3_ema21_slope_input": None,
        "t3_rci9_prev_status": "MISSING_NOT_IN_FROZEN_CACHE",
        "t3_ema21_slope_input_status": "MISSING_NOT_IN_FROZEN_CACHE",
    }
    for i, bar in enumerate(bars[:3]):
        out[f"p2_bar{i}_low"] = _f((bar or {}).get("low"))
        out[f"p2_bar{i}_close"] = _f((bar or {}).get("close"))
    return out


def load_v22_index(day: str) -> dict[tuple[str, str, float], dict[str, Any]]:
    path = V22_CACHE / f"day_{day}.json"
    body = load_day_cache(path, spec_sha256_v22())
    out: dict[tuple[str, str, float], dict[str, Any]] = {}
    if not body:
        return out
    for r in list(body.get("rows") or []):
        key = _tid(r)
        if key is None:
            continue
        out[key] = _v22_t3(r)
    return out


def load_pullback_index(day: str, *, cohort: str) -> dict[tuple[str, str, float], dict[str, Any]]:
    tag = "development" if cohort == "DEVELOPMENT" else "forward"
    path = PULLBACK_CACHE / f"day_{tag}_{day}.json"
    body = load_day_cache(path, spec_sha256_anchored())
    out: dict[tuple[str, str, float], dict[str, Any]] = {}
    if not body:
        return out
    for r in list(body.get("rows") or []):
        key = _tid(r)
        if key is None:
            continue
        sp = dict(r.get("structure_path") or {})
        out[key] = {
            "reference": dict(sp.get("reference") or {}),
            "closed_overlap": dict(sp.get("closed_overlap") or {}),
        }
    return out


def _pnl_bps(pnl: Optional[float], fill_px: Optional[float]) -> Optional[float]:
    if pnl is None or fill_px is None or float(fill_px) <= 0:
        return None
    notional = float(fill_px) * SHARES
    if notional <= 0:
        return None
    return float(pnl) / notional * 10000.0


def harvest_day(day: str, *, cohort: str, today: str = TODAY) -> dict[str, Any]:
    body = load_frozen_day(day, cohort=cohort, today=today)
    if not body.get("ok"):
        return body
    rows = list(body.get("rows") or [])
    ctrl = dict(body.get("control") or {})
    cap = extract_cap_inventory(day, body)
    occ_state = occupancy_at_signals(day, list(ctrl.get("event_log") or []))
    v22 = load_v22_index(day)
    pb = load_pullback_index(day, cohort=cohort)
    ctrl_ids = {k for k in (_tid(t) for t in list(ctrl.get("trades") or [])) if k is not None}
    ctrl_by = {k: t for t in list(ctrl.get("trades") or []) if (k := _tid(t)) is not None}
    cap_only_ids = {k for k in (_tid(r) for r in list(cap.get("cap_only") or [])) if k is not None}
    hyp_ids = {
        k
        for r in list(cap.get("cap_only") or [])
        if bool(r.get("actual_filled")) and _f(r.get("session_close_pnl")) is not None
        for k in [_tid(r)]
        if k is not None
    }
    executable = [r for r in rows if bool(r.get("executable_signal"))]
    executable.sort(key=lambda r: (float(_f(r.get("t0")) or 0.0), _bare(r.get("symbol"))))
    n_exec = len(executable)
    packed = []
    for rank_i, r in enumerate(executable, start=1):
        key = _tid(r)
        if key is None:
            continue
        clock = session_clock(day, float(key[2]))
        cp = dict(r.get("candidate_path") or {})
        pb_hit = dict(pb.get(key) or {})
        t3 = dict(v22.get(key) or {})
        p2 = _p2_from_path(cp, dict(pb_hit.get("reference") or {}), dict(pb_hit.get("closed_overlap") or {}))
        occ = dict(occ_state.get(key) or {})
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
        rec = {
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
            "mfe_bps": t3.get("mfe_ask_bid_600"),
            "mae_bps": t3.get("mae_ask_bid_600"),
            "mfe_mae_source": "V22_ASK_BID_600" if t3.get("mfe_ask_bid_600") is not None else "NOT_IN_FROZEN_CACHE",
            **clock,
            **occ,
            **{
                k: t3.get(k)
                for k in (
                    "t3_ema9",
                    "t3_ema21",
                    "t3_rci9",
                    "t3_bb_lower",
                    "t3_close",
                    "t3_ema_gap_bps",
                    "t3_close_ema9_bps",
                    "t3_trend_flag",
                    "t3_pullback_flag",
                    "t3_rci_cross_flag",
                    "v22_joined",
                )
            },
            **p2,
        }
        packed.append(rec)
    return {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "leftover_ok": body.get("leftover_ok"),
        "control_sot_ok": body.get("control_sot_ok"),
        "control": {
            "trades": list(ctrl.get("trades") or []),
            "fill_n": ctrl.get("fill_n"),
            "cap_blocked": ctrl.get("cap_blocked"),
        },
        "cap_inventory": cap,
        "candidates": packed,
        "executable_n": n_exec,
        "admitted_n": sum(1 for c in packed if c.get("control_admitted")),
        "hyp_n": sum(1 for c in packed if c.get("hypothetical_fill")),
        "v22_join_n": sum(1 for c in packed if c.get("v22_joined")),
    }
