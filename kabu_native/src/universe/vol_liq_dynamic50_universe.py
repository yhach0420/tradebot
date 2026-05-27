"""
Phase 113: Shadow universe from full-market previous-day vol_liq top50.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

PUSH_LIMIT = 50
FOCUS_SYMBOLS = ("3905.T", "6613.T")

UNIVERSE_FIELDS = (
    "symbol",
    "symbol_key",
    "exchange",
    "passed",
    "source_bucket",
    "selected_reason",
    "volatility_liquidity_score",
    "atr_pct",
    "intraday_range_pct",
    "trading_value",
    "volume",
    "rank",
)


def build_universe_rows(top50_features: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in top50_features:
        sym = row["symbol"]
        ex = int(row.get("exchange") or 1)
        out.append(
            {
                "symbol": sym,
                "symbol_key": str(row.get("symbol_key") or f"{sym.replace('.T', '')}@{ex}"),
                "exchange": ex,
                "passed": "True",
                "source_bucket": "vol_liq_dynamic50",
                "selected_reason": "previous_day_vol_liq_top50",
                "volatility_liquidity_score": row.get("volatility_liquidity_score") or "",
                "atr_pct": row.get("atr_pct") or "",
                "intraday_range_pct": row.get("intraday_range_pct") or "",
                "trading_value": row.get("trading_value") or "",
                "volume": row.get("volume") or "",
                "rank": row.get("rank") or "",
            }
        )
    return out


def write_universe_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(UNIVERSE_FIELDS), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in UNIVERSE_FIELDS})


def validate_universe_csv(path: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True

    def add(cid: str, passed: bool, detail: str) -> None:
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"check_id": cid, "passed": passed, "detail": detail})

    if not path.is_file():
        add("file_exists", False, "missing")
        return {"passed": False, "checks": checks, "total_count": 0}

    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        for col in UNIVERSE_FIELDS:
            add(f"column_{col}", col in fields, col)
        for row in reader:
            rows.append({k: str(v or "") for k, v in row.items()})

    syms = [r.get("symbol", "") for r in rows]
    dup = len(syms) - len(set(syms))
    buckets = {r.get("source_bucket") for r in rows}
    passed_n = sum(1 for r in rows if str(r.get("passed", "")).lower() in ("true", "1", "yes"))

    add("symbol_count_50", len(rows) == PUSH_LIMIT, f"total={len(rows)}")
    add("no_duplicate_symbols", dup == 0, f"duplicates={dup}")
    add("source_bucket_vol_liq_dynamic50", buckets == {"vol_liq_dynamic50"}, f"buckets={buckets}")
    add("all_passed_true", passed_n == len(rows), f"passed={passed_n}/{len(rows)}")
    add("total_count_le_50", len(rows) <= PUSH_LIMIT, f"total={len(rows)}")

    return {
        "passed": ok,
        "checks": checks,
        "total_count": len(rows),
        "symbol_count": len(rows),
        "duplicate_count": dup,
        "source_buckets": sorted(buckets),
    }


def diagnostics(
    universe_rows: Sequence[Mapping[str, Any]],
    *,
    static27: set[str],
    feature_meta: Mapping[str, Any],
    feature_top50: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    syms = [str(r.get("symbol") or "") for r in universe_rows]
    market_src = feature_top50 if feature_top50 else universe_rows
    markets = Counter(str(r.get("market") or "unknown") for r in market_src)
    if not markets:
        markets = Counter("unknown" for _ in universe_rows)

    top10 = [
        {
            "rank": r.get("rank"),
            "symbol": r.get("symbol"),
            "volatility_liquidity_score": r.get("volatility_liquidity_score"),
        }
        for r in sorted(universe_rows, key=lambda x: int(x.get("rank") or 999))[:10]
    ]

    uni_set = set(syms)
    return {
        "focus_3905_in_top50": "3905.T" in uni_set,
        "focus_6613_in_top50": "6613.T" in uni_set,
        "market_distribution": dict(markets),
        "top10_symbols": top10,
        "static27_overlap_count": len(uni_set & static27),
        "static27_overlap_symbols": sorted(uni_set & static27),
        "features_summary": feature_meta,
    }
