"""
Phase 109: Build shadow universe CSV from Phase108 opening_dynamic50_0905 artifact.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

PUSH_LIMIT = 50

UNIVERSE_FIELDS = (
    "symbol",
    "symbol_key",
    "exchange",
    "passed",
    "source_bucket",
    "selected_reason",
    "opening_daytrade_score",
    "previous_day_vol_liq_score",
    "early_momentum_score",
    "early_trading_value_score",
    "early_range_score",
    "market",
    "rank",
)


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rank_scores(values: Sequence[Optional[float]]) -> list[float]:
    indexed = [(i, v) for i, v in enumerate(values) if v is not None and not math.isnan(v)]
    if not indexed:
        return [0.0] * len(values)
    indexed.sort(key=lambda x: x[1])
    n = len(indexed)
    out = [0.0] * len(values)
    for rank_i, (orig_i, _) in enumerate(indexed):
        out[orig_i] = rank_i / max(n - 1, 1)
    return out


def opening_0905_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"opening_dynamic50_0905_{day_stamp}.csv"


def universe_output_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_opening_dynamic50_{day_stamp}.csv"


def load_opening_dynamic50_0905(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = str(row.get("symbol") or "").strip()
            if not sym:
                continue
            if not sym.upper().endswith(".T"):
                sym = f"{sym}.T"
            rows.append({k: str(v or "").strip() for k, v in row.items()} | {"symbol": sym})
    return rows


def build_universe_rows(opening_rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    """Map opening_dynamic50_0905 rows → shadow universe CSV (no static27)."""
    sorted_rows = sorted(
        opening_rows,
        key=lambda r: int(r.get("rank") or 999),
    )[:PUSH_LIMIT]

    tv_vals = [_as_float(r.get("trading_value_proxy")) for r in sorted_rows]
    rng_vals = [_as_float(r.get("range_pct_5m")) for r in sorted_rows]
    tv_rank = _rank_scores(tv_vals)
    rng_rank = _rank_scores(rng_vals)

    out: list[dict[str, Any]] = []
    for i, row in enumerate(sorted_rows):
        sym = row["symbol"]
        ex = int(row.get("exchange") or 1)
        key = str(row.get("symbol_key") or f"{sym.replace('.T', '')}@{ex}")
        out.append(
            {
                "symbol": sym,
                "symbol_key": key,
                "exchange": ex,
                "passed": "True",
                "source_bucket": "opening_dynamic50",
                "selected_reason": "opening_daytrade_score_top50",
                "opening_daytrade_score": row.get("opening_daytrade_score") or "",
                "previous_day_vol_liq_score": row.get("previous_day_vol_liq_score") or "",
                "early_momentum_score": row.get("early_momentum_score") or "",
                "early_trading_value_score": round(tv_rank[i], 6),
                "early_range_score": round(rng_rank[i], 6),
                "market": row.get("market") or "",
                "rank": row.get("rank") or str(i + 1),
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

    add("total_count_50", len(rows) == PUSH_LIMIT, f"total={len(rows)}")
    add("no_duplicate_symbols", dup == 0, f"duplicates={dup}")
    add("source_bucket_opening_dynamic50", buckets == {"opening_dynamic50"}, f"buckets={buckets}")
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
