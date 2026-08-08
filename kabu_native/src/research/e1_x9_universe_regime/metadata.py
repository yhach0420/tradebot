"""As-of metadata loaders — no post-cutoff or retroactive fill."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from . import ASOF_CUTOFF, TARGET_SYMBOL

TRADEBOT = Path(r"C:\Users\yhach\Documents\tradebotfile")
JPX_PATH = TRADEBOT / "data" / "jpx" / "all_symbols.csv"
FEATURES_DIR = TRADEBOT / "kabu_native" / "results" / "reports"


def _norm_sym(s: Any) -> str:
    return str(s).replace(".T", "").replace(".t", "").strip()


def _file_pub_date(path: Path) -> str:
    """YYYYMMDD from mtime in local time — conservative publication proxy."""
    ts = datetime.fromtimestamp(path.stat().st_mtime)
    return ts.strftime("%Y%m%d")


def load_jpx_segment_scale() -> dict[str, dict[str, Any]]:
    """Market segment + TOPIX scale_category. Snapshot file (May 2026 build)."""
    if not JPX_PATH.exists():
        return {}
    pub = _file_pub_date(JPX_PATH)
    # Also use known build date from exploration if earlier
    effective = min(pub, "20260522")
    asof_valid = effective <= ASOF_CUTOFF and pub <= ASOF_CUTOFF
    df = pd.read_csv(JPX_PATH)
    out = {}
    for _, row in df.iterrows():
        sym = _norm_sym(row["symbol"])
        out[sym] = {
            "symbol": sym,
            "market": str(row.get("market") or "other").lower(),
            "scale_category": str(row.get("scale_category") or "-"),
            "name": row.get("name"),
            "source_name": "JPX_listed_issues_snapshot",
            "source_location": str(JPX_PATH),
            "effective_date": effective,
            "publication_date": pub,
            "retrieved_at": datetime.now().isoformat(timespec="seconds"),
            "asof_valid": asof_valid,
        }
    return out


def load_turnover_20d(symbols: set[str]) -> dict[str, dict[str, Any]]:
    """20 trading-day mean trading_value ending latest features day <= cutoff."""
    files = sorted(FEATURES_DIR.glob("features_2026*.csv"))
    usable = [f for f in files if f.stem.split("_")[1] <= ASOF_CUTOFF]
    if not usable:
        return {}
    # take last 20 available feature days ending at latest <= cutoff
    window = usable[-20:]
    end_day = window[-1].stem.split("_")[1]
    frames = []
    for f in window:
        d = pd.read_csv(f)
        d["symbol"] = d["symbol"].map(_norm_sym)
        d["trade_date"] = f.stem.split("_")[1]
        frames.append(d[["symbol", "trading_value", "volume", "market", "trade_date"]])
    all_df = pd.concat(frames, ignore_index=True)
    out = {}
    # dated features: publication = trade date in filename (not filesystem mtime)
    pub = end_day
    for sym in symbols:
        sub = all_df[all_df["symbol"] == sym]
        if sub.empty:
            continue
        tv = pd.to_numeric(sub["trading_value"], errors="coerce").dropna()
        vol = pd.to_numeric(sub["volume"], errors="coerce").dropna()
        if len(tv) == 0:
            continue
        out[sym] = {
            "symbol": sym,
            "avg_trading_value_20d": float(tv.mean()),
            "avg_volume_20d": float(vol.mean()) if len(vol) else None,
            "n_days_in_avg": int(len(tv)),
            "window_end": end_day,
            "window_start": window[0].stem.split("_")[1],
            "source_name": "features_csv_20d_mean",
            "source_location": str(FEATURES_DIR),
            "effective_date": end_day,
            "publication_date": pub,
            "retrieved_at": datetime.now().isoformat(timespec="seconds"),
            "asof_valid": end_day <= ASOF_CUTOFF and pub <= ASOF_CUTOFF,
        }
    return out


def market_cap_asof_status() -> dict[str, Any]:
    """PUSH TotalMarketValue starts 20260721 — not as-of valid for this audit."""
    return {
        "field": "market_cap_total_market_value",
        "available_asof": False,
        "reason": "market_capture PUSH TotalMarketValue begins 20260721 (> asof cutoff 20260720)",
        "asof_valid": False,
        "coverage_symbol": 0.0,
        "coverage_episode": 0.0,
    }


def direct_ownership_status() -> dict[str, Any]:
    return {
        "field": "direct_institutional_ownership",
        "available_asof": False,
        "reason": "no institutional/foreign ownership or free-float historical store in research tree",
        "asof_valid": False,
        "status": "DIRECT_INSTITUTIONAL_DATA_NOT_EVALUABLE",
        "coverage_symbol": 0.0,
        "coverage_episode": 0.0,
    }


def assign_index_status(scale: Optional[str]) -> str:
    if not scale or scale == "-" or scale.lower() == "nan":
        return "NON_MAJOR_INDEX"
    if "TOPIX" in str(scale).upper() or "NIKKEI" in str(scale).upper() or "JPX" in str(scale).upper():
        return "MAJOR_INDEX_MEMBER"
    return "NON_MAJOR_INDEX"


def assign_market_segment(market: Optional[str]) -> str:
    m = str(market or "other").lower()
    if m == "prime":
        return "PRIME"
    if m == "standard":
        return "STANDARD"
    if m == "growth":
        return "GROWTH"
    return "OTHER"


def tercile_labels(values: dict[str, float]) -> dict[str, str]:
    """Cross-symbol terciles on provided map symbol->value. Equal-count bins."""
    if not values:
        return {}
    items = sorted(values.items(), key=lambda x: x[1])
    n = len(items)
    out = {}
    for i, (sym, _) in enumerate(items):
        if i < n / 3:
            out[sym] = "LOW"
        elif i < 2 * n / 3:
            out[sym] = "MID"
        else:
            out[sym] = "HIGH"
    return out
