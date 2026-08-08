"""Weighting, spreads, day/TOD, density — diagnostic aggregates only."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from . import HORIZONS_SEC, TOD_BUCKETS

JST = ZoneInfo("Asia/Tokyo")


def _finite(xs: list[float]) -> np.ndarray:
    a = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    return a


def dist_stats(xs: list[float] | np.ndarray) -> dict[str, Any]:
    a = _finite(list(xs) if not isinstance(xs, np.ndarray) else xs.tolist())
    if a.size == 0:
        return {
            "n": 0, "mean": None, "median": None,
            "p10": None, "p50": None, "p90": None, "p95": None,
            "positive_rate": None, "negative_rate": None,
        }
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "p10": float(np.quantile(a, 0.10)),
        "p50": float(np.quantile(a, 0.50)),
        "p90": float(np.quantile(a, 0.90)),
        "p95": float(np.quantile(a, 0.95)),
        "positive_rate": float(np.mean(a > 0)),
        "negative_rate": float(np.mean(a < 0)),
    }


def episode_mean(rows: list[dict], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if r.get(key) is not None and np.isfinite(r[key])]
    return float(np.mean(xs)) if xs else None


def _group_means(rows: list[dict], key: str, group_fn) -> dict[Any, float]:
    by: dict[Any, list[float]] = defaultdict(list)
    for r in rows:
        v = r.get(key)
        if v is None or not np.isfinite(v):
            continue
        by[group_fn(r)].append(float(v))
    return {k: float(np.mean(v)) for k, v in by.items() if v}


def balanced_mean(rows: list[dict], key: str, group_fn) -> float | None:
    m = _group_means(rows, key, group_fn)
    return float(np.mean(list(m.values()))) if m else None


def ss_key(r: dict) -> tuple:
    return (r["date"], r["symbol"], r["session"])


def day_key(r: dict) -> str:
    return r["date"]


def symbol_day_key(r: dict) -> tuple:
    return (r["date"], r["symbol"])


def weighting_table(rows: list[dict], keys: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"n_episodes": len(rows)}
    for key in keys:
        out[key] = {
            "episode_weighted": episode_mean(rows, key),
            "symbol_session_balanced": balanced_mean(rows, key, ss_key),
            "day_balanced": balanced_mean(rows, key, day_key),
            "symbol_day_balanced": balanced_mean(rows, key, symbol_day_key),
        }
    return out


def tod_bucket(epoch: float) -> str | None:
    dt = datetime.fromtimestamp(float(epoch), tz=JST)
    hm = (dt.hour, dt.minute)
    for name, start, end in TOD_BUCKETS:
        if start <= hm < end:
            return name
    return None


def spread_summary(rows: list[dict]) -> dict[str, Any]:
    entry = [float(r["entry_spread_bps"]) for r in rows if r.get("entry_spread_bps") is not None]
    half = [float(r["entry_half_spread_bps"]) for r in rows if r.get("entry_half_spread_bps") is not None]
    out = {
        "entry_spread_bps": dist_stats(entry),
        "entry_half_spread_bps": dist_stats(half),
        "exit_half_spread_bps": {},
    }
    for H in HORIZONS_SEC:
        k = f"exit_half_spread_bps_{H}"
        out["exit_half_spread_bps"][str(H)] = dist_stats(
            [float(r[k]) for r in rows if r.get(k) is not None]
        )
    return out


def density_audit(rows: list[dict]) -> dict[str, Any]:
    """symbol-session episode count vs performance correlation."""
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by[ss_key(r)].append(r)
    recs = []
    for k, g in by.items():
        n = len(g)
        e300 = [float(x["exec_300"]) for x in g if x.get("exec_300") is not None]
        e600 = [float(x["exec_600"]) for x in g if x.get("exec_600") is not None]
        m300 = [float(x["mid_300"]) for x in g if x.get("mid_300") is not None]
        m600 = [float(x["mid_600"]) for x in g if x.get("mid_600") is not None]
        spr = [float(x["entry_spread_bps"]) for x in g if x.get("entry_spread_bps") is not None]
        recs.append({
            "date": k[0], "symbol": k[1], "session": k[2],
            "episode_count": n,
            "ret300": float(np.mean(e300)) if e300 else None,
            "ret600": float(np.mean(e600)) if e600 else None,
            "mid_ret300": float(np.mean(m300)) if m300 else None,
            "mid_ret600": float(np.mean(m600)) if m600 else None,
            "spread": float(np.mean(spr)) if spr else None,
        })
    counts = np.asarray([r["episode_count"] for r in recs], dtype=float)

    def _corr(ykey: str) -> float | None:
        ys = []
        cs = []
        for r in recs:
            if r.get(ykey) is None:
                continue
            ys.append(float(r[ykey]))
            cs.append(float(r["episode_count"]))
        if len(ys) < 5:
            return None
        return float(np.corrcoef(cs, ys)[0, 1])

    # Does high-count SS pull episode mean up?
    ep600 = episode_mean(rows, "exec_600")
    bal600 = balanced_mean(rows, "exec_600", ss_key)
    return {
        "n_symbol_sessions": len(recs),
        "episode_count_mean": float(np.mean(counts)),
        "episode_count_median": float(np.median(counts)),
        "corr_count_vs_ret300": _corr("ret300"),
        "corr_count_vs_ret600": _corr("ret600"),
        "corr_count_vs_mid300": _corr("mid_ret300"),
        "corr_count_vs_mid600": _corr("mid_ret600"),
        "episode_mean_exec600": ep600,
        "symbol_session_balanced_exec600": bal600,
        "gap_episode_minus_balanced_600": (
            None if ep600 is None or bal600 is None else float(ep600 - bal600)
        ),
        "interpretation": (
            "positive corr(count, ret) + episode>balanced suggests dense good SS inflate episode mean"
            if (_corr("ret600") or 0) > 0.05 and ep600 is not None and bal600 is not None and ep600 > bal600
            else "see correlations / gap"
        ),
        "sample_rows": sorted(recs, key=lambda x: -x["episode_count"])[:40],
    }


def day_decomposition(rows: list[dict], latency_by_day: dict[str, dict] | None = None) -> list[dict]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r)
    out = []
    for day in sorted(by):
        g = by[day]
        rec = {"date": day, "n": len(g)}
        for H in (300, 600):
            rec[f"mid_{H}"] = episode_mean(g, f"mid_{H}")
            rec[f"exec_{H}"] = episode_mean(g, f"exec_{H}")
            rec[f"drag_{H}"] = episode_mean(g, f"drag_{H}")
            rec[f"spread_only_drag_{H}"] = episode_mean(g, f"spread_only_drag_{H}")
            rec[f"residual_drag_{H}"] = episode_mean(g, f"residual_drag_{H}")
        if latency_by_day and day in latency_by_day:
            rec.update(latency_by_day[day])
        out.append(rec)
    return out


def percentile_bin(values: np.ndarray, x: float) -> str:
    if values.size < 10 or not np.isfinite(x):
        return "UNK"
    p33, p66 = np.quantile(values, [0.33, 0.66])
    if x <= p33:
        return "LOW"
    if x <= p66:
        return "MID"
    return "HIGH"


def market_state_fields(rows: list[dict]) -> None:
    """Annotate rows in-place with spread/vol/activity percentile bins (diagnostic)."""
    spreads = np.asarray(
        [float(r["entry_spread_bps"]) for r in rows if r.get("entry_spread_bps") is not None],
        dtype=float,
    )
    # proxy vol: |mid_60| when available
    vols = np.asarray(
        [abs(float(r["mid_60"])) for r in rows if r.get("mid_60") is not None],
        dtype=float,
    )
    # activity proxy: mapping delay inverse not great; use entry_ask_delay
    acts = np.asarray(
        [float(r.get("mapping_delay_sec") or 0.0) for r in rows],
        dtype=float,
    )
    for r in rows:
        sp = r.get("entry_spread_bps")
        r["spread_bin"] = percentile_bin(spreads, float(sp)) if sp is not None else "UNK"
        mv = abs(float(r["mid_60"])) if r.get("mid_60") is not None else None
        r["vol_bin"] = percentile_bin(vols, float(mv)) if mv is not None else "UNK"
        r["activity_bin"] = percentile_bin(acts, float(r.get("mapping_delay_sec") or 0.0))
        r["tod_bucket"] = tod_bucket(float(r["signal_t"]))
