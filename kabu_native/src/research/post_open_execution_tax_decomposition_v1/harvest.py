"""Overlay canonical W5 on the parent clock-anchor population. Features reused, not recomputed."""
from __future__ import annotations

import gzip
import os
import pickle
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, iter_push, record_event_stamp
from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.entry_execution_feasibility.fill import limit_bid_at_t0, standalone_fill
from research.new_entry_breakout_continuation_v1.harvest import BoardTape, board_row
from research.post_open_causal_upside_mechanism_discovery_v1 import (
    ANCHOR_HMS,
    DEVELOPMENT_DAYS,
    PRIMARY_HORIZON_SEC,
    SESSION_FLATTEN_HM,
)
from research.post_open_causal_upside_mechanism_discovery_v1.features import _bps, _f, fresh_ok, path_outcomes
from research.post_open_causal_upside_mechanism_discovery_v1.harvest import harvest_upside
from research.post_open_execution_tax_decomposition_v1 import W5_WAIT_SEC
from research.post_open_execution_tax_decomposition_v1.isolation import CACHE
from research.post_open_prior_close_recapture_full_strategy_v1.fields import (
    ingress_epoch,
    observed_trade_update,
    trusted_state,
)
from research.post_open_prior_close_recapture_full_strategy_v1.harvest import assert_dev_only_day, sealed_dev_caps

CACHE_SCHEMA = "W5_OVERLAY_V1"


def day_overlay_key(symbol: str, hm: str) -> str:
    return f"{symbol}|{hm}"


def join_overlay_key(date: str, symbol: str, hm: str) -> str:
    return f"{date}|{symbol}|{hm}"


def _dump_gz(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        pickle.dump(body, fh, protocol=4)


def _load_gz(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with gzip.open(path, "rb") as fh:
        got = pickle.load(fh)
    return got if isinstance(got, dict) else {}


def first_bid_after_np(board: dict[str, np.ndarray], *, t_start: float, flatten_t: float) -> Optional[float]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return None
    i0 = int(np.searchsorted(t, float(t_start), side="left"))
    exe = board.get("continuous")
    if exe is None:
        exe = board.get("executable")
    bid = board["bid"]
    qty = board["bid_qty"]
    fresh = board["bid_fresh_sec"]
    for i in range(i0, int(t.size)):
        if float(t[i]) + 1e-12 >= float(flatten_t):
            break
        if exe is not None and not bool(exe[i]):
            continue
        age = float(fresh[i]) if np.isfinite(fresh[i]) else None
        if not fresh_ok(age):
            continue
        b = float(bid[i])
        q = float(qty[i])
        if b == b and b > 0 and q == q and q > 0:
            return b
    return None


def simulate_w5(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    sess_end: float,
    flatten_t: float,
    otu_t: list[float],
    otu_px: list[float],
    ask0: Optional[float],
    parent_markout: Optional[float],
) -> dict[str, Any]:
    limit = limit_bid_at_t0(board, float(t0))
    if limit is None:
        return {"W5_FILLED": False, "W5_limit": None, "fill_reason": "INVALID_LIMIT"}
    got = standalone_fill(
        board,
        t0=float(t0),
        wait_sec=float(W5_WAIT_SEC),
        limit_price=float(limit),
        sess_end=float(sess_end),
    )
    filled = bool(got.get("WOULD_FILL"))
    fill_t = got.get("fill_t") if filled else None
    fill_px = got.get("fill_price") if filled else None
    out: dict[str, Any] = {
        "W5_FILLED": filled,
        "W5_limit": float(limit),
        "FILL_T": float(fill_t) if fill_t is not None else None,
        "FILL_PRICE": float(fill_px) if fill_px is not None else None,
        "TIME_TO_FILL_SEC": (float(fill_t) - float(t0)) if (filled and fill_t is not None) else None,
        "fill_reason": got.get("fill_reason"),
        "nonfill_class": got.get("nonfill_class"),
        "repricing": False,
        "chase": False,
        "fallback_ask": False,
    }
    if ask0 is not None and parent_markout is not None:
        endpoint = float(ask0) * (1.0 + float(parent_markout) / 10000.0)
        out["X1_SAME_ENDPOINT_MARKOUT_BPS"] = float(parent_markout)
        out["ENDPOINT_BID_T_PLUS_10M"] = endpoint
        if filled and fill_px is not None and float(fill_px) > 0:
            out["W5_SAME_ENDPOINT_MARKOUT_BPS"] = _bps(endpoint - float(fill_px), float(fill_px))
            out["PRICE_IMPROVEMENT_BPS"] = float(out["W5_SAME_ENDPOINT_MARKOUT_BPS"]) - float(parent_markout)
    if not filled or fill_t is None or fill_px is None:
        return out
    bid10 = first_bid_after_np(board, t_start=float(fill_t) + float(PRIMARY_HORIZON_SEC), flatten_t=flatten_t)
    mark = _bps((bid10 - float(fill_px)) if bid10 is not None else None, float(fill_px))
    p10 = path_outcomes(
        otu_t=otu_t,
        otu_px=otu_px,
        ask0=float(fill_px),
        t=float(fill_t),
        horizon=PRIMARY_HORIZON_SEC,
        flatten_t=flatten_t,
    )
    out.update(
        {
            "W5_MARKOUT_FROM_FILL_10M_BPS": mark,
            "W5_MFE_10M_BPS": p10["mfe"],
            "W5_MAE_10M_BPS": p10["mae"],
            "W5_PATH_EDGE_10M_BPS": p10["edge"],
            "W5_UP_DOMINANT_10M": (p10["edge"] is not None and float(p10["edge"]) > 0),
            "W5_EXEC_POSITIVE_10M": (mark is not None and float(mark) > 0),
            "W5_path_otu_n": p10["n"],
            "W5_future_leak_n": int(p10["leak"]),
        }
    )
    return out


def process_dev_day(payload: dict[str, Any], parent_rows: list[dict[str, Any]]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    try:
        assert_dev_only_day(day)
    except RuntimeError as exc:
        return {"ok": False, "date": day, "blocker": str(exc)}
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    t0w = time.perf_counter()
    am_start = float(hm_epoch(day, 9, 0))
    flatten_t = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
    sess_end = float(session_end_epoch(day, "AM"))
    tapes: dict[str, BoardTape] = {s: BoardTape() for s in universe}
    otu_t: dict[str, list[float]] = {s: [] for s in universe}
    otu_px: dict[str, list[float]] = {s: [] for s in universe}
    last_vol: dict[str, Optional[float]] = {s: None for s in universe}
    events_n = 0
    try:
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in uni:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            ing = ingress_epoch(rec, pay)
            if ing is None:
                continue
            if float(ing) < am_start - 120.0 or float(ing) > sess_end + 2.0:
                continue
            st = trusted_state(pay, event_t=float(ing))
            vol = st["vol"]
            px = st["px"]
            if observed_trade_update(last_vol=last_vol[sym], vol=vol, px=px):
                otu_t[sym].append(float(ing))
                otu_px[sym].append(float(px))
            if vol is not None and not (last_vol[sym] is not None and float(vol) < float(last_vol[sym])):
                last_vol[sym] = float(vol)
            tapes[sym].append(board_row(rec, pay, float(ing)))
            events_n += 1
            if events_n % 400000 == 0:
                print(f"{day} W5 events={events_n}", flush=True)
        views = {s: tapes[s].view() for s in universe}
        overlays: dict[str, dict[str, Any]] = {}
        leak = 0
        for r in parent_rows:
            if str(r.get("date")) != day:
                continue
            s = str(r.get("symbol") or "")
            hm = str(r.get("anchor_hm") or "")
            if not r.get("executable"):
                overlays[day_overlay_key(s, hm)] = {"W5_FILLED": False, "fill_reason": "NOT_EXECUTABLE_ANCHOR"}
                continue
            got = simulate_w5(
                views.get(s) or {},
                t0=float(r["t"]),
                sess_end=sess_end,
                flatten_t=flatten_t,
                otu_t=otu_t.get(s) or [],
                otu_px=otu_px.get(s) or [],
                ask0=_f(r.get("ASK1_AT_T")),
                parent_markout=_f(r.get("EXEC_MARKOUT_10M_BPS")),
            )
            leak += int(got.get("W5_future_leak_n") or 0)
            overlays[day_overlay_key(s, hm)] = got
        ok = leak == 0
        print(f"{day} W5 OVERLAY events={events_n} n={len(overlays)} leak={leak}", flush=True)
        return {
            "ok": ok,
            "date": day,
            "overlays": overlays,
            "events_n": events_n,
            "future_leak_n": leak,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None if ok else "FUTURE_LEAK",
        }
    except Exception as exc:
        return {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}", "elapsed_sec": round(time.perf_counter() - t0w, 3)}


def overlay_w5(parent_rows: list[dict[str, Any]]) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers}
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in parent_rows:
        by_day.setdefault(str(r.get("date")), []).append(r)
    overlays: dict[str, dict[str, Any]] = {}
    leak = 0
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"W5_{day}.pkl.gz"
        saved = _load_gz(cache)
        if saved.get("ok") and saved.get("schema") == CACHE_SCHEMA and str(saved.get("date") or "") == day:
            for k, v in dict(saved.get("overlays") or {}).items():
                overlays[join_overlay_key(day, *str(k).split("|", 1))] = v
            leak += int(saved.get("future_leak_n") or 0)
            print(f"cache-hit W5 {day}", flush=True)
            continue
        body = process_dev_day(
            {"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])},
            by_day.get(day) or [],
        )
        if not body.get("ok"):
            return {"ok": False, "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}"}
        _dump_gz(
            cache,
            {
                "ok": True,
                "schema": CACHE_SCHEMA,
                "date": day,
                "overlays": body.get("overlays"),
                "future_leak_n": body.get("future_leak_n"),
            },
        )
        for k, v in dict(body.get("overlays") or {}).items():
            overlays[join_overlay_key(day, *str(k).split("|", 1))] = v
        leak += int(body.get("future_leak_n") or 0)
    joined = []
    miss = 0
    same_mismatch = 0
    for r in parent_rows:
        key = join_overlay_key(str(r.get("date") or ""), str(r.get("symbol") or ""), str(r.get("anchor_hm") or ""))
        over = overlays.get(key)
        rec = dict(r)
        if over is None:
            miss += 1
            rec["W5_FILLED"] = False
            rec["W5_MISSING_OVERLAY"] = True
        else:
            rec.update(over)
            rec["W5_MISSING_OVERLAY"] = False
            x1_same = rec.get("X1_SAME_ENDPOINT_MARKOUT_BPS")
            parent_m = rec.get("EXEC_MARKOUT_10M_BPS")
            if rec.get("W5_FILLED") and x1_same is not None and parent_m is not None:
                if abs(float(x1_same) - float(parent_m)) > 1e-6:
                    same_mismatch += 1
        joined.append(rec)
    ok = leak == 0 and miss == 0 and same_mismatch == 0
    blocker = None
    if leak:
        blocker = "FUTURE_LEAK"
    elif miss:
        blocker = "OVERLAY_MISS"
    elif same_mismatch:
        blocker = f"X1_SAME_PARENT_MISMATCH:{same_mismatch}"
    return {
        "ok": ok,
        "rows": joined,
        "future_leak_n": leak,
        "overlay_miss_n": miss,
        "x1_same_mismatch_n": same_mismatch,
        "days": list(DEVELOPMENT_DAYS),
        "blocker": blocker,
    }


def load_parent_population() -> dict[str, Any]:
    return harvest_upside()
