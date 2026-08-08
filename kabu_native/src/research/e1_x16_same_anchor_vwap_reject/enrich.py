"""Enrich C0 anchors with features at the same C0 epoch (no re-anchoring)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.e1_x14_board_independent_signal.ticks import load_symbol_ticks
from research.e1_x15_rpfe_incremental_entry.anchors import _rebound_diag

from . import DAYS, MIN_UNIVERSE

NATIVE = Path(__file__).resolve().parents[3]
X15_MATCHED = NATIVE / "results" / "research" / "e1_x15_rpfe_incremental_entry" / "_matched_cache.jsonl"
OUT = NATIVE / "results" / "research" / "e1_x16_same_anchor_vwap_reject"
CACHE = OUT / "_enriched_c0.jsonl"


def _prep(ticks: list[dict[str, Any]]) -> dict[str, Any]:
    if not ticks:
        return {"times": np.asarray([], dtype=float), "price": None, "vol": None, "vwap": None}
    times = np.asarray([t["t"] for t in ticks], dtype=float)
    price = np.asarray([t["price"] if t.get("price") is not None else np.nan for t in ticks], dtype=float)
    vol = np.asarray([t["vol"] if t.get("vol") is not None else np.nan for t in ticks], dtype=float)
    vwap = np.asarray([t["vwap"] if t.get("vwap") is not None else np.nan for t in ticks], dtype=float)
    return {"times": times, "price": price, "vol": vol, "vwap": vwap}


def _asof_idx(times: np.ndarray, epoch: float) -> int:
    if times.size == 0:
        return -1
    return int(np.searchsorted(times, epoch, side="right") - 1)


def _px_vwap(prep: dict[str, Any], epoch: float) -> tuple[Optional[float], Optional[float], Optional[float]]:
    times = prep["times"]
    i = _asof_idx(times, epoch)
    if i < 0:
        return None, None, None
    px = prep["price"][i]
    if np.isnan(px):
        return None, None, None
    age = epoch - float(times[i])
    vw = prep["vwap"][i]
    if np.isnan(vw):
        return float(px), None, age
    return float(px), float(vw), age


def _volume_delta_60s(prep: dict[str, Any], epoch: float) -> Optional[float]:
    times = prep["times"]
    vol = prep["vol"]
    i0 = _asof_idx(times, epoch)
    i1 = _asof_idx(times, epoch - 60.0)
    if i0 < 0 or i1 < 0:
        return None
    v0, v1 = vol[i0], vol[i1]
    if np.isnan(v0) or np.isnan(v1):
        return None
    if float(v0) + 1e-9 < float(v1):
        return None
    return float(v0) - float(v1)


def load_x15_matched() -> list[dict[str, Any]]:
    rows = []
    with X15_MATCHED.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def enrich_c0(*, force: bool = False) -> list[dict[str, Any]]:
    """
    Features at C0 epoch only (same anchor).

    VWAP distance + rebound: recomputed from push ticks for episode symbols.
    volume_percentile / universe_n: prefer X15 C0 grid values (same-time RS);
    if missing, compute episode-day relative volume rank among episode symbols
    only when that set size >= MIN_UNIVERSE (conservative; else leave None).
    """
    OUT.mkdir(parents=True, exist_ok=True)
    if CACHE.exists() and not force:
        return [json.loads(l) for l in CACHE.read_text(encoding="utf-8").splitlines() if l.strip()]

    matched = load_x15_matched()
    by_day: dict[str, list] = {}
    for m in matched:
        if m.get("c0_status") != "OK":
            continue
        by_day.setdefault(m["date"], []).append(m)

    enriched: list[dict[str, Any]] = []
    for day in DAYS:
        eps = by_day.get(day) or []
        if not eps:
            continue
        syms = sorted({ep["symbol"] for ep in eps})
        print(f"=== enrich {day} episodes={len(eps)} symbols={len(syms)} ===", flush=True)
        ticks_by: dict[str, list] = {}
        prep_by: dict[str, dict] = {}
        for sym in syms:
            ticks = load_symbol_ticks(day, sym)
            ticks_by[sym] = ticks
            prep_by[sym] = _prep(ticks)

        for ep in eps:
            sym = ep["symbol"]
            ticks = ticks_by.get(sym) or []
            prep = prep_by.get(sym) or _prep([])
            c0_epoch = float(ep["c0_epoch"])
            px, vwap, price_age = _px_vwap(prep, c0_epoch)
            if px is not None and vwap is not None and vwap != 0:
                dist = (px / vwap - 1.0) * 10000.0
                vwap_eval = True
            else:
                dist = None
                vwap_eval = False

            diag = _rebound_diag(ticks, c0_epoch) if ticks else {"ok": False}
            if diag.get("ok") and diag.get("rebound_bps") is not None:
                reb = float(diag["rebound_bps"])
                reb_eval = True
            else:
                reb = None
                reb_eval = False

            # Same-timestamp universe percentile from X15 C0 grid (full-day RS);
            # do not invent a smaller episode-only universe.
            volp = ep.get("c0_volume_percentile_60s")
            uni_n = int(ep["c0_rs_universe_n"]) if ep.get("c0_rs_universe_n") is not None else 0

            def out(k: str):
                return ep.get(f"c0_{k}")

            i = _asof_idx(prep["times"], c0_epoch)
            volume_age = (c0_epoch - float(prep["times"][i])) if i >= 0 else None
            vwap_age = volume_age if (i >= 0 and not np.isnan(prep["vwap"][i])) else None

            enriched.append({
                "rpfe_episode_id": ep["rpfe_episode_id"],
                "date": ep["date"],
                "symbol": ep["symbol"],
                "session": ep["session"],
                "c0_time": ep["c0_time"],
                "c0_epoch": c0_epoch,
                "c0_price": ep.get("c0_price") if ep.get("c0_price") is not None else px,
                "anchor_contract": "SAME_C0_NO_REANCHOR",
                "distance_from_vwap_bps": dist,
                "vwap_evaluable": vwap_eval,
                "rebound_from_recent_low_bps": reb,
                "rebound_evaluable": reb_eval,
                "volume_percentile_60s": volp,
                "rs_universe_n": uni_n or 0,
                "price_age_sec": price_age,
                "volume_age_sec": volume_age,
                "vwap_age_sec": vwap_age,
                "rebound_diag": diag,
                "forward_return_30s": out("forward_return_30s"),
                "forward_return_60s": out("forward_return_60s"),
                "forward_return_180s": out("forward_return_180s"),
                "forward_return_300s": out("forward_return_300s"),
                "MFE_60s": out("MFE_60s"), "MAE_60s": out("MAE_60s"),
                "MFE_180s": out("MFE_180s"), "MAE_180s": out("MAE_180s"),
                "MFE_300s": out("MFE_300s"), "MAE_300s": out("MAE_300s"),
                "plus5_before_minus5": out("plus5_before_minus5"),
                "plus5_before_minus10": out("plus5_before_minus10"),
                "plus10_before_minus10": out("plus10_before_minus10"),
                "plus10_before_minus15": out("plus10_before_minus15"),
                "time_to_plus5": out("time_to_plus5"),
                "time_to_plus10": out("time_to_plus10"),
                "NO_PROGRESS_300S": out("NO_PROGRESS_300S"),
            })

    with CACHE.open("w", encoding="utf-8") as f:
        for r in enriched:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"=== enriched {len(enriched)} ===", flush=True)
    return enriched
