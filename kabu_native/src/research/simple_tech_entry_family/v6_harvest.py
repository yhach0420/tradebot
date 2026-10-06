"""V6: all evaluable s0 rows + V3-identical Ask markout + TQ/PQ. No C14. No ENTRY change."""
from __future__ import annotations

import gc
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family import EMA_SLOPE_BARS, PULLBACK_LOOKBACK
from research.simple_tech_entry_family.harvest import CACHE, _Buf, _board_row
from research.simple_tech_entry_family.v3_harvest import markout_row
from small_paper.v1r_live_dual_lane import session_end_for_position

V6_CACHE = CACHE / "v6_trend_pullback_stage_rca"
SLIM_KEYS = (
    "date",
    "symbol",
    "t0",
    "s1",
    "s2",
    "executable_signal",
    "markout_60",
    "markout_180",
    "markout_300",
    "mfe_bps",
    "mae_bps",
    "TQ1",
    "TQ2",
    "TQ3",
    "TQ4",
    "PQ1",
    "PQ2",
    "PQ3",
    "PQ4",
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _bps_rel(num: Any, den: Any) -> Optional[float]:
    if not _finite(num) or not _finite(den):
        return None
    d = float(den)
    if abs(d) <= 1e-12:
        return None
    return (float(num) / d - 1.0) * 10000.0


def _pctb(px: Any, lower: Any, upper: Any) -> Optional[float]:
    if not _finite(px) or not _finite(lower) or not _finite(upper):
        return None
    w = float(upper) - float(lower)
    if abs(w) <= 1e-12:
        return None
    return (float(px) - float(lower)) / w


def attach_tq_pq_day(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in opps:
        s = _bare(r.get("symbol"))
        if s:
            by[s].append(r)
    lag = int(EMA_SLOPE_BARS)
    pb = int(PULLBACK_LOOKBACK)
    for xs in by.values():
        xs.sort(key=lambda r: int(r.get("i") or 0))
        ema9 = [float(r["ema9"]) if _finite(r.get("ema9")) else None for r in xs]
        ema21 = [float(r["ema21"]) if _finite(r.get("ema21")) else None for r in xs]
        lows = [float(r["low"]) if _finite(r.get("low")) else None for r in xs]
        closes = [float(r["close"]) if _finite(r.get("close")) else None for r in xs]
        up = [float(r["bb_upper"]) if _finite(r.get("bb_upper")) else None for r in xs]
        lo_bb = [float(r["bb_lower"]) if _finite(r.get("bb_lower")) else None for r in xs]
        gap = [_bps_rel(a, b) for a, b in zip(ema9, ema21)]
        for k, r in enumerate(xs):
            tq1 = gap[k]
            e21_lag = ema21[k - lag] if k >= lag else (float(r["ema21_lag3"]) if _finite(r.get("ema21_lag3")) else None)
            e9_lag = ema9[k - lag] if k >= lag else None
            tq2 = _bps_rel(ema21[k], e21_lag)
            tq3 = _bps_rel(ema9[k], e9_lag)
            tq4 = (float(tq1) - float(gap[k - lag])) if tq1 is not None and k >= lag and gap[k - lag] is not None else None
            dist = []
            pct_low = []
            for j in range(max(0, k - pb + 1), k + 1):
                dist.append(_bps_rel(lows[j], ema9[j]))
                pct_low.append(_pctb(lows[j], lo_bb[j], up[j]))
            finite_dist = [float(x) for x in dist if x is not None]
            finite_pct = [float(x) for x in pct_low if x is not None]
            pq1 = min(finite_dist) if finite_dist else None
            pq2 = min(finite_pct) if finite_pct else None
            pct_now = _pctb(closes[k], lo_bb[k], up[k])
            pq3 = (float(pct_now) - float(pq2)) if pct_now is not None and pq2 is not None else None
            recency = None
            for back in range(0, k + 1):
                j = k - back
                if lows[j] is None or ema9[j] is None:
                    continue
                if float(lows[j]) <= float(ema9[j]) + 1e-12:
                    recency = back
                    break
            r["TQ1"] = tq1
            r["TQ2"] = tq2
            r["TQ3"] = tq3
            r["TQ4"] = tq4
            r["PQ1"] = pq1
            r["PQ2"] = pq2
            r["PQ3"] = pq3
            r["PQ4"] = recency
    return opps


def process_v6_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    cands = list(payload.get("candidates") or [])
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "C14_REPLAY_N": 0,
        "EXIT_SIM_N": 0,
        "FUTURE_ASK_USE_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "ENTRY_RULE_CHANGE_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "PERSISTENCE_AND_N": 0,
        "RCI_CHANGE_N": 0,
    }
    if not cands:
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "events_n": 0,
            "rows": [],
            "leak": leak,
            "elapsed_sec": 0.0,
            "blocker": None,
        }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        needed = {_bare(r.get("symbol")) for r in cands if _bare(r.get("symbol"))}
        bufs: dict[str, _Buf] = {s: _Buf() for s in needed}
        events_n = 0
        last_et: Optional[float] = None
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in needed:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            if float(et) < am_start - 120.0:
                continue
            if float(et) > am_end + 2.0:
                continue
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            last_et = float(et)
            events_n += 1
            row = _board_row(pay, float(et))
            bufs[sym].append(row)
            if not row["executable"]:
                st = str(row.get("state") or "")
                if "ITAYOSE" in st or "PREOPEN" in st or "NOT_OPENED" in st:
                    leak["ITAYOSE_SKIP_N"] += 1
                elif "SPECIAL" in st:
                    leak["SPECIAL_SKIP_N"] += 1
                else:
                    leak["INVALID_SKIP_N"] += 1
            if events_n % 400000 == 0:
                print(f"{day} v6 stream kept={events_n} last_et={last_et}", flush=True)

        rows = []
        views = {s: bufs[s].view() for s in needed}
        copy_keys = ("s1", "s2", "TQ1", "TQ2", "TQ3", "TQ4", "PQ1", "PQ2", "PQ3", "PQ4")
        for sig in cands:
            s = _bare(sig.get("symbol"))
            if s and s in views:
                rec = markout_row(sig, views[s], am_end=am_end)
            else:
                rec = {k: None for k in SLIM_KEYS}
                rec["date"] = sig.get("date")
                rec["symbol"] = s
                rec["t0"] = sig.get("t0")
                rec["executable_signal"] = False
            for k in copy_keys:
                rec[k] = sig.get(k)
            rows.append({k: rec.get(k) for k in SLIM_KEYS})
        print(f"{day} v6 kept_events={events_n} pre_trend={len(rows)} last_et={last_et}", flush=True)
        del bufs, views
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "events_n": events_n,
            "last_et": last_et,
            "rows": rows,
            "leak": leak,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def save_v6_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "leak": body.get("leak"),
            "elapsed_sec": body.get("elapsed_sec"),
            "rows": body.get("rows"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")
