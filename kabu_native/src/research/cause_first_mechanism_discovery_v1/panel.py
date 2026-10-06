"""Load V2 minute parquet and build causally timed snapshots. Frozen Validation dates are never loaded."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np
import pandas as pd

from research.cause_first_mechanism_discovery_v1 import CLOCKS, MIN_ACTIVE_SYMBOLS, PANEL_FROM, PANEL_TO, PRIMARY_HORIZON_MIN, X1_TAX_BPS
from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, interval_crosses_lunch
from research.cause_first_mechanism_discovery_v1.isolation import REF
from research.fixed_universe_historical_foundation_v1.technical import ema, session_vwap

COLS = ["symbol", "date", "time_label", "open", "high", "low", "close", "volume", "trading_value"]


def parquet_path(symbol: str):
    return REF / "minute" / f"minute_{symbol}_{PANEL_FROM}_{PANEL_TO}.parquet"


def collect_session_days(*, symbols: list[str], min_active: int = MIN_ACTIVE_SYMBOLS) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    per_symbol_dates: dict[str, list[str]] = {}
    missing = []
    for i, sym in enumerate(symbols, start=1):
        path = parquet_path(sym)
        if not path.is_file():
            missing.append(sym)
            continue
        df = pd.read_parquet(path, columns=["date"])
        days = sorted({str(x) for x in df["date"].tolist()})
        per_symbol_dates[sym] = days
        counts.update(days)
        if i % 20 == 0:
            print(f"DATES {i}/{len(symbols)}", flush=True)
    days = sorted(
        d
        for d, n in counts.items()
        if int(n) >= int(min_active) and str(d) >= PANEL_FROM and str(d) <= PANEL_TO and str(d) < "20260914"
    )
    return {
        "ok": len(days) >= 30 and not missing,
        "session_days": days,
        "n_sessions": len(days),
        "min_active_symbols": int(min_active),
        "active_n_by_day": {d: int(counts[d]) for d in days},
        "missing_parquet": missing,
        "symbol_first_last": {
            s: {"first": (per_symbol_dates[s][0] if per_symbol_dates[s] else None), "last": (per_symbol_dates[s][-1] if per_symbol_dates[s] else None), "n": len(per_symbol_dates[s])}
            for s in symbols
            if s in per_symbol_dates
        },
        "did_not_backfill_late_listings": True,
    }


def load_minutes(*, symbols: list[str], allowed_dates: set[str], forbidden_dates: set[str]) -> pd.DataFrame:
    if allowed_dates & forbidden_dates:
        raise RuntimeError("validation_dates_in_allowed_load")
    frames = []
    for i, sym in enumerate(symbols, start=1):
        path = parquet_path(sym)
        if not path.is_file():
            continue
        try:
            lo = min(allowed_dates)
            hi = max(allowed_dates)
            df = pd.read_parquet(path, columns=COLS, filters=[("date", ">=", lo), ("date", "<=", hi)])
        except Exception:
            df = pd.read_parquet(path, columns=COLS)
        df["date"] = df["date"].astype(str)
        df["time_label"] = df["time_label"].astype(str).str.slice(0, 5)
        df["symbol"] = df["symbol"].astype(str)
        if df["date"].isin(list(forbidden_dates)).any():
            df = df[~df["date"].isin(forbidden_dates)]
        df = df[df["date"].isin(allowed_dates)]
        if df.empty:
            continue
        frames.append(df)
        if i % 20 == 0:
            print(f"LOAD {i}/{len(symbols)} rows={sum(len(x) for x in frames)}", flush=True)
    if not frames:
        return pd.DataFrame(columns=COLS)
    out = pd.concat(frames, ignore_index=True)
    if out["date"].isin(list(forbidden_dates)).any():
        raise RuntimeError("frozen_validation_rows_loaded")
    return out


def _num(series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)


def _prior_max(highs: np.ndarray, i: int, n: int) -> float:
    if i < n:
        return float("nan")
    w = highs[i - n : i]
    if not np.all(np.isfinite(w)):
        return float("nan")
    return float(np.max(w))


def _prior_mean(xs: np.ndarray, i: int, n: int) -> float:
    if i < n:
        return float("nan")
    w = xs[i - n : i]
    w = w[np.isfinite(w)]
    if w.size < n:
        return float("nan")
    return float(np.mean(w))


def build_day_events(*, date: str, g: pd.DataFrame, sector_of: dict[str, str], clocks: tuple[str, ...] = CLOCKS) -> list[dict[str, Any]]:
    per: dict[str, dict[str, Any]] = {}
    for sym, sg in g.groupby("symbol", sort=False):
        sg = sg.sort_values("time_label")
        times = [str(t)[:5] for t in sg["time_label"].tolist()]
        o = _num(sg["open"])
        h = _num(sg["high"])
        l = _num(sg["low"])
        c = _num(sg["close"])
        v = _num(sg["volume"])
        va = _num(sg["trading_value"])
        e9 = ema(c, 9)
        e21 = ema(c, 21)
        vw = session_vwap(h, l, c, v, va)
        idx = {t: i for i, t in enumerate(times)}
        per[str(sym)] = {"t": times, "idx": idx, "o": o, "h": h, "l": l, "c": c, "v": v, "va": va, "e9": e9, "e21": e21, "vw": vw}

    events: list[dict[str, Any]] = []
    for clock in clocks:
        look1 = hhmm_add(clock, -1)
        look3 = hhmm_add(clock, -3)
        look5 = hhmm_add(clock, -5)
        look15 = hhmm_add(clock, -15)
        entry_t = hhmm_add(clock, 1)
        h5_t = hhmm_add(clock, 5)
        h15_t = hhmm_add(clock, int(PRIMARY_HORIZON_MIN))
        h30_t = hhmm_add(clock, 30)
        if entry_t is None or in_invalid_entry(entry_t):
            continue
        rows = []
        rets5 = []
        rets1 = []
        rets15 = []
        ema_flags = []
        vwap_flags = []
        vols = []
        vas = []
        for sym, rec in per.items():
            i = rec["idx"].get(clock)
            if i is None:
                continue
            c0 = rec["c"][i]
            if not np.isfinite(c0) or c0 == 0:
                continue

            def pret(hh: str | None) -> float:
                if not hh:
                    return float("nan")
                j = rec["idx"].get(hh)
                if j is None:
                    return float("nan")
                px = rec["c"][j]
                if not np.isfinite(px) or px == 0:
                    return float("nan")
                return float(c0 / px - 1.0)

            r1 = pret(look1)
            r3 = pret(look3)
            r5 = pret(look5)
            r15 = pret(look15)
            vw = rec["vw"][i]
            e9v = rec["e9"][i]
            e21v = rec["e21"][i]
            above_vwap = bool(np.isfinite(vw) and c0 > vw)
            prev_vwap = False
            if look1 and look1 in rec["idx"]:
                j = rec["idx"][look1]
                prev_vwap = bool(np.isfinite(rec["vw"][j]) and rec["c"][j] < rec["vw"][j])
            reclaim = bool(above_vwap and prev_vwap)
            brk = rec["c"][i] >= _prior_max(rec["h"], i, 20) if np.isfinite(_prior_max(rec["h"], i, 20)) else False
            vm = _prior_mean(rec["v"], i, 20)
            vol_rel = float(rec["v"][i] / vm) if np.isfinite(vm) and vm > 0 and np.isfinite(rec["v"][i]) else float("nan")
            x0 = _x0_bps(rec, entry_t, h5_t, h15_t, h30_t)
            row = {
                "date": date,
                "clock": clock,
                "symbol": sym,
                "sector": sector_of.get(sym) or "",
                "ret_1m": r1,
                "ret_3m": r3,
                "ret_5m": r5,
                "ret_15m": r15,
                "above_vwap": above_vwap,
                "vwap_reclaim": reclaim,
                "ema9_gt_ema21": bool(np.isfinite(e9v) and np.isfinite(e21v) and e9v > e21v),
                "ema_ready": bool(np.isfinite(e9v) and np.isfinite(e21v)),
                "pullback": bool(np.isfinite(r15) and np.isfinite(r5) and r15 > 0 and r5 < 0),
                "chase": bool(np.isfinite(r15) and np.isfinite(r5) and r15 > 0 and r5 > 0),
                "breakout20": bool(brk),
                "vol_rel20": vol_rel,
                "vol_expand": bool(np.isfinite(vol_rel) and vol_rel >= 1.5),
                **x0,
            }
            rows.append(row)
            rets5.append(r5)
            rets1.append(r1)
            rets15.append(r15)
            if row["ema_ready"]:
                ema_flags.append(1.0 if row["ema9_gt_ema21"] else 0.0)
            vwap_flags.append(1.0 if above_vwap else 0.0)
            vols.append(rec["v"][i])
            vas.append(rec["va"][i])
        if len(rows) < 20:
            continue
        up5 = _up_ratio(rets5)
        up1 = _up_ratio(rets1)
        up3 = _up_ratio([_ret3(per, r["symbol"], clock) for r in rows])
        med5 = _finite_median(rets5)
        disp5 = _finite_std(rets5)
        pos = np.array([x for x in rets5 if np.isfinite(x) and x > 0], dtype=float)
        lead_hhi = float(np.sum((pos / pos.sum()) ** 2)) if pos.size and pos.sum() > 0 else None
        top_n = max(int(round(0.10 * len(rows))), 1)
        order = sorted((x for x in rets5 if np.isfinite(x)), reverse=True)
        top_mass = float(sum(order[:top_n])) if order else 0.0
        all_pos = float(pos.sum()) if pos.size else 0.0
        lead_top10 = (top_mass / all_pos) if all_pos > 0 else None
        vol_active = float(np.mean([1.0 if np.isfinite(x) and x > 0 else 0.0 for x in vols])) if vols else None
        va_active = float(np.mean([1.0 if np.isfinite(x) and x > 0 else 0.0 for x in vas])) if vas else None
        sector_up: dict[str, list[float]] = defaultdict(list)
        for r, r5 in zip(rows, rets5):
            if np.isfinite(r5):
                sector_up[r["sector"]].append(r5)
        sector_br = {k: _up_ratio(vs) for k, vs in sector_up.items()}
        market_strong = up5 is not None and up5 >= 0.60
        market_weak = up5 is not None and up5 <= 0.40
        market_neutral = (not market_strong) and (not market_weak) and up5 is not None
        for r, r5 in zip(rows, rets5):
            r["active_n"] = len(rows)
            r["up_1m"] = up1
            r["up_3m"] = up3
            r["up_5m"] = up5
            r["median_ret_5m"] = med5
            r["dispersion_5m"] = disp5
            r["volume_active_ratio"] = vol_active
            r["va_active_ratio"] = va_active
            r["vwap_above_ratio"] = float(np.mean(vwap_flags)) if vwap_flags else None
            r["ema_breadth"] = float(np.mean(ema_flags)) if ema_flags else None
            r["leadership_hhi"] = lead_hhi
            r["leadership_top10_share"] = lead_top10
            r["sector_up_5m"] = sector_br.get(r["sector"])
            r["sector_n"] = len(sector_up.get(r["sector"]) or [])
            r["rs_5m"] = (float(r5) - float(med5)) if np.isfinite(r5) and med5 is not None else float("nan")
            r["rs_high"] = bool(np.isfinite(r["rs_5m"]) and r["rs_5m"] > 0)
            r["market_strong"] = market_strong
            r["market_weak"] = market_weak
            r["market_neutral"] = market_neutral
            r["sector_strong"] = r["sector_up_5m"] is not None and r["sector_up_5m"] >= 0.60
            r["sector_weak"] = r["sector_up_5m"] is not None and r["sector_up_5m"] <= 0.40
            r["same_bar_close_entry"] = False
            r["feature_available_at"] = hhmm_add(clock, 1)
            r["entry_bar"] = entry_t
            r["execution_x0"] = "next_bar_open_after_feature_bar_end"
            r["universe_conditioned_historical_validation"] = True
            events.append(r)
    return events


def in_invalid_entry(entry_t: str) -> bool:
    from research.cause_first_mechanism_discovery_v1.clock import in_lunch, parse_hhmm

    if in_lunch(entry_t):
        return True
    p = parse_hhmm(entry_t)
    if p is None:
        return True
    m = p[0] * 60 + p[1]
    return m < 9 * 60 or m >= 15 * 60 + 30


def _up_ratio(xs: list[float]) -> float | None:
    arr = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if arr.size < 8:
        return None
    return float(np.mean(arr > 0.0))


def _finite_median(xs: list[float]) -> float | None:
    arr = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if arr.size == 0:
        return None
    return float(np.median(arr))


def _finite_std(xs: list[float]) -> float | None:
    arr = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if arr.size < 8:
        return None
    return float(np.std(arr, ddof=1))


def _ret3(per: dict[str, dict[str, Any]], sym: str, clock: str) -> float:
    rec = per.get(sym)
    if not rec:
        return float("nan")
    look3 = hhmm_add(clock, -3)
    i = rec["idx"].get(clock)
    j = rec["idx"].get(look3) if look3 else None
    if i is None or j is None:
        return float("nan")
    a, b = rec["c"][i], rec["c"][j]
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return float("nan")
    return float(a / b - 1.0)


def _x0_bps(rec: dict[str, Any], entry_t: str, h5_t: str | None, h15_t: str | None, h30_t: str | None) -> dict[str, Any]:
    ie = rec["idx"].get(entry_t)
    px = rec["o"][ie] if ie is not None else float("nan")
    out = {"x0_entry_open": px if ie is not None else None, "x0_h5_bps": None, "x0_h15_bps": None, "x0_h30_bps": None, "x1_h15_bps": None}

    def mark(hh: str | None, key: str) -> None:
        if not hh or not np.isfinite(px) or px == 0:
            return
        if interval_crosses_lunch(entry_t, hh):
            return
        j = rec["idx"].get(hh)
        if j is None:
            return
        cl = rec["c"][j]
        if not np.isfinite(cl):
            return
        out[key] = float((cl / px - 1.0) * 10_000.0)

    mark(h5_t, "x0_h5_bps")
    mark(h15_t, "x0_h15_bps")
    mark(h30_t, "x0_h30_bps")
    if out["x0_h15_bps"] is not None:
        out["x1_h15_bps"] = float(out["x0_h15_bps"] - float(X1_TAX_BPS))
    return out


def build_events(*, minutes: pd.DataFrame, sector_of: dict[str, str], forbidden_dates: set[str]) -> list[dict[str, Any]]:
    if minutes.empty:
        return []
    if minutes["date"].isin(list(forbidden_dates)).any():
        raise RuntimeError("frozen_validation_accessed_during_event_build")
    events: list[dict[str, Any]] = []
    grouped = minutes.groupby("date", sort=True)
    n_dates = grouped.ngroups
    for i, (day, g) in enumerate(grouped, start=1):
        events.extend(build_day_events(date=str(day), g=g, sector_of=sector_of))
        if i % 25 == 0:
            print(f"EVENTS {i}/{n_dates} n={len(events)}", flush=True)
    if any(str(e.get("date")) in forbidden_dates for e in events):
        raise RuntimeError("frozen_validation_accessed_during_discovery")
    return events
