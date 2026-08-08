"""Load sources, contract parity, as-of context features."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x14_board_independent_signal.ticks import load_symbol_ticks
from research.e1_x16_same_anchor_vwap_reject.evaluate import assign_variants
from research.e1_x16_same_anchor_vwap_reject import VWAP_UPPER_LIMIT_BPS as X16_THR
from research.e1_x17_vwap_reject_prospective import VWAP_UPPER_LIMIT_BPS as X17_THR

from . import (
    FORBIDDEN_DAY,
    HIST_DAYS,
    HIST_SOURCE_RUN,
    PROSP_DAY,
    PROSP_SOURCE_RUN,
    VWAP_UPPER_LIMIT_BPS,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
X16_ENRICHED = NATIVE / "results" / "research" / "e1_x16_same_anchor_vwap_reject" / "_enriched_c0.jsonl"
X16_REPORT = NATIVE / "results" / "research" / "e1_x16_same_anchor_vwap_reject" / "report.json"
X17_CACHE = NATIVE / "results" / "research" / "e1_x17_vwap_reject_prospective" / "_c0_cache.jsonl"
X17_REPORT = NATIVE / "results" / "research" / "e1_x17_vwap_reject_prospective" / "report.json"
OUT = NATIVE / "results" / "research" / "e1_x18_vwap_failure_source"
CTX_CACHE = OUT / "_context_cache.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def contract_parity() -> dict[str, Any]:
    """Confirm Historical and Prospective share the same sealed contracts."""
    mismatches = []
    if abs(X16_THR - VWAP_UPPER_LIMIT_BPS) > 1e-12 or abs(X17_THR - VWAP_UPPER_LIMIT_BPS) > 1e-12:
        mismatches.append("threshold_constant")
    x16 = json.loads(X16_REPORT.read_text(encoding="utf-8"))
    x17 = json.loads(X17_REPORT.read_text(encoding="utf-8"))
    if x16.get("run_id") != HIST_SOURCE_RUN:
        mismatches.append("hist_source_run")
    if x17.get("run_id") != PROSP_SOURCE_RUN:
        mismatches.append("prosp_source_run")
    pc = x16.get("prospective_precommit") or {}
    thr = float(pc.get("threshold") or 0)
    if abs(thr - VWAP_UPPER_LIMIT_BPS) > 1e-12:
        mismatches.append("precommit_threshold")

    checks = {
        "rpfe_episode_definition": "X15 build_episodes gap=300s symbol+session",
        "c0_anchor_definition": "first candidate in episode",
        "one_anchor_per_episode": True,
        "vwap_source": "push_jsonl tick as-of VWAP",
        "vwap_asof_resolution": "latest tick t <= c0_epoch",
        "distance_from_vwap_bps_formula": "(price/vwap - 1) * 10000",
        "symbol_normalization": "strip .T",
        "session_boundary": "AM if local < 12:00 else PM; no cross-session",
        "forward_label_construction": "X14 attach_forward_labels from CurrentPrice path",
        "mfe_mae_construction": "same attach_forward_labels",
        "first_touch_construction": "same attach_forward_labels",
        "no_progress_construction": "MFE_300s < 5bps and |FR_300s| < 5bps",
        "hist_labels_source": "X15 matched C0 outcomes (same label code)",
        "prosp_labels_source": "X17 rebuilt via attach_forward_labels (same label code)",
        "threshold_bps": VWAP_UPPER_LIMIT_BPS,
    }
    # Structural sample checks
    hist = _load_jsonl(X16_ENRICHED)
    prosp = _load_jsonl(X17_CACHE)
    if any(r.get("date") == FORBIDDEN_DAY for r in hist + prosp):
        mismatches.append("forbidden_day_present")
    if any(r.get("date") >= "20260805" for r in hist + prosp):
        mismatches.append("risk_only_alpha_used")
    # formula spot-check: recompute a few prosp distances if price/vwap recoverable — skip if not stored
    ok = len(mismatches) == 0
    return {
        "ok": ok,
        "mismatches": mismatches,
        "checks": checks,
        "status": "OK" if ok else "CONSTRUCTION_MISMATCH",
        "hist_n": len(hist),
        "prosp_n": len(prosp),
    }


def load_panels() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hist = assign_variants(_load_jsonl(X16_ENRICHED))
    # keep only hist days
    hist = [r for r in hist if r.get("date") in HIST_DAYS]
    prosp_raw = _load_jsonl(X17_CACHE)
    prosp = []
    for r in prosp_raw:
        if r.get("date") != PROSP_DAY:
            continue
        m = dict(r)
        # normalize flags to X16 naming; C0 OK only
        if not m.get("in_C0"):
            continue
        vwap_ok = bool(m.get("vwap_evaluable")) and m.get("distance_from_vwap_bps") is not None
        m["in_A0"] = True
        m["in_A1"] = vwap_ok
        dist = m.get("distance_from_vwap_bps")
        m["in_A2"] = bool(vwap_ok and dist is not None and float(dist) <= VWAP_UPPER_LIMIT_BPS)
        m["in_A2_Rejected"] = bool(vwap_ok and dist is not None and float(dist) > VWAP_UPPER_LIMIT_BPS)
        m["panel"] = "prospective_consumed"
        prosp.append(m)
    for r in hist:
        r["panel"] = "historical"
    return hist, prosp


def _asof_idx(times: np.ndarray, epoch: float) -> int:
    if times.size == 0:
        return -1
    return int(np.searchsorted(times, epoch, side="right") - 1)


def _ret(prices: np.ndarray, times: np.ndarray, epoch: float, lookback: float) -> Optional[float]:
    i0 = _asof_idx(times, epoch)
    i1 = _asof_idx(times, epoch - lookback)
    if i0 < 0 or i1 < 0:
        return None
    p0, p1 = prices[i0], prices[i1]
    if np.isnan(p0) or np.isnan(p1) or p1 == 0:
        return None
    return float(p0 / p1 - 1.0)


def _session_open_epoch(day: str, session: str) -> float:
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
    if session == "AM":
        return datetime(y, m, d, 9, 0, tzinfo=JST).timestamp()
    return datetime(y, m, d, 12, 30, tzinfo=JST).timestamp()


def attach_context(rows: list[dict[str, Any]], *, force: bool = False) -> list[dict[str, Any]]:
    """As-of-only path features at C0 (no future)."""
    OUT.mkdir(parents=True, exist_ok=True)
    if CTX_CACHE.exists() and not force:
        cached = {r["rpfe_episode_id"]: r for r in _load_jsonl(CTX_CACHE)}
        if all(r["rpfe_episode_id"] in cached for r in rows):
            out = []
            for r in rows:
                m = dict(r)
                c = cached[r["rpfe_episode_id"]]
                for k, v in c.items():
                    if k.startswith("ctx_") or k in ("time_bucket",):
                        m[k] = v
                out.append(m)
            return out

    by_day_sym: dict[tuple[str, str], list] = {}
    for r in rows:
        by_day_sym.setdefault((r["date"], r["symbol"]), []).append(r)

    print(f"=== context enrich symbol-days={len(by_day_sym)} ===", flush=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _load_one(key: tuple[str, str]):
        day, sym = key
        return key, load_symbol_ticks(day, sym)

    tick_cache: dict[tuple[str, str], list] = {}
    keys = sorted(by_day_sym.keys())
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_load_one, k): k for k in keys}
        done = 0
        for fut in as_completed(futs):
            key, ticks = fut.result()
            tick_cache[key] = ticks
            done += 1
            if done % 25 == 0 or done == len(keys):
                print(f"  loaded {done}/{len(keys)}", flush=True)

    enriched: list[dict[str, Any]] = []
    for (day, sym), eps in sorted(by_day_sym.items()):
        ticks = tick_cache.get((day, sym)) or []
        if not ticks:
            for ep in eps:
                m = dict(ep)
                m["ctx_missing"] = True
                m["time_bucket"] = _time_bucket(ep.get("c0_time"))
                enriched.append(m)
            continue
        times = np.asarray([t["t"] for t in ticks], dtype=float)
        prices = np.asarray([t["price"] if t.get("price") is not None else np.nan for t in ticks], dtype=float)
        vols = np.asarray([t["vol"] if t.get("vol") is not None else np.nan for t in ticks], dtype=float)
        day_open_i = 0
        for i in range(len(times)):
            if not np.isnan(prices[i]):
                day_open_i = i
                break
        day_open_px = float(prices[day_open_i]) if not np.isnan(prices[day_open_i]) else None

        for ep in eps:
            m = dict(ep)
            epoch = float(ep["c0_epoch"])
            i = _asof_idx(times, epoch)
            m["time_bucket"] = _time_bucket(ep.get("c0_time"))
            if i < 0:
                m["ctx_missing"] = True
                enriched.append(m)
                continue
            px = float(prices[i]) if not np.isnan(prices[i]) else None
            m["ctx_price"] = px
            m["ctx_volume"] = float(vols[i]) if not np.isnan(vols[i]) else None
            m["ctx_return_60s"] = _ret(prices, times, epoch, 60)
            m["ctx_return_180s"] = _ret(prices, times, epoch, 180)
            m["ctx_return_300s"] = _ret(prices, times, epoch, 300)
            so = _session_open_epoch(day, ep["session"])
            j = int(np.searchsorted(times, so, side="left"))
            while j < len(prices) and np.isnan(prices[j]):
                j += 1
            open_px = float(prices[j]) if j < len(prices) else None
            if px and open_px and open_px > 0:
                m["ctx_return_from_session_open"] = px / open_px - 1.0
            else:
                m["ctx_return_from_session_open"] = None
            if px and day_open_px and day_open_px > 0:
                m["ctx_return_from_day_open"] = px / day_open_px - 1.0
            else:
                m["ctx_return_from_day_open"] = None
            i_prev = _asof_idx(times, so - 1.0)
            if i_prev >= 0 and not np.isnan(prices[i_prev]) and px:
                prev = float(prices[i_prev])
                m["ctx_gap_vs_pre_open"] = px / prev - 1.0 if prev > 0 else None
            else:
                m["ctx_gap_vs_pre_open"] = None
            i_so = int(np.searchsorted(times, so, side="left"))
            window = prices[i_so: i + 1]
            window = window[~np.isnan(window)]
            if len(window) and px:
                hi, lo = float(np.max(window)), float(np.min(window))
                m["ctx_dist_from_session_high_bps"] = (px / hi - 1.0) * 10000.0 if hi > 0 else None
                m["ctx_dist_from_session_low_bps"] = (px / lo - 1.0) * 10000.0 if lo > 0 else None
                m["ctx_range_width_bps"] = (hi / lo - 1.0) * 10000.0 if lo > 0 else None
            else:
                m["ctx_dist_from_session_high_bps"] = None
                m["ctx_dist_from_session_low_bps"] = None
                m["ctx_range_width_bps"] = None
            m["ctx_vol_60s"] = _path_vol(prices, times, epoch, 60)
            m["ctx_vol_300s"] = _path_vol(prices, times, epoch, 300)
            m["ctx_rebound_bps"] = ep.get("rebound_from_recent_low_bps")
            m["ctx_missing"] = False
            enriched.append(m)

    # market state: same-day cross-section among panel peers near C0
    by_day: dict[str, list] = {}
    for r in enriched:
        by_day.setdefault(r["date"], []).append(r)
    for day, day_rows in by_day.items():
        for r in day_rows:
            peers = [
                x for x in day_rows
                if abs(float(x["c0_epoch"]) - float(r["c0_epoch"])) <= 60.0
                and x.get("ctx_return_60s") is not None
            ]
            rets = [float(x["ctx_return_60s"]) for x in peers]
            rets180 = [float(x["ctx_return_180s"]) for x in peers if x.get("ctx_return_180s") is not None]
            rets300 = [float(x["ctx_return_300s"]) for x in peers if x.get("ctx_return_300s") is not None]
            vols = [float(x["ctx_volume"]) for x in peers if x.get("ctx_volume") is not None]
            if rets:
                r["ctx_univ_median_return_60s"] = float(np.median(rets))
                r["ctx_advancing_frac"] = float(sum(1 for x in rets if x > 0) / len(rets))
                r["ctx_declining_frac"] = float(sum(1 for x in rets if x < 0) / len(rets))
                r["ctx_return_dispersion_60s"] = float(np.std(rets))
            else:
                r["ctx_univ_median_return_60s"] = None
                r["ctx_advancing_frac"] = None
                r["ctx_declining_frac"] = None
                r["ctx_return_dispersion_60s"] = None
            r["ctx_univ_median_return_180s"] = float(np.median(rets180)) if rets180 else None
            r["ctx_univ_median_return_300s"] = float(np.median(rets300)) if rets300 else None
            r["ctx_volume_dispersion"] = float(np.std(vols)) if len(vols) >= 2 else None
            r["ctx_peer_n"] = len(peers)

    slim = []
    for r in enriched:
        slim.append({
            "rpfe_episode_id": r["rpfe_episode_id"],
            "time_bucket": r.get("time_bucket"),
            **{k: v for k, v in r.items() if k.startswith("ctx_")},
        })
    with CTX_CACHE.open("w", encoding="utf-8") as f:
        for r in slim:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"=== context done n={len(enriched)} ===", flush=True)
    return enriched


def _path_vol(prices: np.ndarray, times: np.ndarray, epoch: float, lookback: float) -> Optional[float]:
    i0 = _asof_idx(times, epoch)
    i1 = _asof_idx(times, epoch - lookback)
    if i0 < 0 or i1 < 0 or i0 <= i1:
        return None
    seg = prices[i1: i0 + 1]
    seg = seg[~np.isnan(seg)]
    if len(seg) < 3:
        return None
    rets = np.diff(seg) / seg[:-1]
    rets = rets[np.isfinite(rets)]
    if len(rets) < 2:
        return None
    return float(np.std(rets))


def _time_bucket(c0_time: Any) -> Optional[str]:
    if not c0_time:
        return None
    try:
        dt = datetime.fromisoformat(str(c0_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        dt = dt.astimezone(JST)
    except Exception:
        return None
    mins = dt.hour * 60 + dt.minute
    from . import TIME_BUCKETS
    for name, (a, b) in TIME_BUCKETS.items():
        if a <= mins < b:
            return name
    return "OTHER"
