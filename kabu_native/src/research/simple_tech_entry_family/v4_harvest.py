"""V4: PRE_VOLUME s4 rows + V3-identical Ask markout + VQ1–VQ4. No C14. No ENTRY change."""
from __future__ import annotations

import gc
import os
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

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family.harvest import _Buf, _board_row
from research.simple_tech_entry_family.v3_harvest import markout_row
from research.simple_tech_entry_family.v4_spec import EPSILON_EFFICIENCY
from small_paper.v1r_live_dual_lane import session_end_for_position

CACHE = NATIVE / "results" / "research" / "simple_tech_entry_family" / "_work"
V4_CACHE = CACHE / "v4_volume_quality_rca"
CONTINUOUS_STATES = {"CONTINUOUS_TRADING", "LEGACY_QUOTE_ONLY"}


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _cum_at(t: np.ndarray, vol: np.ndarray, tq: float) -> Optional[float]:
    if int(t.size) == 0:
        return None
    i = int(np.searchsorted(t, float(tq), side="right") - 1)
    if i < 0:
        return None
    v = float(vol[i])
    if not (v == v):
        return None
    return v


def _px_at(t: np.ndarray, px: np.ndarray, exe: np.ndarray, tq: float) -> Optional[float]:
    if int(t.size) == 0:
        return None
    i = int(np.searchsorted(t, float(tq), side="right") - 1)
    for j in range(i, -1, -1):
        if not bool(exe[j]):
            continue
        p = float(px[j])
        if p == p and p > 0:
            return p
    return None


def volume_persistence_300s(t: np.ndarray, vol: np.ndarray, t0: float) -> Optional[float]:
    """e1_x14 volume_persistence_300s: fraction of 10s steps with positive cum-vol delta."""
    hits = 0
    known = 0
    for s in range(10, 301, 10):
        later = _cum_at(t, vol, float(t0) - float(s - 10))
        earlier = _cum_at(t, vol, float(t0) - float(s))
        if later is None or earlier is None:
            continue
        known += 1
        if float(later) + 1e-12 < float(earlier):
            continue
        if float(later) > float(earlier) + 1e-12:
            hits += 1
    if known <= 0:
        return None
    return float(hits) / float(known)


def _window_volume(t: np.ndarray, vol: np.ndarray, t_lo: float, t_hi: float) -> Optional[float]:
    a = _cum_at(t, vol, float(t_lo))
    b = _cum_at(t, vol, float(t_hi))
    if a is None or b is None:
        return None
    if float(b) + 1e-12 < float(a):
        return None
    return float(b) - float(a)


def directional_volume_60(board: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    t = board["t"]
    px = board["px"]
    bid = board["bid"]
    ask = board["ask"]
    vol = board["cum_vol"]
    exe = board["executable"]
    spec = board["special"]
    state = board["board_execution_state"]
    t_lo = float(t0) - 60.0
    i0 = int(np.searchsorted(t, t_lo, side="right"))
    i1 = int(np.searchsorted(t, float(t0), side="right"))
    buy = sell = neu = total = 0.0
    classified = 0.0
    n_inc = 0
    prev_cum: Optional[float] = None
    prev_px: Optional[float] = None
    j_seed = i0 - 1
    if j_seed >= 0:
        cv = float(vol[j_seed])
        if cv == cv:
            prev_cum = cv
        p0 = float(px[j_seed])
        if p0 == p0 and p0 > 0:
            prev_px = p0
    for i in range(max(0, i0), i1):
        ti = float(t[i])
        if ti <= t_lo + 1e-12 or ti > float(t0) + 1e-12:
            continue
        cv = float(vol[i])
        p = float(px[i])
        if p == p and p > 0:
            last_trade = p
        else:
            last_trade = None
        if cv == cv and prev_cum is not None and cv > prev_cum + 1e-12:
            delta = cv - prev_cum
            total += delta
            n_inc += 1
            side = None
            if last_trade is not None and bool(exe[i]) and not bool(spec[i]) and str(state[i] or "") in CONTINUOUS_STATES:
                av = float(ask[i])
                bv = float(bid[i])
                ask_ok = av == av and av > 0
                bid_ok = bv == bv and bv > 0
                if ask_ok and last_trade + 1e-12 >= av:
                    side = "BUY"
                elif bid_ok and last_trade - 1e-12 <= bv:
                    side = "SELL"
                elif ask_ok and bid_ok and bv < last_trade < av:
                    if prev_px is None:
                        side = "NEUTRAL"
                    elif last_trade > prev_px + 1e-12:
                        side = "BUY"
                    elif last_trade < prev_px - 1e-12:
                        side = "SELL"
                    else:
                        side = "NEUTRAL"
            if side == "BUY":
                buy += delta
                classified += delta
            elif side == "SELL":
                sell += delta
                classified += delta
            elif side == "NEUTRAL":
                neu += delta
                classified += delta
            prev_cum = cv
        elif cv == cv:
            prev_cum = cv
        if last_trade is not None:
            prev_px = last_trade
    ratio = None
    den = buy + sell
    if den > 1e-12:
        ratio = (buy - sell) / den
    cov = (classified / total) if total > 1e-12 else None
    return {
        "BUY_VOLUME_60": buy,
        "SELL_VOLUME_60": sell,
        "NEUTRAL_VOLUME_60": neu,
        "TOTAL_VOLUME_DELTA_60": total,
        "DIRECTIONAL_VOLUME_RATIO_60": ratio,
        "VQ3_COVERAGE": cov,
        "VQ3_INCREMENT_N": n_inc,
    }


def price_response_60(board: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    t = board["t"]
    vol = board["cum_vol"]
    px = board["px"]
    exe = board["executable"]
    p1 = _px_at(t, px, exe, float(t0))
    p0 = _px_at(t, px, exe, float(t0) - 60.0)
    ret = None
    if p1 is not None and p0 is not None and p0 > 0:
        ret = (float(p1) / float(p0) - 1.0) * 10000.0
    vol60 = _window_volume(t, vol, float(t0) - 60.0, float(t0))
    priors = []
    for k in range(1, 6):
        v = _window_volume(t, vol, float(t0) - float(k + 1) * 60.0, float(t0) - float(k) * 60.0)
        if v is not None and v >= 0:
            priors.append(v)
    med = float(np.median(priors)) if len(priors) == 5 else None
    norm = None
    if vol60 is not None and med is not None and med > 0:
        norm = float(vol60) / float(med)
    elif vol60 is not None and med is not None and med == 0 and vol60 == 0:
        norm = None
    eff = None
    if ret is not None and norm is not None:
        eff = float(ret) / max(float(norm), float(EPSILON_EFFICIENCY))
    return {
        "RET_60_BPS": ret,
        "VOLUME_60": vol60,
        "PRIOR5_60S_MEDIAN": med,
        "NORMALIZED_VOLUME_60": norm,
        "PRICE_RESPONSE_EFFICIENCY": eff,
        "VQ4_PRIOR_N": len(priors),
    }


def attach_vq(row: dict[str, Any], board: dict[str, np.ndarray], *, t0: float, vol_accel: Any) -> dict[str, Any]:
    t = board["t"]
    vol = board["cum_vol"]
    row["VQ1"] = float(vol_accel) if _finite(vol_accel) else None
    row["VQ2"] = volume_persistence_300s(t, vol, t0)
    row["VQ2_AVAILABLE"] = row["VQ2"] is not None
    d = directional_volume_60(board, t0)
    row.update(d)
    row["VQ3"] = d.get("DIRECTIONAL_VOLUME_RATIO_60")
    p = price_response_60(board, t0)
    row.update(p)
    row["VQ4"] = p.get("PRICE_RESPONSE_EFFICIENCY")
    return row


def taxonomy(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    vq1 = row.get("VQ1")
    vq2 = row.get("VQ2")
    ratio = row.get("VQ3")
    ret = row.get("RET_60_BPS")
    high = _finite(vq1) and float(vq1) >= 1.5
    if (not _finite(vq1)) or (float(vq1) < 1.5):
        labels.append("VM1_LOW_ACTIVITY")
    if high and _finite(ratio) and float(ratio) > 0:
        labels.append("VM2_HIGH_VOLUME_BUY_DOMINANT")
    if high and _finite(ratio) and float(ratio) < 0:
        labels.append("VM3_HIGH_VOLUME_SELL_DOMINANT")
    if high and (not _finite(ratio) or abs(float(ratio)) <= 1e-12):
        labels.append("VM4_HIGH_VOLUME_NEUTRAL")
    if _finite(vq2) and float(vq2) >= 0.5 and _finite(ratio) and float(ratio) > 0:
        labels.append("VM5_PERSISTENT_BUYING")
    if high and (not _finite(ret) or float(ret) <= 0):
        labels.append("VM6_VOLUME_SPIKE_NO_PRICE_RESPONSE")
    if _finite(ratio) and float(ratio) > 0 and _finite(ret) and float(ret) > 0:
        labels.append("VM7_BUY_VOLUME_POSITIVE_PRICE_RESPONSE")
    return labels


def process_v4_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        cums: dict[str, list[float]] = {s: [] for s in needed}
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
            cv = row.get("cum_vol")
            cums[sym].append(float(cv) if _finite(cv) else float("nan"))
            if not row["executable"]:
                st = str(row.get("state") or "")
                if "ITAYOSE" in st or "PREOPEN" in st or "NOT_OPENED" in st:
                    leak["ITAYOSE_SKIP_N"] += 1
                elif "SPECIAL" in st:
                    leak["SPECIAL_SKIP_N"] += 1
                else:
                    leak["INVALID_SKIP_N"] += 1
            if events_n % 200000 == 0:
                print(f"{day} v4 stream kept={events_n} last_et={last_et}", flush=True)

        rows = []
        views: dict[str, dict[str, np.ndarray]] = {}
        for s in needed:
            v = bufs[s].view()
            v["cum_vol"] = np.asarray(cums[s], dtype=float)
            views[s] = v
        for sig in cands:
            s = _bare(sig.get("symbol"))
            board = views[s]
            rec = markout_row(sig, board, am_end=am_end)
            rec["s4"] = True
            rec["s5"] = bool(sig.get("s5"))
            rec["s6"] = bool(sig.get("s6"))
            rec["s7"] = bool(sig.get("s7"))
            rec["v1_signal"] = bool(sig.get("s7"))
            attach_vq(rec, board, t0=float(sig["t0"]), vol_accel=sig.get("vol_accel"))
            rec["taxonomy"] = taxonomy(rec)
            rec["taxonomy_join"] = "|".join(rec["taxonomy"])
            rows.append(rec)
        print(f"{day} v4 kept_events={events_n} pre_volume={len(rows)} last_et={last_et}", flush=True)
        del bufs, views, cums
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


def save_v4_day_cache(path: Path, body: dict[str, Any]) -> None:
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
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")
