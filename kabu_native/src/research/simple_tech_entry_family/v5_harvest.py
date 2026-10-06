"""V5: PRE_REVERSAL s2 rows + V3-identical Ask markout + RQ1–RQ4. No C14. No ENTRY change."""
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
from research.simple_tech_entry_family import RCI_CROSS_LEVEL
from research.simple_tech_entry_family.harvest import CACHE, _Buf, _board_row
from research.simple_tech_entry_family.v3_harvest import markout_row
from research.simple_tech_entry_family.v5_spec import (
    R1_WEAK_CROSS_MAX,
    R2_RECOVERY_FRACTION_MIN,
    R3_FAST_DELTA_MIN,
    RQ4_LOOKBACK_BARS,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V5_CACHE = CACHE / "v5_reversal_quality_rca"


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _recovery_fraction(rci: list[Optional[float]], k: int, lookback: int) -> Optional[float]:
    lo = max(0, int(k) - int(lookback))
    found: Optional[float] = None
    for j in range(int(k) - 1, lo, -1):
        if j - 1 < 0 or j + 1 > int(k):
            continue
        a, b, c = rci[j - 1], rci[j], rci[j + 1]
        if a is None or b is None or c is None:
            continue
        if float(b) <= float(a) + 1e-12 and float(b) <= float(c) + 1e-12:
            found = float(b)
            break
    if found is None:
        window = [float(x) for x in rci[lo : int(k) + 1] if x is not None]
        if not window:
            return None
        found = float(min(window))
    cur = rci[k]
    if cur is None or found is None or float(found) >= -1e-12:
        return None
    return (float(cur) - float(found)) / (0.0 - float(found))


def taxonomy(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    rq1 = row.get("RQ1")
    rq2 = row.get("RQ2")
    rq3 = row.get("RQ3")
    rq4 = row.get("RQ4")
    cross = bool(row.get("rci_cross"))
    if cross and _finite(rq1) and float(RCI_CROSS_LEVEL) < float(rq1) <= float(R1_WEAK_CROSS_MAX) + 1e-12:
        labels.append("R1_WEAK_CROSS")
    if _finite(rq4) and float(rq4) >= float(R2_RECOVERY_FRACTION_MIN) - 1e-12:
        labels.append("R2_STRONG_RCI_RECOVERY")
    if _finite(rq2) and float(rq2) >= float(R3_FAST_DELTA_MIN) - 1e-12:
        labels.append("R3_FAST_RCI_REVERSAL")
    prev_d = (float(rq3) - float(rq2)) if _finite(rq3) and _finite(rq2) else None
    if _finite(rq2) and float(rq2) > 1e-12 and (prev_d is None or float(prev_d) <= 1e-12):
        labels.append("R4_SINGLE_BAR_SPIKE")
    if _finite(rq2) and float(rq2) > 1e-12 and prev_d is not None and float(prev_d) > 1e-12:
        labels.append("R5_MULTI_BAR_RCI_RECOVERY")
    recovered = (_finite(rq4) and float(rq4) >= float(R2_RECOVERY_FRACTION_MIN) - 1e-12) or (_finite(rq2) and float(rq2) > 1e-12)
    if recovered and not bool(row.get("price_follow")):
        labels.append("R6_RCI_RECOVERED_BUT_PRICE_NOT_FOLLOWING")
    return labels


def attach_rq_day(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in opps:
        s = _bare(r.get("symbol"))
        if s:
            by[s].append(r)
    out: list[dict[str, Any]] = []
    for xs in by.values():
        xs = sorted(xs, key=lambda r: int(r.get("i") or 0))
        rci: list[Optional[float]] = []
        highs: list[Optional[float]] = []
        for r in xs:
            rci.append(float(r["rci9"]) if _finite(r.get("rci9")) else None)
            highs.append(float(r["high"]) if _finite(r.get("high")) else None)
        for k, r in enumerate(xs):
            if not r.get("s2"):
                continue
            rq1 = rci[k]
            prev = rci[k - 1] if k >= 1 else (float(r["rci9_prev"]) if _finite(r.get("rci9_prev")) else None)
            tm2 = rci[k - 2] if k >= 2 else None
            hi_prev = highs[k - 1] if k >= 1 else None
            close = float(r["close"]) if _finite(r.get("close")) else None
            ema9 = float(r["ema9"]) if _finite(r.get("ema9")) else None
            price_follow = bool(
                close is not None
                and ema9 is not None
                and hi_prev is not None
                and float(close) > float(ema9)
                and float(close) > float(hi_prev)
            )
            rec = {
                "date": r.get("date"),
                "session": "AM",
                "symbol": _bare(r.get("symbol")),
                "i": r.get("i"),
                "t0": r.get("t0"),
                "bar_minute": r.get("bar_minute"),
                "rci9": rq1,
                "rci9_prev": prev,
                "RQ1": rq1,
                "RQ2": (float(rq1) - float(prev)) if rq1 is not None and prev is not None else None,
                "RQ3": (float(rq1) - float(tm2)) if rq1 is not None and tm2 is not None else None,
                "RQ4": _recovery_fraction(rci, k, int(RQ4_LOOKBACK_BARS)),
                "rci_cross": bool(r.get("s3")),
                "s2": True,
                "s3_v1": bool(r.get("s3")),
                "s4": bool(r.get("s4")),
                "s5": bool(r.get("s5")),
                "s6": bool(r.get("s6")),
                "s7": bool(r.get("s7")),
                "v1_signal": bool(r.get("s7")),
                "WOULD_FILL": r.get("WOULD_FILL"),
                "close": close,
                "ema9": ema9,
                "ema21": r.get("ema21"),
                "high": r.get("high"),
                "high_prev": hi_prev,
                "bb_upper": r.get("bb_upper"),
                "volume": r.get("volume"),
                "vol_accel": r.get("vol_accel"),
                "fwd_3m": r.get("fwd_3m"),
                "mfe_5m": r.get("mfe_5m"),
                "up_first": r.get("up_first"),
                "cost_exceed": r.get("cost_exceed"),
                "price_follow": price_follow,
            }
            rec["taxonomy"] = taxonomy(rec)
            rec["taxonomy_join"] = "|".join(rec["taxonomy"])
            out.append(rec)
    return out


def process_v5_day(payload: dict[str, Any]) -> dict[str, Any]:
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
            if events_n % 200000 == 0:
                print(f"{day} v5 stream kept={events_n} last_et={last_et}", flush=True)

        rows = []
        views = {s: bufs[s].view() for s in needed}
        copy_keys = (
            "RQ1", "RQ2", "RQ3", "RQ4", "rci_cross", "s2", "s3_v1", "s4", "s5", "s6", "s7",
            "rci9_prev", "high_prev", "price_follow", "taxonomy", "taxonomy_join", "i", "v1_signal",
        )
        for sig in cands:
            s = _bare(sig.get("symbol"))
            rec = markout_row(sig, views[s], am_end=am_end)
            for k in copy_keys:
                rec[k] = sig.get(k)
            rows.append(rec)
        print(f"{day} v5 kept_events={events_n} pre_reversal={len(rows)} last_et={last_et}", flush=True)
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


def save_v5_day_cache(path: Path, body: dict[str, Any]) -> None:
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
