"""Rebuild C0 anchors for TARGET_DAY with same X15 episode/label contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.e1_x14_board_independent_signal.features import (
    attach_forward_labels,
    attach_path_volume_features,
)
from research.e1_x14_board_independent_signal.grid import build_symbol_day_grid
from research.e1_x14_board_independent_signal.ticks import load_symbol_ticks
from research.e1_x15_rpfe_incremental_entry.anchors import _asof_row, _no_progress, _rebound_diag
from research.e1_x15_rpfe_incremental_entry.episodes import build_episodes

from . import TARGET_DAY, VWAP_UPPER_LIMIT_BPS

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x17_vwap_reject_prospective"
CACHE = OUT / "_c0_cache.jsonl"


def _prep(ticks: list[dict[str, Any]]) -> dict[str, Any]:
    if not ticks:
        return {"times": np.asarray([], dtype=float), "price": None, "vwap": None}
    times = np.asarray([t["t"] for t in ticks], dtype=float)
    price = np.asarray([t["price"] if t.get("price") is not None else np.nan for t in ticks], dtype=float)
    vwap = np.asarray([t["vwap"] if t.get("vwap") is not None else np.nan for t in ticks], dtype=float)
    return {"times": times, "price": price, "vwap": vwap}


def _asof_idx(times: np.ndarray, epoch: float) -> int:
    if times.size == 0:
        return -1
    return int(np.searchsorted(times, epoch, side="right") - 1)


def _tick_vwap(prep: dict[str, Any], epoch: float) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (distance_bps, vwap_age_sec, price) from ticks at C0 epoch."""
    times = prep["times"]
    i = _asof_idx(times, epoch)
    if i < 0:
        return None, None, None
    px = prep["price"][i]
    vw = prep["vwap"][i]
    if np.isnan(px):
        return None, None, None
    age = epoch - float(times[i])
    if np.isnan(vw) or float(vw) == 0:
        return None, age, float(px)
    dist = (float(px) / float(vw) - 1.0) * 10000.0
    return dist, age, float(px)


def construct_c0(*, force: bool = False) -> list[dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    if CACHE.exists() and not force:
        return [json.loads(l) for l in CACHE.read_text(encoding="utf-8").splitlines() if l.strip()]

    episodes = build_episodes((TARGET_DAY,))
    print(f"=== episodes {TARGET_DAY} n={len(episodes)} ===", flush=True)
    by_sym: dict[str, list] = {}
    for ep in episodes:
        by_sym.setdefault(ep["symbol"], []).append(ep)

    rows_out: list[dict[str, Any]] = []
    for sym, eps in sorted(by_sym.items()):
        print(f"  symbol {sym} eps={len(eps)}", flush=True)
        ticks = load_symbol_ticks(TARGET_DAY, sym)
        prep = _prep(ticks)
        grids = build_symbol_day_grid(TARGET_DAY, sym, ticks, f"push_jsonl_{TARGET_DAY}")
        grids = attach_path_volume_features(grids, ticks)
        grids = attach_forward_labels(grids, ticks, TARGET_DAY)

        for ep in eps:
            sess_rows = [r for r in grids if r.get("session") == ep["session"]]
            c0_epoch = float(ep["c0_epoch"])
            crow = _asof_row(sess_rows, c0_epoch)
            dist, vwap_age, tick_px = _tick_vwap(prep, c0_epoch)
            vwap_eval = dist is not None
            # Prefer tick-based VWAP (same as X16 precommit feature_source);
            # fall back to grid feature if tick missing
            if not vwap_eval and crow and crow.get("distance_from_vwap_bps") is not None:
                dist = float(crow["distance_from_vwap_bps"])
                vwap_eval = True
                vwap_age = crow.get("vwap_age_sec") or crow.get("price_age_sec")

            if crow is None:
                status = "NO_ENTRY_FOR_VARIANT"
                labels = {}
            else:
                status = "OK"
                labels = {
                    k: crow.get(k)
                    for k in (
                        "forward_return_30s", "forward_return_60s", "forward_return_180s", "forward_return_300s",
                        "MFE_60s", "MAE_60s", "MFE_180s", "MAE_180s", "MFE_300s", "MAE_300s",
                        "plus5_before_minus5", "plus5_before_minus10",
                        "plus10_before_minus10", "plus10_before_minus15",
                        "time_to_plus5", "time_to_plus10",
                        "price_age_sec",
                    )
                }
                labels["NO_PROGRESS_300S"] = _no_progress(crow)

            reb_diag = _rebound_diag(ticks, c0_epoch) if ticks else {"ok": False}
            reb = float(reb_diag["rebound_bps"]) if reb_diag.get("ok") and reb_diag.get("rebound_bps") is not None else None

            a2_pass = bool(status == "OK" and vwap_eval and float(dist) <= VWAP_UPPER_LIMIT_BPS)
            a2_rej = bool(status == "OK" and vwap_eval and float(dist) > VWAP_UPPER_LIMIT_BPS)

            rows_out.append({
                "rpfe_episode_id": ep["rpfe_episode_id"],
                "date": ep["date"],
                "symbol": ep["symbol"],
                "session": ep["session"],
                "c0_time": ep["c0_time"],
                "c0_epoch": c0_epoch,
                "c0_price": (crow or {}).get("CurrentPrice") or ep.get("c0_price") or tick_px,
                "c0_status": status,
                "anchor_contract": "SAME_C0_X15_CONTRACT",
                "distance_from_vwap_bps": dist if vwap_eval else None,
                "vwap_evaluable": vwap_eval,
                "vwap_age_sec": vwap_age,
                "rebound_from_recent_low_bps": reb,
                "rebound_diag": reb_diag,
                "in_C0": status == "OK",
                "in_VWAP_evaluable": status == "OK" and vwap_eval,
                "in_VWAP_not_evaluable": status == "OK" and not vwap_eval,
                "in_A2": a2_pass,
                "in_A2_Rejected": a2_rej,
                **labels,
            })

    with CACHE.open("w", encoding="utf-8") as f:
        for r in rows_out:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"=== c0 rows {len(rows_out)} ===", flush=True)
    return rows_out
