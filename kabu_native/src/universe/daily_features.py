"""
Phase 113: Full-market previous-day features CSV (shadow / data infra).
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from universe.opening_screen import PreviousDayFeatures, fetch_previous_day_yfinance

PUSH_LIMIT = 50
FOCUS_SYMBOLS = ("3905.T", "6613.T")

FEATURE_FIELDS = (
    "symbol",
    "symbol_key",
    "exchange",
    "market",
    "close",
    "volume",
    "trading_value",
    "intraday_range_pct",
    "atr_pct",
    "volume_surge_5",
    "volatility_liquidity_score",
    "data_source",
    "trade_date",
)


def features_csv_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"features_{day_stamp}.csv"


def universe_csv_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_vol_liq_dynamic50_{day_stamp}.csv"


def generate_features_csv(
    *,
    symbols: Sequence[str],
    trade_date: date,
    symbol_meta: Mapping[str, Mapping[str, Any]],
    out_path: Path,
) -> dict[str, Any]:
    prev_by_sym = fetch_previous_day_yfinance(symbols, trade_date)
    rows: list[dict[str, Any]] = []
    valid_vol_liq = 0

    for sym in symbols:
        p = prev_by_sym.get(sym)
        if not p:
            continue
        m = symbol_meta.get(sym, {})
        if p.volatility_liquidity_score is not None:
            valid_vol_liq += 1
        rows.append(
            {
                "symbol": sym,
                "symbol_key": str(m.get("symbol_key") or f"{sym.replace('.T', '')}@1"),
                "exchange": int(m.get("exchange") or 1),
                "market": str(m.get("market") or ""),
                "close": p.close,
                "volume": p.volume,
                "trading_value": p.trading_value,
                "intraday_range_pct": p.intraday_range_pct,
                "atr_pct": p.atr_pct,
                "volume_surge_5": p.volume_surge_5,
                "volatility_liquidity_score": p.volatility_liquidity_score,
                "data_source": p.data_source,
                "trade_date": trade_date.isoformat(),
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(FEATURE_FIELDS), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in FEATURE_FIELDS})

    return {
        "path": str(out_path),
        "row_count": len(rows),
        "valid_vol_liq_count": valid_vol_liq,
        "requested": len(symbols),
        "fetched": len(prev_by_sym),
    }


def load_features_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            if not sym:
                continue
            if not sym.upper().endswith(".T"):
                sym = f"{sym}.T"
            rows.append({k: str(v or "").strip() for k, v in row.items()} | {"symbol": sym})
    return rows


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def select_top50_by_vol_liq(feature_rows: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    scored: list[tuple[float, dict[str, str]]] = []
    for row in feature_rows:
        vl = _as_float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        scored.append((vl, dict(row)))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, str]] = []
    for i, (_, row) in enumerate(scored[:PUSH_LIMIT], start=1):
        row["rank"] = str(i)
        out.append(row)
    return out
