"""Overlay SHORT_X1 / MID / SHORT_W5 on the parent clock-anchor population."""
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
from research.new_entry_breakout_continuation_v1.harvest import BoardTape, board_row
from research.post_open_causal_downside_mechanism_discovery_v1 import (
    DEVELOPMENT_DAYS,
    PRIMARY_HORIZON_SEC,
    SECONDARY_HORIZONS_SEC,
    SESSION_FLATTEN_HM,
    W5_WAIT_SEC,
)
from research.post_open_causal_downside_mechanism_discovery_v1.isolation import CACHE
from research.post_open_causal_downside_mechanism_discovery_v1.short_w5 import limit_ask_at_t0, standalone_short_fill
from research.post_open_causal_upside_mechanism_discovery_v1.features import _bps, _f, fresh_ok
from research.post_open_causal_upside_mechanism_discovery_v1.harvest import harvest_upside
from research.post_open_prior_close_recapture_full_strategy_v1.fields import (
    ingress_epoch,
    observed_trade_update,
    trusted_state,
)
from research.post_open_prior_close_recapture_full_strategy_v1.harvest import assert_dev_only_day, sealed_dev_caps

CACHE_SCHEMA = "SHORT_OVERLAY_V1"
HORIZONS = (PRIMARY_HORIZON_SEC,) + tuple(SECONDARY_HORIZONS_SEC)


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


def first_ask_after_np(board: dict[str, np.ndarray], *, t_start: float, flatten_t: float) -> Optional[float]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return None
    i0 = int(np.searchsorted(t, float(t_start), side="left"))
    exe = board.get("continuous")
    if exe is None:
        exe = board.get("executable")
    ask = board["ask"]
    qty = board["ask_qty"]
    fresh = board["ask_fresh_sec"]
    for i in range(i0, int(t.size)):
        if float(t[i]) + 1e-12 >= float(flatten_t):
            break
        if exe is not None and not bool(exe[i]):
            continue
        age = float(fresh[i]) if np.isfinite(fresh[i]) else None
        if not fresh_ok(age):
            continue
        a = float(ask[i])
        q = float(qty[i])
        if a == a and a > 0 and q == q and q > 0:
            return a
    return None


def first_mid_after_np(board: dict[str, np.ndarray], *, t_start: float, flatten_t: float) -> Optional[float]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return None
    i0 = int(np.searchsorted(t, float(t_start), side="left"))
    exe = board.get("continuous")
    if exe is None:
        exe = board.get("executable")
    for i in range(i0, int(t.size)):
        if float(t[i]) + 1e-12 >= float(flatten_t):
            break
        if exe is not None and not bool(exe[i]):
            continue
        if board["special"][i]:
            continue
        ba = float(board["bid_fresh_sec"][i]) if np.isfinite(board["bid_fresh_sec"][i]) else None
        aa = float(board["ask_fresh_sec"][i]) if np.isfinite(board["ask_fresh_sec"][i]) else None
        if not fresh_ok(ba) or not fresh_ok(aa):
            continue
        bid = float(board["bid"][i])
        ask = float(board["ask"][i])
        bq = float(board["bid_qty"][i])
        aq = float(board["ask_qty"][i])
        if bid == bid and ask == ask and bid > 0 and ask > 0 and bq == bq and aq == aq and bq > 0 and aq > 0:
            return (bid + ask) / 2.0
    return None


def short_path_outcomes(
    *,
    otu_t: list[float],
    otu_px: list[float],
    entry: float,
    t: float,
    horizon: float,
    flatten_t: float,
) -> dict[str, Any]:
    from research.post_open_causal_upside_mechanism_discovery_v1.features import _slice_window

    end = min(float(t) + float(horizon), float(flatten_t))
    lo, hi = _slice_window(otu_t, t, end)
    mfe = None
    mae = None
    leak = 0
    n = 0
    for i in range(lo, hi):
        ti = float(otu_t[i])
        if ti <= float(t) + 1e-12:
            continue
        if ti > end + 1e-12:
            leak += 1
            continue
        n += 1
        bps = _bps(float(entry) - float(otu_px[i]), float(entry))
        if bps is None:
            continue
        mfe = bps if mfe is None else max(mfe, bps)
        mae = bps if mae is None else min(mae, bps)
    edge = None if (mfe is None or mae is None) else float(mfe) - abs(float(mae))
    return {"mfe": mfe, "mae": mae, "edge": edge, "n": n, "leak": leak}


def simulate_short(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    sess_end: float,
    flatten_t: float,
    otu_t: list[float],
    otu_px: list[float],
    bid0: Optional[float],
    ask0: Optional[float],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "SHORT_W5_FILLED": False,
        "repricing": False,
        "chase": False,
        "fallback_bid": False,
        "synthetic_fill": False,
    }
    mid_t = ((float(bid0) + float(ask0)) / 2.0) if (bid0 is not None and ask0 is not None and float(bid0) > 0 and float(ask0) > 0) else None
    out["MID_T"] = mid_t
    leak = 0
    if bid0 is not None and float(bid0) > 0:
        for hz in HORIZONS:
            ask_h = first_ask_after_np(board, t_start=float(t0) + float(hz), flatten_t=flatten_t)
            mark = _bps((float(bid0) - float(ask_h)) if ask_h is not None else None, float(bid0))
            key = {300.0: "5M", 600.0: "10M", 900.0: "15M"}[float(hz)]
            out[f"COVER_ASK_{key}"] = ask_h
            out[f"SHORT_EXEC_MARKOUT_{key}_BPS"] = mark
            if hz == PRIMARY_HORIZON_SEC:
                p = short_path_outcomes(
                    otu_t=otu_t,
                    otu_px=otu_px,
                    entry=float(bid0),
                    t=float(t0),
                    horizon=float(hz),
                    flatten_t=flatten_t,
                )
                leak += int(p["leak"])
                out["SHORT_MFE_10M_BPS"] = p["mfe"]
                out["SHORT_MAE_10M_BPS"] = p["mae"]
                out["SHORT_PATH_EDGE_10M_BPS"] = p["edge"]
                out["DOWN_DOMINANT_10M"] = bool(p["edge"] is not None and float(p["edge"]) > 0)
                out["SHORT_EXEC_POSITIVE_10M"] = bool(mark is not None and float(mark) > 0)
                out["SHORT_path_otu_n"] = p["n"]
    if mid_t is not None:
        mid10 = first_mid_after_np(board, t_start=float(t0) + float(PRIMARY_HORIZON_SEC), flatten_t=flatten_t)
        out["MID_10M"] = mid10
        out["MID_RETURN_10M_BPS"] = _bps((float(mid10) - float(mid_t)) if mid10 is not None else None, float(mid_t))
    limit = limit_ask_at_t0(board, float(t0))
    out["SHORT_W5_limit"] = float(limit) if limit is not None else None
    if limit is None:
        out["fill_reason"] = "INVALID_LIMIT"
        out["SHORT_future_leak_n"] = leak
        return out
    got = standalone_short_fill(
        board,
        t0=float(t0),
        wait_sec=float(W5_WAIT_SEC),
        limit_price=float(limit),
        sess_end=float(sess_end),
    )
    filled = bool(got.get("WOULD_FILL"))
    fill_t = got.get("fill_t") if filled else None
    fill_px = got.get("fill_price") if filled else None
    out.update(
        {
            "SHORT_W5_FILLED": filled,
            "FILL_T": float(fill_t) if fill_t is not None else None,
            "FILL_PRICE": float(fill_px) if fill_px is not None else None,
            "TIME_TO_FILL_SEC": (float(fill_t) - float(t0)) if (filled and fill_t is not None) else None,
            "fill_reason": got.get("fill_reason"),
            "nonfill_class": got.get("nonfill_class"),
        }
    )
    if filled and fill_t is not None and fill_px is not None:
        cover = first_ask_after_np(board, t_start=float(fill_t) + float(PRIMARY_HORIZON_SEC), flatten_t=flatten_t)
        wmark = _bps((float(fill_px) - float(cover)) if cover is not None else None, float(fill_px))
        pw = short_path_outcomes(
            otu_t=otu_t,
            otu_px=otu_px,
            entry=float(fill_px),
            t=float(fill_t),
            horizon=PRIMARY_HORIZON_SEC,
            flatten_t=flatten_t,
        )
        leak += int(pw["leak"])
        out["W5_COVER_ASK_10M"] = cover
        out["SHORT_W5_MARKOUT_10M_BPS"] = wmark
        out["SHORT_W5_MFE_10M_BPS"] = pw["mfe"]
        out["SHORT_W5_MAE_10M_BPS"] = pw["mae"]
        out["SHORT_W5_PATH_EDGE_10M_BPS"] = pw["edge"]
        out["SHORT_W5_DOWN_DOMINANT_10M"] = bool(pw["edge"] is not None and float(pw["edge"]) > 0)
        out["SHORT_W5_EXEC_POSITIVE_10M"] = bool(wmark is not None and float(wmark) > 0)
    out["SHORT_future_leak_n"] = leak
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
                print(f"{day} SHORT events={events_n}", flush=True)
        views = {s: tapes[s].view() for s in universe}
        overlays: dict[str, dict[str, Any]] = {}
        leak = 0
        for r in parent_rows:
            if str(r.get("date")) != day:
                continue
            s = str(r.get("symbol") or "")
            hm = str(r.get("anchor_hm") or "")
            if not r.get("executable"):
                overlays[day_overlay_key(s, hm)] = {"SHORT_W5_FILLED": False, "fill_reason": "NOT_EXECUTABLE_ANCHOR"}
                continue
            got = simulate_short(
                views.get(s) or {},
                t0=float(r["t"]),
                sess_end=sess_end,
                flatten_t=flatten_t,
                otu_t=otu_t.get(s) or [],
                otu_px=otu_px.get(s) or [],
                bid0=_f(r.get("BID1_AT_T")),
                ask0=_f(r.get("ASK1_AT_T")),
            )
            leak += int(got.get("SHORT_future_leak_n") or 0)
            overlays[day_overlay_key(s, hm)] = got
        ok = leak == 0
        print(f"{day} SHORT OVERLAY events={events_n} n={len(overlays)} leak={leak}", flush=True)
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


def overlay_short(parent_rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        cache = CACHE / f"SHORT_{day}.pkl.gz"
        saved = _load_gz(cache)
        if saved.get("ok") and saved.get("schema") == CACHE_SCHEMA and str(saved.get("date") or "") == day:
            for k, v in dict(saved.get("overlays") or {}).items():
                overlays[join_overlay_key(day, *str(k).split("|", 1))] = v
            leak += int(saved.get("future_leak_n") or 0)
            print(f"cache-hit SHORT {day}", flush=True)
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
    for r in parent_rows:
        key = join_overlay_key(str(r.get("date") or ""), str(r.get("symbol") or ""), str(r.get("anchor_hm") or ""))
        over = overlays.get(key)
        rec = dict(r)
        if over is None:
            miss += 1
            rec["SHORT_W5_FILLED"] = False
            rec["SHORT_MISSING_OVERLAY"] = True
        else:
            rec.update(over)
            rec["SHORT_MISSING_OVERLAY"] = False
        joined.append(rec)
    ok = leak == 0 and miss == 0
    return {
        "ok": ok,
        "rows": joined,
        "future_leak_n": leak,
        "overlay_miss_n": miss,
        "days": list(DEVELOPMENT_DAYS),
        "blocker": None if ok else ("FUTURE_LEAK" if leak else "OVERLAY_MISS"),
    }


def load_parent_population() -> dict[str, Any]:
    return harvest_upside()
