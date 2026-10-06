"""Harvest SECOND_BELOW_BE_AFTER_RECLAIM and related economic crossings."""
from __future__ import annotations

import gc
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from replay.pnl_yen import compute_pnl_yen_100
from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v28_harvest import _bid_ok, _clock_ok

PATH_CACHE = RESEARCH_CACHE / "proven_failure_causal_actionability_gate"
EPS = 1e-9


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


def walk_economic_be_sequence(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    fill_px: float,
    be_t: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    t = board.get("t")
    out: dict[str, Any] = {
        "first_BE_time": float(be_t),
        "first_below_be_time": None,
        "first_reclaim_time": None,
        "second_below_be_after_reclaim_time": None,
        "had_first_below_be": False,
        "had_first_reclaim": False,
        "had_second_below_be_after_reclaim": False,
        "above_to_below_crossing_n": 0,
        "below_to_above_crossing_n": 0,
        "be_to_first_below_sec": None,
        "first_below_to_reclaim_sec": None,
        "reclaim_to_second_below_sec": None,
        "post_be_quote_n": 0,
        "micro_jitter_warn": False,
    }
    if t is None or int(t.size) == 0:
        return out
    state = "AT_OR_ABOVE_BE"
    i0 = int(np.searchsorted(t, float(be_t), side="left"))
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(be_t):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        if not _clock_ok(meta, i, ti, leak) or not _bid_ok(board, i):
            continue
        bid = float(board["bid"][i])
        if bid <= 0.0:
            continue
        yen = float(compute_pnl_yen_100(float(fill_px), bid, side="long"))
        out["post_be_quote_n"] = int(out["post_be_quote_n"]) + 1
        if state in ("AT_OR_ABOVE_BE", "ABOVE_BE") and yen < -EPS:
            out["above_to_below_crossing_n"] = int(out["above_to_below_crossing_n"]) + 1
            if out["first_below_be_time"] is None:
                out["first_below_be_time"] = ti
                out["had_first_below_be"] = True
                out["be_to_first_below_sec"] = float(ti) - float(be_t)
            elif out["had_first_reclaim"] and not out["had_second_below_be_after_reclaim"]:
                out["second_below_be_after_reclaim_time"] = ti
                out["had_second_below_be_after_reclaim"] = True
                if out["first_reclaim_time"] is not None:
                    out["reclaim_to_second_below_sec"] = float(ti) - float(out["first_reclaim_time"])
            state = "BELOW_BE"
        elif state == "BELOW_BE" and yen >= 0.0:
            out["below_to_above_crossing_n"] = int(out["below_to_above_crossing_n"]) + 1
            if out["first_below_be_time"] is not None and out["first_reclaim_time"] is None:
                out["first_reclaim_time"] = ti
                out["had_first_reclaim"] = True
                out["first_below_to_reclaim_sec"] = float(ti) - float(out["first_below_be_time"])
            state = "AT_OR_ABOVE_BE"
    out["micro_jitter_warn"] = bool(
        int(out["above_to_below_crossing_n"]) + int(out["below_to_above_crossing_n"]) >= 50
    )
    return out


def harvest_trade_actionability(
    row: dict[str, Any],
    packed: dict[str, Any],
    leak: dict[str, Any],
) -> dict[str, Any]:
    sym = _bare(row.get("symbol"))
    fill_px = _f(row.get("fill_price"))
    be_t = _f(row.get("first_break_even_time"))
    sess_end = float(packed["am_end"])
    out = dict(row)
    if not row.get("break_even_reached") or fill_px is None or be_t is None:
        out["actionability_ok"] = False
        return out
    board = packed["views"].get(sym) or {}
    meta = packed["metas"].get(sym) or {}
    seq = walk_economic_be_sequence(
        board, meta, fill_px=float(fill_px), be_t=float(be_t), sess_end=sess_end, leak=leak
    )
    out["actionability_ok"] = True
    out["economic_sequence"] = seq
    out["first_BE_time"] = seq.get("first_BE_time")
    out["first_below_BE_time"] = seq.get("first_below_be_time")
    out["first_BE_reclaim_time"] = seq.get("first_reclaim_time")
    out["second_below_BE_after_reclaim_time"] = seq.get("second_below_be_after_reclaim_time")
    return out


def harvest_day_actionability(
    day: str,
    *,
    cohort: str,
    be_rows: list[dict[str, Any]],
    spec_sha: str,
    today: str = TODAY,
) -> dict[str, Any]:
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    if not be_rows:
        body = {"ok": True, "date": day, "cohort": cohort, "spec_sha": spec_sha, "rows": [], "leak": {}, "events_n": 0}
        PATH_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return body
    capture = find_capture_dir(day)
    if capture is None:
        return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}

    symbols = {_bare(r.get("symbol")) for r in be_rows if _bare(r.get("symbol"))}
    leak = {"FUTURE_QUOTE_CARRYBACK_N": 0, "FUTURE_TIMESTAMP_CARRYBACK_N": 0, "BE_ANCHOR_DRIFT_N": 0}
    print(f"{cohort} {day} actionability symbols={len(symbols)} be={len(be_rows)}", flush=True)
    packed = stream_day(day, capture, symbols, leak)
    out_rows = [harvest_trade_actionability(dict(r), packed, leak) for r in be_rows]
    body = {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "spec_sha": spec_sha,
        "rows": out_rows,
        "leak": leak,
        "events_n": packed.get("events_n"),
    }
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    del packed
    gc.collect()
    return body
