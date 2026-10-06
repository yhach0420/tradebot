"""Raw valid continuous board events in (t0-180, t0]. Same session. No future fill."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.entry_sequence_representation import MARK_OFFSETS
from research.entry_sequence_representation.sequence import (
    itayose_mask,
    snapshot_at,
    special_mask,
    continuous_valid_mask,
)
from research.raw_event_information_audit import STALE_SEC, WINDOW_SEC


def _spread_bps(bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    mid = 0.5 * (bid + ask)
    out = np.zeros(bid.shape, dtype=float)
    ok = mid > 0
    out[ok] = (ask[ok] - bid[ok]) / mid[ok] * 10000.0
    return out


def _imbalance(bq: np.ndarray, aq: np.ndarray) -> np.ndarray:
    den = bq + aq
    out = np.zeros(bq.shape, dtype=float)
    ok = den > 0
    out[ok] = (bq[ok] - aq[ok]) / den[ok]
    return out


def _sign_flips(x: np.ndarray) -> int:
    if x.size < 2:
        return 0
    s = np.sign(x)
    return int(np.sum(s[1:] != s[:-1]))


def _zero_crosses(x: np.ndarray) -> int:
    if x.size < 2:
        return 0
    a = x[:-1]
    b = x[1:]
    return int(np.sum(((a > 0) & (b < 0)) | ((a < 0) & (b > 0))))


def _direction_changes(x: np.ndarray) -> int:
    if x.size < 3:
        return 0
    d = np.diff(x)
    nz = d[np.abs(d) > 1e-12]
    if nz.size < 2:
        return 0
    s = np.sign(nz)
    return int(np.sum(s[1:] != s[:-1]))


def _max_drop(x: np.ndarray) -> float:
    if x.size < 2:
        return 0.0
    d = np.diff(x)
    drops = -d[d < 0]
    return float(np.max(drops)) if drops.size else 0.0


def extract_raw_indices(
    *,
    t: np.ndarray,
    valid: np.ndarray,
    itay: np.ndarray,
    specm: np.ndarray,
    t0: float,
    t_lo: Optional[float],
) -> tuple[np.ndarray, dict[str, int]]:
    acc = {
        "event_time_after_t0_n": 0,
        "session_carry_n": 0,
        "itayose_event_use_n": 0,
        "special_event_use_n": 0,
        "future_event_use_n": 0,
    }
    n = int(t.size)
    if n == 0:
        return np.asarray([], dtype=int), acc
    lo = float(t_lo) if t_lo is not None else float("-inf")
    left = float(t0) - float(WINDOW_SEC)
    i0 = int(np.searchsorted(t, left, side="right"))
    i1 = int(np.searchsorted(t, float(t0), side="right"))
    keep = []
    for i in range(i0, i1):
        ti = float(t[i])
        if ti > float(t0) + 1e-12:
            acc["event_time_after_t0_n"] += 1
            acc["future_event_use_n"] += 1
            continue
        if ti < lo - 1e-12:
            continue
        if not bool(valid[i]):
            continue
        if bool(itay[i]) or bool(specm[i]):
            continue
        keep.append(i)
    return np.asarray(keep, dtype=int), acc


def _grid_series(
    *,
    t: np.ndarray,
    valid: np.ndarray,
    itay: np.ndarray,
    specm: np.ndarray,
    bid: np.ndarray,
    ask: np.ndarray,
    bq: np.ndarray,
    aq: np.ndarray,
    t0: float,
    t_lo: Optional[float],
) -> dict[str, np.ndarray]:
    imb = []
    spr = []
    qb = []
    qa = []
    ok = []
    for off in MARK_OFFSETS:
        mark = float(t0) - float(off)
        i, _fl = snapshot_at(t=t, valid=valid, itay=itay, specm=specm, mark=mark, t0=float(t0), t_lo=t_lo)
        if i < 0:
            imb.append(0.0)
            spr.append(0.0)
            qb.append(0.0)
            qa.append(0.0)
            ok.append(False)
            continue
        b = float(bid[i])
        a = float(ask[i])
        mid = 0.5 * (b + a)
        qbb = float(bq[i])
        qaa = float(aq[i])
        den = qbb + qaa
        imb.append((qbb - qaa) / den if den > 0 else 0.0)
        spr.append((a - b) / mid * 10000.0 if mid > 0 else 0.0)
        qb.append(qbb)
        qa.append(qaa)
        ok.append(True)
    mask = np.asarray(ok, dtype=bool)
    return {
        "imbalance": np.asarray(imb, dtype=float)[mask],
        "spread": np.asarray(spr, dtype=float)[mask],
        "bq": np.asarray(qb, dtype=float)[mask],
        "aq": np.asarray(qa, dtype=float)[mask],
    }


def _time_shares(
    *,
    t_evt: np.ndarray,
    imb: np.ndarray,
    spread: np.ndarray,
    t0: float,
    t_start: float,
    start: Optional[dict[str, float]],
) -> dict[str, Optional[float]]:
    times = [float(t_start)]
    imbs: list[Optional[float]] = [None if start is None else float(start["imbalance"])]
    sprs: list[Optional[float]] = [None if start is None else float(start["spread"])]
    ages_at: list[Optional[float]] = [None if start is None else float(start["age"])]
    for i in range(int(t_evt.size)):
        times.append(float(t_evt[i]))
        imbs.append(float(imb[i]))
        sprs.append(float(spread[i]))
        ages_at.append(0.0)
    times.append(float(t0))
    med_sp = None
    finite_sp = [float(s) for s in sprs if s is not None]
    if finite_sp:
        med_sp = float(np.median(finite_sp))
    pos = neg = tight = above = stale = covered = 0.0
    for i in range(len(times) - 1):
        dt = float(times[i + 1] - times[i])
        if dt <= 0:
            continue
        if imbs[i] is None or sprs[i] is None:
            continue
        covered += dt
        if float(imbs[i]) > 0:
            pos += dt
        elif float(imbs[i]) < 0:
            neg += dt
        if med_sp is not None and float(sprs[i]) <= med_sp + 1e-12:
            tight += dt
        if med_sp is not None and float(sprs[i]) > med_sp:
            above += dt
        age0 = 0.0 if ages_at[i] is None else float(ages_at[i])
        # age grows from age0 over dt; stale when age > STALE_SEC
        lim = max(float(STALE_SEC) - age0, 0.0)
        stale += max(dt - lim, 0.0)
    if covered <= 0:
        return {
            "positive_imbalance_time_share": None,
            "negative_imbalance_time_share": None,
            "tight_spread_time_share": None,
            "stale_quote_time_share": None,
            "time_spread_above_median_share": None,
        }
    return {
        "positive_imbalance_time_share": pos / covered,
        "negative_imbalance_time_share": neg / covered,
        "tight_spread_time_share": tight / covered,
        "stale_quote_time_share": stale / covered,
        "time_spread_above_median_share": above / covered,
    }


def describe(
    *,
    board: dict[str, np.ndarray],
    t0: float,
    t_lo: Optional[float],
) -> tuple[dict[str, Any], dict[str, int]]:
    empty = {k: None for k in (
        "event_count_180s",
        "event_count_60s",
        "interarrival_median_ms",
        "interarrival_p10_ms",
        "interarrival_cv",
        "max_silence_ms",
        "imbalance_sign_flip_count",
        "imbalance_zero_cross_count",
        "imbalance_abs_change_sum",
        "imbalance_max",
        "imbalance_min",
        "imbalance_range",
        "spread_change_count",
        "spread_widen_count",
        "spread_narrow_count",
        "spread_max_bps",
        "spread_range_bps",
        "time_spread_above_median_share",
        "bid_qty_abs_change_sum",
        "ask_qty_abs_change_sum",
        "bid_qty_max_drop",
        "ask_qty_max_drop",
        "depth_ratio_sign_flip_count",
        "positive_imbalance_time_share",
        "negative_imbalance_time_share",
        "tight_spread_time_share",
        "stale_quote_time_share",
        "raw_imbalance_flips_n",
        "grid_visible_imbalance_flips_n",
        "raw_spread_transitions_n",
        "grid_visible_spread_transitions_n",
        "raw_depth_direction_changes_n",
        "grid_visible_depth_direction_changes_n",
        "n_events",
    )}
    t = np.asarray(board.get("t") if board.get("t") is not None else [], dtype=float)
    n = int(t.size)
    if n == 0:
        out = dict(empty)
        out["n_events"] = 0
        out["event_count_180s"] = 0
        out["event_count_60s"] = 0
        out["raw_imbalance_flips_n"] = 0
        out["grid_visible_imbalance_flips_n"] = 0
        out["raw_spread_transitions_n"] = 0
        out["grid_visible_spread_transitions_n"] = 0
        out["raw_depth_direction_changes_n"] = 0
        out["grid_visible_depth_direction_changes_n"] = 0
        return out, {
            "event_time_after_t0_n": 0,
            "session_carry_n": 0,
            "itayose_event_use_n": 0,
            "special_event_use_n": 0,
            "future_event_use_n": 0,
        }
    valid = continuous_valid_mask(board)
    itay = itayose_mask(board)
    specm = special_mask(board)
    bid = np.asarray(board["bid"][:n], dtype=float)
    ask = np.asarray(board["ask"][:n], dtype=float)
    bq = np.asarray(board["bid_qty"][:n], dtype=float)
    aq = np.asarray(board["ask_qty"][:n], dtype=float)
    idx, acc = extract_raw_indices(t=t, valid=valid, itay=itay, specm=specm, t0=t0, t_lo=t_lo)
    t_start = float(t0) - float(WINDOW_SEC)
    i_start, _f0 = snapshot_at(t=t, valid=valid, itay=itay, specm=specm, mark=t_start, t0=float(t0), t_lo=t_lo)
    start = None
    if i_start >= 0:
        bb, aa = float(bid[i_start]), float(ask[i_start])
        mid = 0.5 * (bb + aa)
        qbb, qaa = float(bq[i_start]), float(aq[i_start])
        den = qbb + qaa
        start = {
            "imbalance": (qbb - qaa) / den if den > 0 else 0.0,
            "spread": (aa - bb) / mid * 10000.0 if mid > 0 else 0.0,
            "age": max(t_start - float(t[i_start]), 0.0),
        }
    grid = _grid_series(
        t=t, valid=valid, itay=itay, specm=specm, bid=bid, ask=ask, bq=bq, aq=aq, t0=t0, t_lo=t_lo
    )
    g_imb_flips = _sign_flips(grid["imbalance"])
    g_spr_tr = 0
    if grid["spread"].size >= 2:
        ds = np.diff(grid["spread"])
        g_spr_tr = int(np.sum(np.abs(ds) > 1e-8))
    g_depth = _direction_changes(grid["bq"]) + _direction_changes(grid["aq"])
    out = dict(empty)
    n_evt = int(idx.size)
    out["n_events"] = n_evt
    out["event_count_180s"] = n_evt
    out["grid_visible_imbalance_flips_n"] = g_imb_flips
    out["grid_visible_spread_transitions_n"] = g_spr_tr
    out["grid_visible_depth_direction_changes_n"] = g_depth
    if n_evt == 0:
        out["event_count_60s"] = 0
        out["raw_imbalance_flips_n"] = 0
        out["raw_spread_transitions_n"] = 0
        out["raw_depth_direction_changes_n"] = 0
        shares = _time_shares(t_evt=np.asarray([], dtype=float), imb=np.asarray([]), spread=np.asarray([]), t0=float(t0), t_start=t_start, start=start)
        out.update(shares)
        return out, acc
    tt = t[idx]
    bb = bid[idx]
    aa = ask[idx]
    qbb = bq[idx]
    qaa = aq[idx]
    imb = _imbalance(qbb, qaa)
    spr = _spread_bps(bb, aa)
    ratio = np.divide(qbb, qaa, out=np.ones_like(qbb), where=qaa > 0)
    out["event_count_60s"] = int(np.sum(tt > float(t0) - 60.0 + 1e-12))
    if n_evt >= 2:
        gaps = np.diff(tt)
        gaps_ms = gaps * 1000.0
        out["interarrival_median_ms"] = float(np.median(gaps_ms))
        out["interarrival_p10_ms"] = float(np.quantile(gaps_ms, 0.10))
        mu = float(np.mean(gaps_ms))
        sd = float(np.std(gaps_ms))
        out["interarrival_cv"] = (sd / mu) if mu > 1e-12 else None
    lead = float(tt[0] - t_start)
    trail = float(t0) - float(tt[-1])
    silences = [max(lead, 0.0), max(trail, 0.0)]
    if n_evt >= 2:
        silences.extend([float(x) for x in np.diff(tt)])
    out["max_silence_ms"] = float(np.max(silences) * 1000.0)
    out["imbalance_sign_flip_count"] = _sign_flips(imb)
    out["imbalance_zero_cross_count"] = _zero_crosses(imb)
    out["imbalance_abs_change_sum"] = float(np.sum(np.abs(np.diff(imb)))) if n_evt >= 2 else 0.0
    out["imbalance_max"] = float(np.max(imb))
    out["imbalance_min"] = float(np.min(imb))
    out["imbalance_range"] = float(np.max(imb) - np.min(imb))
    if n_evt >= 2:
        ds = np.diff(spr)
        out["spread_change_count"] = int(np.sum(np.abs(ds) > 1e-8))
        out["spread_widen_count"] = int(np.sum(ds > 1e-8))
        out["spread_narrow_count"] = int(np.sum(ds < -1e-8))
        out["bid_qty_abs_change_sum"] = float(np.sum(np.abs(np.diff(qbb))))
        out["ask_qty_abs_change_sum"] = float(np.sum(np.abs(np.diff(qaa))))
    else:
        out["spread_change_count"] = 0
        out["spread_widen_count"] = 0
        out["spread_narrow_count"] = 0
        out["bid_qty_abs_change_sum"] = 0.0
        out["ask_qty_abs_change_sum"] = 0.0
    out["spread_max_bps"] = float(np.max(spr))
    out["spread_range_bps"] = float(np.max(spr) - np.min(spr))
    out["bid_qty_max_drop"] = _max_drop(qbb)
    out["ask_qty_max_drop"] = _max_drop(qaa)
    out["depth_ratio_sign_flip_count"] = _sign_flips(ratio - 1.0)
    out["raw_imbalance_flips_n"] = int(out["imbalance_sign_flip_count"] or 0)
    out["raw_spread_transitions_n"] = int(out["spread_change_count"] or 0)
    out["raw_depth_direction_changes_n"] = _direction_changes(qbb) + _direction_changes(qaa)
    shares = _time_shares(t_evt=tt, imb=imb, spread=spr, t0=float(t0), t_start=t_start, start=start)
    out.update(shares)
    return out, acc
