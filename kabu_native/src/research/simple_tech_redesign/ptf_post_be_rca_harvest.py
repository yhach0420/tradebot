"""Post-BE causal transition harvest from burned capture. Anchor = first economic BE."""
from __future__ import annotations

import gc
import json
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from replay.pnl_yen import compute_pnl_yen_100
from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_entry_family.stages import attach_indicators, trend_up
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars
from research.simple_tech_redesign.exit_lifecycle_harvest import _first_bar_flag, walk_break_even
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.ptf_post_be_rca_spec import PRIMARY_STRUCTURE
from research.simple_tech_redesign.v24_harvest import NAN, _finite, corrected_board_freshness
from research.simple_tech_redesign.v27_harvest import primitive_known
from research.simple_tech_redesign.v28_harvest import _bid_ok, _clock_ok, eval_k6_persistence
from research.simple_tech_redesign.v28_spec import PERSISTENCE_K
from small_paper.v1r_live_dual_lane import session_end_for_position

PATH_CACHE = RESEARCH_CACHE / "ptf_post_be_state_transition_rca"
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


def _leak0() -> dict[str, Any]:
    return {
        "FUTURE_QUOTE_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "BE_ANCHOR_DRIFT_N": 0,
        "PATH_SKIP_N": 0,
        "BOARD_CLOCK_UNRESOLVED_N": 0,
    }


def _structure_pred(kind: str) -> Callable[[dict[str, np.ndarray], int], Optional[bool]]:
    if kind == "A_EMA_STRUCTURE_LOSS_3M":
        return lambda ind, i: primitive_known("A_EMA_STRUCTURE_LOSS", ind, i)
    if kind == "A_EMA_STRUCTURE_LOSS_5M":
        return lambda ind, i: primitive_known("A_EMA_STRUCTURE_LOSS", ind, i)
    if kind == "D_BB_STRUCTURE_LOSS_3M":
        return lambda ind, i: primitive_known("D_BB_STRUCTURE_LOSS", ind, i)
    if kind == "E_RCI_ROLLOVER_3M":
        return lambda ind, i: primitive_known("E_RCI_ROLLOVER", ind, i)
    if kind == "TREND_LOST_1M":
        return lambda ind, i: (not trend_up(ind, i)) if trend_up(ind, i) is not None else None
    raise KeyError(kind)


def _tf_for_structure(kind: str) -> str:
    if kind.endswith("_5M"):
        return "tf5"
    if kind in ("TREND_LOST_1M",):
        return "tf1"
    return "tf3"


def walk_post_be_economic(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    be_t: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    t = board.get("t")
    out = {
        "first_positive_executable_time": None,
        "first_below_be_time": None,
        "first_be_reclaim_time": None,
        "below_be_transition_n": 0,
        "reclaim_transition_n": 0,
        "post_be_quote_n": 0,
        "ended_below_be": None,
    }
    if t is None or int(t.size) == 0:
        return out
    state = "AT_OR_ABOVE_BE"
    i0 = int(np.searchsorted(t, float(be_t), side="left"))
    last_yen = None
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
        last_yen = yen
        if out["first_positive_executable_time"] is None and yen > EPS:
            out["first_positive_executable_time"] = ti
        if state in ("AT_OR_ABOVE_BE", "ABOVE_BE") and yen < -EPS:
            out["below_be_transition_n"] = int(out["below_be_transition_n"]) + 1
            if out["first_below_be_time"] is None:
                out["first_below_be_time"] = ti
            state = "BELOW_BE"
        elif state == "BELOW_BE" and yen >= 0.0:
            out["reclaim_transition_n"] = int(out["reclaim_transition_n"]) + 1
            if out["first_be_reclaim_time"] is None:
                out["first_be_reclaim_time"] = ti
            state = "AT_OR_ABOVE_BE"
    out["ended_below_be"] = bool(last_yen is not None and float(last_yen) < -EPS)
    return out


def _structure_inventory(
    tf_by: dict[str, dict[str, np.ndarray]],
    *,
    be_t: float,
) -> dict[str, Any]:
    inv: dict[str, Any] = {}
    earliest_t = None
    earliest_kind = None
    for kind in (
        "A_EMA_STRUCTURE_LOSS_3M",
        "A_EMA_STRUCTURE_LOSS_5M",
        "D_BB_STRUCTURE_LOSS_3M",
        "E_RCI_ROLLOVER_3M",
        "TREND_LOST_1M",
    ):
        tf = tf_by.get(_tf_for_structure(kind)) or {}
        hit, ht = _first_bar_flag(tf, start_t=float(be_t), end_t=None, pred=_structure_pred(kind))
        inv[kind] = {"hit": bool(hit), "time": ht}
        if hit and ht is not None and (earliest_t is None or float(ht) < float(earliest_t) - 1e-12):
            earliest_t = float(ht)
            earliest_kind = kind
    inv["first_structure_loss_type"] = earliest_kind
    inv["first_structure_loss_time"] = earliest_t
    return inv


def _ema_recover_after_loss(
    tf3: dict[str, np.ndarray],
    *,
    loss_i: int,
) -> dict[str, Any]:
    fin = tf3.get("finalize_t")
    n = int(fin.size) if fin is not None else 0
    out = {
        "first_structure_recover_time": None,
        "second_structure_loss_time": None,
        "reclaim_after_loss": False,
        "reloss_after_reclaim": False,
        "v29_family_a": None,
    }
    if loss_i < 0 or loss_i >= n:
        return out
    ema_prev = True
    saw_reclaim = False
    for i in range(int(loss_i) + 1, n):
        ema_loss = primitive_known("A_EMA_STRUCTURE_LOSS", tf3, int(i))
        reclaim = bool(ema_prev is True and ema_loss is False)
        reloss = bool(ema_prev is False and ema_loss is True)
        if reclaim and out["first_structure_recover_time"] is None:
            out["first_structure_recover_time"] = float(fin[i]) if _finite(fin[i]) else None
            saw_reclaim = True
        if saw_reclaim and reloss and out["second_structure_loss_time"] is None:
            out["second_structure_loss_time"] = float(fin[i]) if _finite(fin[i]) else None
            out["reloss_after_reclaim"] = True
            break
        if ema_loss is not None:
            ema_prev = bool(ema_loss)
    out["reclaim_after_loss"] = bool(saw_reclaim)
    if not saw_reclaim:
        out["v29_family_a"] = "A_NO_RECLAIM"
    elif out["reloss_after_reclaim"]:
        out["v29_family_a"] = "A_RECLAIM_THEN_RELOSS"
    else:
        out["v29_family_a"] = "A_RECLAIM_THEN_MAINTAIN"
    return out


def _loss_bar_index(tf3: dict[str, np.ndarray], loss_t: float) -> Optional[int]:
    fin = tf3.get("finalize_t")
    if fin is None or int(fin.size) == 0:
        return None
    i = int(np.searchsorted(fin, float(loss_t), side="left"))
    if i < int(fin.size) and _finite(fin[i]) and abs(float(fin[i]) - float(loss_t)) <= 1e-6:
        return i
    return i if 0 <= i < int(fin.size) else None


def eval_post_be_transitions(
    tf_by: dict[str, dict[str, np.ndarray]],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    be_t: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    econ = walk_post_be_economic(
        board, meta, fill_t=float(fill_t), fill_px=float(fill_px), be_t=float(be_t), sess_end=float(sess_end), leak=leak
    )
    inv = _structure_inventory(tf_by, be_t=float(be_t))
    tf3 = tf_by.get("tf3") or {}
    primary_t = inv.get("first_structure_loss_time")
    primary_kind = inv.get("first_structure_loss_type")
    ema_seq: dict[str, Any] = {}
    if primary_kind == PRIMARY_STRUCTURE and primary_t is not None:
        li = _loss_bar_index(tf3, float(primary_t))
        if li is not None:
            ema_seq = _ema_recover_after_loss(tf3, loss_i=int(li))
    k6 = eval_k6_persistence(tf3, fill_t=float(be_t), k=int(PERSISTENCE_K))
    below_t = _f(econ.get("first_below_be_time"))
    struct_t = _f(primary_t)
    if below_t is not None and struct_t is not None:
        if abs(float(below_t) - float(struct_t)) <= 1e-6:
            first_order = "TIE"
        elif float(below_t) < float(struct_t) - 1e-12:
            first_order = "BELOW_BE_FIRST"
        else:
            first_order = "STRUCTURE_LOSS_FIRST"
    elif below_t is not None:
        first_order = "BELOW_BE_FIRST"
    elif struct_t is not None:
        first_order = "STRUCTURE_LOSS_FIRST"
    else:
        first_order = "NEITHER"
    seq_a = {
        "had_below_be": below_t is not None,
        "had_be_reclaim": _f(econ.get("first_be_reclaim_time")) is not None,
        "no_reclaim_after_below": bool(below_t is not None and _f(econ.get("first_be_reclaim_time")) is None),
    }
    recover_t = _f(ema_seq.get("first_structure_recover_time"))
    second_t = _f(ema_seq.get("second_structure_loss_time"))
    seq_b = {
        "had_structure_loss": struct_t is not None,
        "had_structure_recover": recover_t is not None,
        "no_recover_after_loss": bool(struct_t is not None and recover_t is None),
    }
    seq_c = {
        "had_second_structure_loss": second_t is not None,
        "reclaim_then_reloss": bool(recover_t is not None and second_t is not None),
    }
    return {
        "anchor_time": float(be_t),
        "economic": econ,
        "structure_inventory": inv,
        "ema_recover_sequence": ema_seq,
        "k6_post_be": {
            "k6_reached": bool(k6.get("k6_reached")),
            "k6_trigger_t": k6.get("k6_trigger_t"),
            "k6_recovered_before_trigger_n": int(k6.get("k6_recovered_before_trigger_n") or 0),
        },
        "sequence_a": seq_a,
        "sequence_b": seq_b,
        "sequence_c": seq_c,
        "sequence_d_first_order": first_order,
        "primary_structure_kind": primary_kind,
        "first_structure_loss_time": struct_t,
        "first_structure_recover_time": recover_t,
        "second_structure_loss_time": second_t,
        "first_below_be_time": below_t,
        "first_be_reclaim_time": _f(econ.get("first_be_reclaim_time")),
        "first_positive_executable_time": _f(econ.get("first_positive_executable_time")),
        "v29_family_a": ema_seq.get("v29_family_a"),
    }


def stream_day(
    day: str,
    capture: Path,
    symbols: set[str],
    leak: dict[str, Any],
) -> dict[str, Any]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    bufs = {s: _Buf() for s in symbols}
    builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
    srcs = {s: [] for s in symbols}
    clocks = {s: [] for s in symbols}
    price_ages = {s: [] for s in symbols}
    events_n = 0
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in symbols:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None or float(et) < am_start - 120.0 or float(et) > am_end + 2.0:
            continue
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
        corr = corrected_board_freshness(rec, pay, float(et))
        row = _board_row(pay, float(et))
        row["fresh_sec"] = float(corr["fresh_sec"]) if _finite(corr.get("fresh_sec")) else NAN
        bufs[sym].append(row)
        srcs[sym].append(str(corr.get("source") or "UNRESOLVED"))
        clocks[sym].append(float(corr["clock_epoch"]) if _finite(corr.get("clock_epoch")) else NAN)
        price_ages[sym].append(float(corr["price_fresh_sec"]) if _finite(corr.get("price_fresh_sec")) else NAN)
        builders[sym].on_event(
            et=float(et),
            px=row["px"] if row["px"] == row["px"] else None,
            cum_vol=row.get("cum_vol"),
            bid=row["bid"] if row["bid"] == row["bid"] else None,
            ask=row["ask"] if row["ask"] == row["ask"] else None,
            continuous=bool(row.get("continuous")),
        )
        events_n += 1
    tf_by_sym: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    views = {s: bufs[s].view() for s in symbols}
    metas = {
        s: {
            "source": np.asarray(srcs[s], dtype=object),
            "clock": np.asarray(clocks[s], dtype=float),
            "price_fresh": np.asarray(price_ages[s], dtype=float),
        }
        for s in symbols
    }
    for s in symbols:
        builders[s].close_session()
        raw = builders[s].as_arrays()
        by_tf: dict[str, dict[str, np.ndarray]] = {}
        if int(raw["minute_epoch"].size) > 0:
            by_tf["tf1"] = attach_indicators(raw)
        for tf, width in (("tf3", 180.0), ("tf5", 300.0)):
            arr, alk = aggregate_bars(raw, width_sec=float(width), am_start=am_start, am_end=am_end)
            if int(arr["minute_epoch"].size) > 0:
                by_tf[tf] = attach_indicators(arr)
        tf_by_sym[s] = by_tf
    return {"views": views, "metas": metas, "tf_by_sym": tf_by_sym, "am_end": am_end, "events_n": events_n}


def harvest_trade_row(
    row: dict[str, Any],
    packed: dict[str, Any],
    leak: dict[str, Any],
) -> dict[str, Any]:
    sym = _bare(row.get("symbol"))
    fill_t = _f(row.get("fill_time"))
    fill_px = _f(row.get("fill_price"))
    be_t = _f(row.get("first_break_even_time"))
    sess_end = float(packed["am_end"])
    out = dict(row)
    if not row.get("break_even_reached") or fill_t is None or fill_px is None or be_t is None:
        out["post_be_ok"] = False
        out["post_be_blocker"] = "NOT_BE_REACHED_OR_MISSING_FILL"
        return out
    board = packed["views"].get(sym) or {}
    meta = packed["metas"].get(sym) or {}
    tf_by = packed["tf_by_sym"].get(sym) or {}
    be_check = walk_break_even(board, meta, fill_t=float(fill_t), fill_px=float(fill_px), sess_end=sess_end, pullback_low=None, leak=leak)
    if be_check.get("be_t") is not None and abs(float(be_check["be_t"]) - float(be_t)) > 1e-6:
        leak["BE_ANCHOR_DRIFT_N"] = int(leak.get("BE_ANCHOR_DRIFT_N") or 0) + 1
    transitions = eval_post_be_transitions(
        tf_by, board, meta, fill_t=float(fill_t), fill_px=float(fill_px), be_t=float(be_t), sess_end=sess_end, leak=leak
    )
    out["post_be_ok"] = True
    out["post_be"] = transitions
    out["first_BE_time"] = float(be_t)
    out["session_close_pnl"] = _f(row.get("session_close_pnl"))
    out["peak_to_close_giveback"] = _f(row.get("peak_to_close_giveback"))
    out["below_be_terminal_loss"] = _f(row.get("below_be_terminal_loss"))
    return out


def harvest_day_be(
    day: str,
    *,
    cohort: str,
    be_rows: list[dict[str, Any]],
    spec_sha: str,
    today: str = TODAY,
) -> dict[str, Any]:
    if day == str(today) or day in {"20260903"}:
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    if not be_rows:
        body = {"ok": True, "date": day, "cohort": cohort, "spec_sha": spec_sha, "rows": [], "leak": _leak0(), "events_n": 0}
        PATH_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return body
    capture = find_capture_dir(day)
    if capture is None:
        return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
    symbols = {_bare(r.get("symbol")) for r in be_rows if _bare(r.get("symbol"))}
    leak = _leak0()
    print(f"{cohort} {day} ptf_post_be symbols={len(symbols)} be_trades={len(be_rows)}", flush=True)
    packed = stream_day(day, capture, symbols, leak)
    out_rows = [harvest_trade_row(dict(r), packed, leak) for r in be_rows]
    body = {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "spec_sha": spec_sha,
        "rows": out_rows,
        "leak": leak,
        "events_n": packed.get("events_n"),
        "be_trade_n": len(out_rows),
    }
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    del packed
    gc.collect()
    return body
