"""Per-episode variant anchors C0–C3 with directional labels."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x14_board_independent_signal.features import (
    attach_forward_labels,
    attach_path_volume_features,
    attach_relative_strength,
)
from research.e1_x14_board_independent_signal.grid import build_symbol_day_grid
from research.e1_x14_board_independent_signal.ticks import list_day_symbols, load_symbol_ticks

from . import (
    DAYS,
    MIN_RS_UNIVERSE,
    NO_PROGRESS_RET,
    REBOUND_Q80,
    VOL_PCT_Q80,
    VWAP_Q80,
)

JST = ZoneInfo("Asia/Tokyo")


def _asof_row(rows: list[dict[str, Any]], epoch: float) -> Optional[dict[str, Any]]:
    best = None
    for r in rows:
        ge = r.get("grid_epoch")
        if ge is None or ge > epoch + 1e-9:
            continue
        if r.get("CurrentPrice") is None:
            continue
        if best is None or ge > best["grid_epoch"]:
            best = r
    return best


def _rebound_diag(ticks: list[dict[str, Any]], epoch: float, lookback: float = 180.0) -> dict[str, Any]:
    times = np.asarray([t["t"] for t in ticks], dtype=float)
    prices = np.asarray([t["price"] for t in ticks], dtype=float)
    i_now = int(np.searchsorted(times, epoch, side="right") - 1)
    i_lb = int(np.searchsorted(times, epoch - lookback, side="right") - 1)
    if i_now < 0 or i_lb < 0 or i_now <= i_lb:
        return {"ok": False, "reason": "insufficient_path"}
    window = prices[i_lb + 1: i_now + 1]
    wtimes = times[i_lb + 1: i_now + 1]
    j = int(np.argmin(window))
    low_px = float(window[j])
    low_t = float(wtimes[j])
    anchor_px = float(prices[i_now])
    if low_t > times[i_now] + 1e-9:
        return {"ok": False, "reason": "recent_low_after_anchor"}
    rebound = (anchor_px / low_px - 1.0) * 10000.0 if low_px > 0 else None
    return {
        "ok": True,
        "recent_low_time": datetime.fromtimestamp(low_t, tz=JST).isoformat(),
        "recent_low_price": low_px,
        "anchor_time": datetime.fromtimestamp(times[i_now], tz=JST).isoformat(),
        "anchor_price": anchor_px,
        "rebound_bps": rebound,
        "elapsed_sec_from_low": times[i_now] - low_t,
    }


def _conds(row: dict[str, Any]) -> dict[str, bool]:
    vwap = row.get("distance_from_vwap_bps")
    reb = row.get("rebound_from_recent_low_bps")
    volp = row.get("volume_percentile_60s")
    rs_n = row.get("rs_universe_n") or 0
    activity_eval = rs_n >= MIN_RS_UNIVERSE and volp is not None
    return {
        "REBOUND_READY": reb is not None and float(reb) >= REBOUND_Q80,
        "VWAP_DISTANCE_OK": vwap is not None and float(vwap) <= VWAP_Q80,
        "ACTIVITY_READY": activity_eval and float(volp) >= VOL_PCT_Q80,
        "ACTIVITY_NOT_EVALUABLE": not activity_eval,
    }


def _variant_ok(name: str, c: dict[str, bool]) -> bool:
    if name == "C1":
        return c["REBOUND_READY"]
    if name == "C2":
        return c["REBOUND_READY"] and c["VWAP_DISTANCE_OK"]
    if name == "C3":
        return c["REBOUND_READY"] and c["VWAP_DISTANCE_OK"] and c["ACTIVITY_READY"]
    return False


def _no_progress(row: dict[str, Any]) -> bool:
    mfe = row.get("MFE_300s")
    fr = row.get("forward_return_300s")
    if mfe is None or fr is None:
        return False
    return float(mfe) < NO_PROGRESS_RET and abs(float(fr)) < NO_PROGRESS_RET


def build_day_grids(day: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list]]:
    all_syms = list_day_symbols(day)
    sym_grids: dict[str, list] = {}
    ticks_by: dict[str, list] = {}
    for sym in all_syms:
        ticks = load_symbol_ticks(day, sym)
        ticks_by[sym] = ticks
        grids = build_symbol_day_grid(day, sym, ticks, f"push_jsonl_{day}")
        grids = attach_path_volume_features(grids, ticks)
        grids = attach_forward_labels(grids, ticks, day)
        sym_grids[sym] = grids
    rs_rows = attach_relative_strength(sym_grids)
    rs_map = {(r["symbol"], r["grid_epoch"]): r for r in rs_rows}
    for sym, rows in sym_grids.items():
        for r in rows:
            m = rs_map.get((sym, r["grid_epoch"]))
            if m:
                for k in (
                    "volume_percentile_60s", "return_percentile_60s", "return_percentile_180s",
                    "rs_universe_n", "relative_status",
                ):
                    if k in m:
                        r[k] = m[k]
    return sym_grids, ticks_by


def select_anchors_for_episodes(
    episodes: list[dict[str, Any]],
    *,
    progress_cb=None,
) -> list[dict[str, Any]]:
    by_day: dict[str, list] = {}
    for ep in episodes:
        by_day.setdefault(ep["date"], []).append(ep)

    matched = []
    for day in DAYS:
        eps = by_day.get(day) or []
        if not eps:
            continue
        if progress_cb:
            progress_cb(f"day {day} episodes={len(eps)}")
        sym_grids, ticks_by = build_day_grids(day)

        for ep in eps:
            sym = ep["symbol"]
            rows = [r for r in (sym_grids.get(sym) or []) if r.get("session") == ep["session"]]
            ticks = ticks_by.get(sym) or []
            t0, t1 = ep["window_start"], ep["window_end"]

            c0_row = _asof_row(rows, ep["c0_epoch"])
            c0 = {
                "variant": "C0",
                "status": "OK" if c0_row else "NO_ENTRY_FOR_VARIANT",
                "time": ep["c0_time"],
                "epoch": ep["c0_epoch"],
                "price": (c0_row or {}).get("CurrentPrice") or ep.get("c0_price"),
                "row": c0_row,
            }
            # C0 outcomes from as-of row (labels at grid, approximate)
            firsts: dict[str, Any] = {"C0": c0}
            for vname in ("C1", "C2", "C3"):
                hit = None
                for r in sorted(rows, key=lambda x: x["grid_epoch"]):
                    ge = r["grid_epoch"]
                    if ge < t0 - 1e-9 or ge > t1 + 1e-9:
                        continue
                    if r.get("rebound_from_recent_low_bps") is None:
                        continue
                    if _variant_ok(vname, _conds(r)):
                        hit = r
                        break
                if hit is None:
                    firsts[vname] = {
                        "variant": vname, "status": "NO_ENTRY_FOR_VARIANT",
                        "time": None, "epoch": None, "price": None, "row": None,
                    }
                else:
                    firsts[vname] = {
                        "variant": vname, "status": "OK",
                        "time": hit.get("grid_time"), "epoch": hit["grid_epoch"],
                        "price": hit.get("CurrentPrice"), "row": hit,
                    }

            rec = {k: ep[k] for k in (
                "rpfe_episode_id", "date", "symbol", "session",
                "window_start", "window_end", "n_candidates",
            )}
            for vname, info in firsts.items():
                prefix = vname.lower()
                rec[f"{prefix}_status"] = info["status"]
                rec[f"{prefix}_time"] = info["time"]
                rec[f"{prefix}_epoch"] = info["epoch"]
                rec[f"{prefix}_price"] = info["price"]
                row = info.get("row") or {}
                if info["status"] == "OK" and info.get("epoch") is not None:
                    if vname != "C0":
                        rec[f"{prefix}_time_delta_vs_c0_sec"] = float(info["epoch"] - ep["c0_epoch"])
                    else:
                        rec[f"{prefix}_time_delta_vs_c0_sec"] = 0.0
                    c0p = firsts["C0"].get("price")
                    vp = info.get("price")
                    if c0p and vp and float(c0p) > 0:
                        rec[f"{prefix}_price_delta_vs_c0_bps"] = (float(vp) / float(c0p) - 1.0) * 10000.0
                    else:
                        rec[f"{prefix}_price_delta_vs_c0_bps"] = None
                    for k in (
                        "forward_return_30s", "forward_return_60s", "forward_return_180s", "forward_return_300s",
                        "MFE_60s", "MAE_60s", "MFE_180s", "MAE_180s", "MFE_300s", "MAE_300s",
                        "plus5_before_minus5", "plus5_before_minus10",
                        "plus10_before_minus10", "plus10_before_minus15",
                        "time_to_plus5", "time_to_plus10",
                        "distance_from_vwap_bps", "rebound_from_recent_low_bps", "volume_percentile_60s",
                        "rs_universe_n", "price_age_sec",
                    ):
                        rec[f"{prefix}_{k}"] = row.get(k)
                    rec[f"{prefix}_NO_PROGRESS_300S"] = _no_progress(row)
                    diag = _rebound_diag(ticks, float(info["epoch"]))
                    rec[f"{prefix}_rebound_diag"] = diag
                    if vname in ("C1", "C2", "C3") and diag.get("reason") == "recent_low_after_anchor":
                        rec[f"{prefix}_status"] = "NO_ENTRY_FOR_VARIANT"
                else:
                    rec[f"{prefix}_time_delta_vs_c0_sec"] = None
                    rec[f"{prefix}_price_delta_vs_c0_bps"] = None
                    rec[f"{prefix}_NO_PROGRESS_300S"] = None
            matched.append(rec)
    return matched
