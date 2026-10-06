"""Harvest frozen P2 3-bar SETUP_LOW/HIGH and post-signal completed-bar breaks."""
from __future__ import annotations

import gc
import json
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_entry_family import PULLBACK_LOOKBACK
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_entry_family.stages import pullback_setup, reversal_rci, trend_up
from research.simple_tech_redesign.branch_u_bb_harvest import first_causal_bid_evidence
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_spec import SAME_BAR_SEC
from research.simple_tech_redesign.exit_lifecycle_harvest import _last_i_at_or_before
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v24_harvest import _finite
from research.simple_tech_redesign.v27_harvest import primitive_known

PATH_CACHE = RESEARCH_CACHE / "entry_anchored_pullback_structure_exit_rca"
LB = int(PULLBACK_LOOKBACK)
PX_EPS = 1e-9


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


def _arr_at(ind: dict[str, np.ndarray], key: str, i: int) -> Optional[float]:
    arr = ind.get(key)
    if arr is None or i < 0 or i >= int(arr.size) or not _finite(arr[i]):
        return None
    return float(arr[i])


def recover_p2_reference(tf1: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "blocker": None,
        "signal_bar_i": None,
        "signal_bar_finalize_t": None,
        "bar_indices": [],
        "bars": [],
        "setup_low": None,
        "setup_high": None,
        "signal_bar_low": None,
        "signal_bar_high": None,
        "signal_bar_close": None,
        "p1_trend_up": None,
        "p2_pullback_setup": None,
        "p3_reversal_rci_at_signal_only": None,
        "max_reference_finalize_t": None,
        "reference_le_signal": None,
        "setup_low_is_min": None,
        "setup_high_is_max": None,
    }
    i_sig = _last_i_at_or_before(tf1, float(t0))
    if i_sig is None:
        out["blocker"] = "SIGNAL_BAR_MISSING"
        return out
    if int(i_sig) + 1 < LB:
        out["blocker"] = "P2_WINDOW_INCOMPLETE"
        return out
    idxs = list(range(int(i_sig) - LB + 1, int(i_sig) + 1))
    bars = []
    lows: list[float] = []
    highs: list[float] = []
    fins: list[float] = []
    for k in idxs:
        lo = _arr_at(tf1, "low", k)
        hi = _arr_at(tf1, "high", k)
        cl = _arr_at(tf1, "close", k)
        ft = _arr_at(tf1, "finalize_t", k)
        if lo is None or hi is None or cl is None or ft is None:
            out["blocker"] = f"P2_BAR_OHLC_MISSING:{k}"
            return out
        bars.append({"i": int(k), "low": lo, "high": hi, "close": cl, "finalize_t": ft})
        lows.append(lo)
        highs.append(hi)
        fins.append(ft)
    setup_low = float(min(lows))
    setup_high = float(max(highs))
    max_fin = float(max(fins))
    out.update(
        {
            "signal_bar_i": int(i_sig),
            "signal_bar_finalize_t": _arr_at(tf1, "finalize_t", int(i_sig)),
            "bar_indices": [int(k) for k in idxs],
            "bars": bars,
            "setup_low": setup_low,
            "setup_high": setup_high,
            "signal_bar_low": _arr_at(tf1, "low", int(i_sig)),
            "signal_bar_high": _arr_at(tf1, "high", int(i_sig)),
            "signal_bar_close": _arr_at(tf1, "close", int(i_sig)),
            "p1_trend_up": bool(trend_up(tf1, int(i_sig))),
            "p2_pullback_setup": bool(pullback_setup(tf1, int(i_sig))),
            "p3_reversal_rci_at_signal_only": bool(reversal_rci(tf1, int(i_sig))),
            "max_reference_finalize_t": max_fin,
            "reference_le_signal": bool(max_fin <= float(t0) + PX_EPS),
            "setup_low_is_min": bool(abs(setup_low - min(lows)) <= PX_EPS)
            and all(setup_low <= x + PX_EPS for x in lows),
            "setup_high_is_max": bool(abs(setup_high - max(highs)) <= PX_EPS)
            and all(setup_high + PX_EPS >= x for x in highs),
        }
    )
    if not out["p1_trend_up"] or not out["p2_pullback_setup"] or not out["p3_reversal_rci_at_signal_only"]:
        out["blocker"] = "T3_SIGNAL_PREDICATE_MISMATCH"
        return out
    if not out["setup_low_is_min"] or not out["setup_high_is_max"]:
        out["blocker"] = "SETUP_EXTREMA_MISMATCH"
        return out
    if not out["reference_le_signal"]:
        out["blocker"] = "REFERENCE_AFTER_SIGNAL"
        return out
    if setup_high + PX_EPS < setup_low:
        out["blocker"] = "INVERTED_SETUP_RANGE"
        return out
    out["ok"] = True
    return out


def _first_close_event(
    tf1: dict[str, np.ndarray],
    *,
    start_i: int,
    sess_end: float,
    pred,
) -> tuple[Optional[float], Optional[int]]:
    fin = tf1.get("finalize_t")
    close = tf1.get("close")
    if fin is None or close is None:
        return None, None
    n = int(fin.size)
    for i in range(int(start_i), n):
        ft = float(fin[i]) if _finite(fin[i]) else None
        cl = float(close[i]) if _finite(close[i]) else None
        if ft is None or cl is None:
            continue
        if ft > float(sess_end) + PX_EPS:
            break
        if pred(cl, i):
            return ft, int(i)
    return None, None


def _same_bar(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= float(SAME_BAR_SEC)


def eval_structure(
    tf1: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    t0: float,
    fill_t: float,
    fill_px: Optional[float],
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    ref = recover_p2_reference(tf1, float(t0))
    out: dict[str, Any] = {
        "reference_ok": bool(ref.get("ok")),
        "reference_blocker": ref.get("blocker"),
        "reference": ref,
        "floor_break_t": None,
        "floor_break_i": None,
        "upside_break_t": None,
        "upside_break_i": None,
        "sequence": "D_NEITHER_BEFORE_SESSION_CLOSE",
        "floor_break": False,
        "upside_break": False,
        "floor_break_first": False,
        "upside_break_first": False,
        "same_completed_bar": False,
        "neither": True,
        "executability": {},
        "closed_overlap": {},
        "timing": {},
        "distance": {},
    }
    if not ref.get("ok"):
        return out
    setup_low = float(ref["setup_low"])
    setup_high = float(ref["setup_high"])
    i_sig = int(ref["signal_bar_i"])
    start_i = i_sig + 1
    floor_t, floor_i = _first_close_event(
        tf1, start_i=start_i, sess_end=float(sess_end), pred=lambda cl, _i: cl < setup_low - PX_EPS
    )
    up_t, up_i = _first_close_event(
        tf1, start_i=start_i, sess_end=float(sess_end), pred=lambda cl, _i: cl > setup_high + PX_EPS
    )
    out["floor_break_t"] = floor_t
    out["floor_break_i"] = floor_i
    out["upside_break_t"] = up_t
    out["upside_break_i"] = up_i
    out["floor_break"] = floor_t is not None
    out["upside_break"] = up_t is not None
    same = bool(floor_t is not None and up_t is not None and abs(float(floor_t) - float(up_t)) <= PX_EPS)
    if same:
        seq = "C_SAME_COMPLETED_BAR"
        floor_first = False
        up_first = False
    elif floor_t is not None and (up_t is None or float(floor_t) < float(up_t) - PX_EPS):
        seq = "B_FLOOR_BREAK_FIRST"
        floor_first = True
        up_first = False
    elif up_t is not None and (floor_t is None or float(up_t) < float(floor_t) - PX_EPS):
        seq = "A_UPSIDE_BREAK_FIRST"
        floor_first = False
        up_first = True
    else:
        seq = "D_NEITHER_BEFORE_SESSION_CLOSE"
        floor_first = False
        up_first = False
    out["sequence"] = seq
    out["floor_break_first"] = bool(floor_first)
    out["upside_break_first"] = bool(up_first)
    out["same_completed_bar"] = bool(same)
    out["neither"] = seq == "D_NEITHER_BEFORE_SESSION_CLOSE"

    def _lag(event_t: Optional[float], origin: float) -> Optional[float]:
        if event_t is None:
            return None
        return float(event_t) - float(origin)

    out["timing"] = {
        "signal_to_floor_sec": _lag(floor_t, t0),
        "signal_to_upside_sec": _lag(up_t, t0),
        "fill_to_floor_sec": _lag(floor_t, fill_t),
        "fill_to_upside_sec": _lag(up_t, fill_t),
    }
    range_yen = setup_high - setup_low
    den = float(fill_px) if fill_px is not None and float(fill_px) > 0 else None
    fill_vs_low = (float(fill_px) - setup_low) if fill_px is not None else None
    sig_close = ref.get("signal_bar_close")
    out["distance"] = {
        "setup_range_yen": range_yen,
        "setup_range_bps": (range_yen / den * 10000.0) if den else None,
        "fill_minus_setup_low_yen": fill_vs_low,
        "fill_minus_setup_low_bps": (fill_vs_low / den * 10000.0) if den is not None and fill_vs_low is not None else None,
        "signal_close_minus_setup_low_yen": (float(sig_close) - setup_low) if sig_close is not None else None,
        "signal_close_minus_setup_low_bps": (
            (float(sig_close) - setup_low) / den * 10000.0 if den is not None and sig_close is not None else None
        ),
    }

    exe: dict[str, Any] = {
        "event_time": floor_t,
        "first_executable_time": None,
        "first_executable_bid": None,
        "latency_sec": None,
        "freshness_ok": None,
        "freshness_evidence": None,
        "miss": True,
    }
    if floor_t is not None and fill_px is not None:
        ev = first_causal_bid_evidence(
            board, meta, event_t=float(floor_t), fill_px=float(fill_px), sess_end=float(sess_end), leak=leak
        )
        exe["first_executable_time"] = ev.get("exit_t")
        exe["first_executable_bid"] = ev.get("exit_bid")
        exe["miss"] = bool(ev.get("miss"))
        exe["freshness_evidence"] = ev.get("freshness_evidence")
        fe = dict(ev.get("freshness_evidence") or {})
        exe["freshness_ok"] = fe.get("freshness_ok") if fe else (not bool(ev.get("miss")))
        if ev.get("exit_t") is not None:
            exe["latency_sec"] = float(ev["exit_t"]) - float(floor_t)
            if float(ev["exit_t"]) + PX_EPS < float(floor_t):
                leak["FUTURE_QUOTE_CARRYBACK_N"] = int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0) + 1
    out["executability"] = exe

    bb_t, _bb_i = _first_close_event(
        tf1,
        start_i=int(np.searchsorted(tf1.get("finalize_t") if tf1.get("finalize_t") is not None else np.asarray([]), float(fill_t), side="right")),
        sess_end=float(sess_end),
        pred=lambda cl, i: (
            _arr_at(tf1, "bb_lower", i) is not None and cl < float(_arr_at(tf1, "bb_lower", i)) - PX_EPS
        ),
    )
    ema_t, _ema_i = _first_close_event(
        tf1,
        start_i=int(np.searchsorted(tf1.get("finalize_t") if tf1.get("finalize_t") is not None else np.asarray([]), float(fill_t), side="right")),
        sess_end=float(sess_end),
        pred=lambda _cl, i: bool(primitive_known("A_EMA_STRUCTURE_LOSS", tf1, i) is True),
    )
    out["closed_overlap"] = {
        "branch_u_bb_lower_t": bb_t,
        "v27_ema_structure_loss_t": ema_t,
        "floor_vs_bb_same_bar": _same_bar(floor_t, bb_t),
        "floor_vs_ema_same_bar": _same_bar(floor_t, ema_t),
        "bb_lower_at_signal": _arr_at(tf1, "bb_lower", i_sig),
        "setup_low_minus_bb_lower_at_signal": (
            setup_low - float(_arr_at(tf1, "bb_lower", i_sig)) if _arr_at(tf1, "bb_lower", i_sig) is not None else None
        ),
    }
    return out


def harvest_trade(row: dict[str, Any], packed: dict[str, Any], leak: dict[str, Any]) -> dict[str, Any]:
    sym = _bare(row.get("symbol"))
    t0 = _f(row.get("t0"))
    fill_t = _f(row.get("fill_time"))
    fill_px = _f(row.get("fill_price"))
    out = dict(row)
    if t0 is None or fill_t is None:
        out["structure_ok"] = False
        out["structure_path"] = {"reference_ok": False, "reference_blocker": "MISSING_T0_OR_FILL"}
        return out
    tf_by = (packed.get("tf_by_sym") or {}).get(sym) or {}
    tf1 = tf_by.get("tf1") or {}
    board = (packed.get("views") or {}).get(sym) or {}
    meta = (packed.get("metas") or {}).get(sym) or {}
    path = eval_structure(
        tf1,
        board,
        meta,
        t0=float(t0),
        fill_t=float(fill_t),
        fill_px=fill_px,
        sess_end=float(packed["am_end"]),
        leak=leak,
    )
    out["structure_ok"] = bool(path.get("reference_ok"))
    out["structure_path"] = path
    return out


def harvest_day(
    day: str,
    *,
    cohort: str,
    rows: list[dict[str, Any]],
    spec_sha: str,
    today: str = TODAY,
) -> dict[str, Any]:
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    if not rows:
        body = {"ok": True, "date": day, "cohort": cohort, "spec_sha": spec_sha, "rows": [], "leak": {}}
        PATH_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return body
    capture = find_capture_dir(day)
    if capture is None:
        return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
    symbols = {_bare(r.get("symbol")) for r in rows if _bare(r.get("symbol"))}
    leak = {"FUTURE_QUOTE_CARRYBACK_N": 0, "FUTURE_TIMESTAMP_CARRYBACK_N": 0, "PRE_DECISION_EXIT_N": 0, "PRE_BAR_EXIT_N": 0}
    print(f"{cohort} {day} anchored-structure symbols={len(symbols)} fills={len(rows)}", flush=True)
    packed = stream_day(day, capture, symbols, leak)
    out_rows = [harvest_trade(dict(r), packed, leak) for r in rows]
    body = {"ok": True, "date": day, "cohort": cohort, "spec_sha": spec_sha, "rows": out_rows, "leak": leak}
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    del packed
    gc.collect()
    return body
