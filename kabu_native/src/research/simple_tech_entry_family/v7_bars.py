"""Aggregate completed 1m OHLCV into 3m/5m session-anchored bars. Causal finalize_t only."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.simple_tech_entry_family.bars import BAR_FIELDS


def aggregate_bars(
    raw: dict[str, np.ndarray],
    *,
    width_sec: float,
    am_start: float,
    am_end: float,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    leak = {
        "PARTIAL_BUCKET_N": 0,
        "GAP_BUCKET_N": 0,
        "LATE_FINALIZE_N": 0,
        "OK_BAR_N": 0,
        "FUTURE_BAR_N": 0,
        "SESSION_TAIL_SKIP_N": 0,
    }
    m = raw.get("minute_epoch")
    empty = {k: np.asarray([], dtype=float) for k in BAR_FIELDS}
    if m is None or int(m.size) == 0:
        return empty, leak
    need = int(round(float(width_sec) / 60.0))
    width = float(need) * 60.0
    buckets: dict[float, list[int]] = {}
    for i in range(int(m.size)):
        mi = float(m[i])
        off = mi - float(am_start)
        if off < -1e-12:
            continue
        k = int(np.floor((off / width) + 1e-12))
        start = float(am_start) + float(k) * width
        if start + width > float(am_end) + 1e-12:
            leak["SESSION_TAIL_SKIP_N"] += 1
            continue
        buckets.setdefault(start, []).append(i)
    recs: list[dict[str, float]] = []
    for start in sorted(buckets):
        idxs = buckets[start]
        expected = [start + 60.0 * j for j in range(need)]
        got = sorted(float(m[i]) for i in idxs)
        if len(got) != need:
            leak["PARTIAL_BUCKET_N"] += 1
            continue
        if any(abs(a - b) > 1e-6 for a, b in zip(got, expected)):
            leak["GAP_BUCKET_N"] += 1
            continue
        order = [i for _, i in sorted((float(m[i]), i) for i in idxs)]
        fin = float(raw["finalize_t"][order[-1]])
        if not (fin == fin) or fin + 1e-12 < start + width:
            leak["LATE_FINALIZE_N"] += 1
            continue
        recs.append(
            {
                "minute_epoch": float(start),
                "open": float(raw["open"][order[0]]),
                "high": float(np.max(raw["high"][order])),
                "low": float(np.min(raw["low"][order])),
                "close": float(raw["close"][order[-1]]),
                "volume": float(np.sum(raw["volume"][order])),
                "n_events": float(np.sum(raw["n_events"][order])),
                "first_t": float(raw["first_t"][order[0]]),
                "last_t": float(raw["last_t"][order[-1]]),
                "finalize_t": fin,
                "up_vol": float(np.sum(raw["up_vol"][order])),
                "down_vol": float(np.sum(raw["down_vol"][order])),
                "ask_vol": float(np.sum(raw["ask_vol"][order])),
                "bid_vol": float(np.sum(raw["bid_vol"][order])),
                "vwap_num": float(np.sum(raw["vwap_num"][order])),
            }
        )
        leak["OK_BAR_N"] += 1
    n = len(recs)
    out: dict[str, np.ndarray] = {}
    if n == 0:
        return empty, leak
    for k in BAR_FIELDS:
        out[k] = np.asarray([float(r[k]) for r in recs], dtype=float)
    return out, leak


def agg_integrity(arr: dict[str, np.ndarray], *, width_sec: float, am_start: float, am_end: float) -> dict[str, Any]:
    m = arr.get("minute_epoch")
    if m is None or int(m.size) == 0:
        return {"bar_n": 0, "ok": True, "FUTURE_BAR_N": 0, "IN_PROGRESS_BAR_N": 0, "SESSION_CARRY_N": 0, "OHLC_INVALID_N": 0, "NON_MONOTONE_N": 0}
    width = float(width_sec)
    fin = arr["finalize_t"]
    future = int(np.sum(fin + 1e-12 < m + width))
    inprog = int(np.sum(~np.isfinite(fin)))
    carry = int(np.sum((m < am_start - 1e-12) | (m + width > am_end + 1e-12)))
    ohlc = int(
        np.sum(
            ~(
                (arr["high"] + 1e-12 >= np.maximum(arr["open"], arr["close"]))
                & (arr["low"] - 1e-12 <= np.minimum(arr["open"], arr["close"]))
                & (arr["volume"] >= -1e-12)
            )
        )
    )
    nonmono = int(np.sum(np.diff(m) <= 1e-12)) if m.size > 1 else 0
    return {
        "bar_n": int(m.size),
        "ok": future == 0 and inprog == 0 and carry == 0 and ohlc == 0 and nonmono == 0,
        "FUTURE_BAR_N": future,
        "IN_PROGRESS_BAR_N": inprog,
        "SESSION_CARRY_N": carry,
        "OHLC_INVALID_N": ohlc,
        "NON_MONOTONE_N": nonmono,
        "width_sec": width,
    }


def self_check_agg() -> dict[str, Any]:
    n = 6
    raw = {k: np.zeros(n, dtype=float) for k in BAR_FIELDS}
    raw["minute_epoch"] = np.asarray([0.0, 60.0, 120.0, 180.0, 240.0, 300.0], dtype=float)
    raw["open"] = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], dtype=float)
    raw["high"] = np.asarray([1.5, 2.5, 3.5, 4.5, 5.5, 6.5], dtype=float)
    raw["low"] = np.asarray([0.5, 1.5, 2.5, 3.5, 4.5, 5.5], dtype=float)
    raw["close"] = np.asarray([1.2, 2.2, 3.2, 4.2, 5.2, 6.2], dtype=float)
    raw["volume"] = np.asarray([10.0, 20.0, 30.0, 40.0, 50.0, 60.0], dtype=float)
    raw["n_events"] = np.ones(n, dtype=float)
    raw["first_t"] = raw["minute_epoch"]
    raw["last_t"] = raw["minute_epoch"] + 50.0
    raw["finalize_t"] = raw["minute_epoch"] + 60.0
    raw["up_vol"] = np.ones(n, dtype=float)
    raw["down_vol"] = np.ones(n, dtype=float)
    raw["ask_vol"] = np.ones(n, dtype=float)
    raw["bid_vol"] = np.ones(n, dtype=float)
    raw["vwap_num"] = raw["close"] * raw["volume"]
    out, leak = aggregate_bars(raw, width_sec=180.0, am_start=0.0, am_end=600.0)
    integ = agg_integrity(out, width_sec=180.0, am_start=0.0, am_end=600.0)
    ok = (
        int(leak.get("OK_BAR_N") or 0) == 2
        and int(out["open"].size) == 2
        and abs(float(out["open"][0]) - 1.0) < 1e-12
        and abs(float(out["close"][0]) - 3.2) < 1e-12
        and abs(float(out["open"][1]) - 4.0) < 1e-12
        and abs(float(out["close"][1]) - 6.2) < 1e-12
        and abs(float(out["finalize_t"][0]) - 180.0) < 1e-12
        and abs(float(out["volume"][0]) - 60.0) < 1e-12
        and bool(integ.get("ok"))
        and int(integ.get("FUTURE_BAR_N") or 0) == 0
    )
    gapped = dict(raw)
    gapped["minute_epoch"] = np.asarray([0.0, 60.0, 121.0, 180.0, 240.0, 300.0], dtype=float)
    gapped["finalize_t"] = np.asarray([60.0, 120.0, 181.0, 240.0, 300.0, 360.0], dtype=float)
    _, leak_g = aggregate_bars(gapped, width_sec=180.0, am_start=0.0, am_end=600.0)
    ok = bool(ok and int(leak_g.get("GAP_BUCKET_N") or 0) >= 1)
    return {"ok": ok, "ok_bar_n": leak.get("OK_BAR_N"), "gap_n": leak_g.get("GAP_BUCKET_N"), "integ": integ}
