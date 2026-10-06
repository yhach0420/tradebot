"""Reference market state architecture. Not an ENTRY signal. No future return."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.daytrade_historical_research_foundation_v2.isolation import REF


def _load_minutes(symbols: list[str], *, date_from: str | None = None) -> pd.DataFrame | None:
    frames = []
    root = REF / "minute"
    if not root.is_dir():
        return None
    want = set(symbols)
    for p in root.glob("minute_*.parquet"):
        try:
            df = pd.read_parquet(p, columns=["symbol", "date", "time_label", "open", "high", "low", "close", "volume"])
        except Exception:
            continue
        if df.empty:
            continue
        df = df[df["symbol"].astype(str).isin(want)]
        if date_from:
            df = df[df["date"].astype(str) >= str(date_from)]
        if df.empty:
            continue
        frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def daily_breadth(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["date"] = d["date"].astype(str)
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d = d.sort_values(["symbol", "date", "time_label"])
    d["prev"] = d.groupby("symbol")["close"].shift(1)
    d["up"] = d["close"] > d["prev"]
    g = d.groupby("date")
    out = g.agg(
        n=("symbol", "nunique"),
        up_ratio=("up", "mean"),
        median_ret=("close", lambda s: float(np.nanmedian(np.diff(np.log(pd.to_numeric(s, errors="coerce").dropna().replace(0, np.nan))))) if s.notna().sum() > 3 else np.nan),
        vol_active_ratio=("volume", lambda s: float((pd.to_numeric(s, errors="coerce") > 0).mean())),
    ).reset_index()
    disp = []
    for _day, gg in g:
        rets = []
        for _sym, sg in gg.groupby("symbol"):
            c = pd.to_numeric(sg["close"], errors="coerce").dropna()
            if len(c) >= 2 and c.iloc[0]:
                rets.append(float(c.iloc[-1] / c.iloc[0] - 1.0))
        disp.append(float(np.nanstd(rets)) if rets else np.nan)
    out["cross_section_dispersion"] = disp
    return out


def compression_agreement(*, pool_df: pd.DataFrame, trade_symbols: list[str]) -> dict[str, Any]:
    if pool_df is None or pool_df.empty or not trade_symbols:
        return {"buildable": False, "reason": "minute_or_trade_set_empty"}
    full = daily_breadth(pool_df)
    sub = daily_breadth(pool_df[pool_df["symbol"].astype(str).isin(set(trade_symbols))])
    merged = full.merge(sub, on="date", suffixes=("_pool", "_trade"))
    if len(merged) < 8:
        return {"buildable": True, "agreement_n": int(len(merged)), "reason": "too_few_days"}
    a = pd.to_numeric(merged["up_ratio_pool"], errors="coerce")
    b = pd.to_numeric(merged["up_ratio_trade"], errors="coerce")
    mask = a.notna() & b.notna()
    if int(mask.sum()) < 8:
        return {"buildable": True, "reason": "up_ratio_nan"}
    corr_raw = float(np.corrcoef(a[mask], b[mask])[0, 1])
    corr = corr_raw if np.isfinite(corr_raw) else None
    da = pd.to_numeric(merged["cross_section_dispersion_pool"], errors="coerce")
    db = pd.to_numeric(merged["cross_section_dispersion_trade"], errors="coerce")
    m2 = da.notna() & db.notna()
    disp_corr = None
    if int(m2.sum()) >= 8:
        disp_raw = float(np.corrcoef(da[m2], db[m2])[0, 1])
        disp_corr = disp_raw if np.isfinite(disp_raw) else None
    sufficient = corr is not None and corr >= 0.90
    return {
        "buildable": True,
        "used_as_entry_signal": False,
        "n_days": int(len(merged)),
        "up_ratio_corr": corr,
        "dispersion_corr": disp_corr,
        "trade_eligible_reproduces_pool_state": sufficient,
        "context_only_n_if_sufficient": 0 if sufficient else None,
        "threshold_precommitted_corr": 0.90,
        "future_return_used": False,
    }


ARCHITECTURE = {
    "fields": [
        "1m_up_ratio",
        "3m_up_ratio",
        "5m_up_ratio",
        "median_return",
        "cross_sectional_dispersion",
        "volume_active_ratio",
        "vwap_above_ratio",
        "ema9_gt_ema21_ratio",
        "sector_breadth",
        "sector_activity",
        "leadership_concentration",
    ],
    "join_rule": "available_at_jst <= decision_time_jst",
    "entry_use_this_run": False,
    "computed_now": ["1m_up_ratio", "median_return", "cross_sectional_dispersion", "volume_active_ratio"],
    "deferred": ["3m_up_ratio", "5m_up_ratio", "vwap_above_ratio", "ema9_gt_ema21_ratio", "sector_breadth", "sector_activity", "leadership_concentration"],
}
