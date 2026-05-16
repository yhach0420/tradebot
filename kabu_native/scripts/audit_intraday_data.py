#!/usr/bin/env python3
"""
Audit intraday_1m CSV inventory for kabu_native replay.

例::
    python kabu_native/scripts/audit_intraday_data.py
    python kabu_native/scripts/audit_intraday_data.py --last-days 30
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_COLS = frozenset({"open", "high", "low", "close", "volume"})
TIMESTAMP_COLS = frozenset({"timestamp", "timestamp_utc", "time", "datetime"})


@dataclass(frozen=True)
class RootScan:
    name: str
    path: Path
    exists: bool


def _paths() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    return repo_root, native_root


def _symbol_from_filename(name: str) -> str:
    base = name
    if base.lower().endswith(".csv"):
        base = base[:-4]
    return base.upper()


def _quick_validate_csv(path: Path) -> tuple[bool, str | None]:
    if path.stat().st_size == 0:
        return False, "empty_csv"
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return False, "empty_csv"
            cols = {c.strip().lower() for c in header}
            if not REQUIRED_COLS.issubset(cols):
                missing = sorted(REQUIRED_COLS - cols)
                return False, f"invalid_columns:{','.join(missing)}"
            if not (cols & TIMESTAMP_COLS):
                return False, "invalid_columns:timestamp"
            if not any(row for row in reader):
                return False, "empty_csv"
    except OSError as e:
        return False, f"read_error:{e}"
    return True, None


def scan_root(root: Path, *, validate: bool) -> dict[str, dict[str, dict[str, Any]]]:
    """
    Returns: date -> symbol -> {path, bytes, valid, issue}
    """
    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    if not root.is_dir():
        return out

    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir() or not DATE_DIR_RE.match(day_dir.name):
            continue
        trade_date = day_dir.name
        for csv_path in sorted(day_dir.glob("*.csv")):
            sym = _symbol_from_filename(csv_path.name)
            valid, issue = (True, None)
            if validate:
                valid, issue = _quick_validate_csv(csv_path)
            out[trade_date][sym] = {
                "path": str(csv_path),
                "bytes": csv_path.stat().st_size,
                "valid": valid,
                "issue": issue,
            }
    return out


def merge_inventory(
    native: dict[str, dict[str, dict[str, Any]]],
    legacy: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    dates = sorted(set(native) | set(legacy))
    rows: list[dict[str, Any]] = []
    for d in dates:
        symbols = sorted(set(native.get(d, {})) | set(legacy.get(d, {})))
        for sym in symbols:
            n = native.get(d, {}).get(sym)
            lg = legacy.get(d, {}).get(sym)
            if n and lg:
                effective = "kabu_native"
                eff_path = n["path"]
            elif n:
                effective = "kabu_native"
                eff_path = n["path"]
            elif lg:
                effective = "legacy"
                eff_path = lg["path"]
            else:
                continue
            rec = n or lg
            rows.append(
                {
                    "trade_date": d,
                    "symbol": sym,
                    "in_kabu_native": bool(n),
                    "in_legacy": bool(lg),
                    "effective_source": effective,
                    "effective_path": eff_path,
                    "valid": rec.get("valid", True),
                    "issue": rec.get("issue") or "",
                    "bytes": rec.get("bytes", 0),
                }
            )
    return rows


def month_key(trade_date: str) -> str:
    return trade_date[:7]


def build_summary(
    rows: list[dict[str, Any]],
    *,
    roots: list[RootScan],
    last_days: int,
    as_of: date,
) -> dict[str, Any]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_symbol: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_date[r["trade_date"]].append(r)
        by_symbol[r["symbol"]].add(r["trade_date"])

    all_dates = sorted(by_date)
    date_csv_counts = {d: len(by_date[d]) for d in all_dates}

    def _month_stats(prefix: str) -> dict[str, Any]:
        month_dates = [d for d in all_dates if d.startswith(prefix)]
        csv_count = sum(date_csv_counts[d] for d in month_dates)
        return {
            "has_data": len(month_dates) > 0,
            "dates": month_dates,
            "date_count": len(month_dates),
            "csv_count": csv_count,
        }

    span_start = as_of - timedelta(days=last_days - 1)
    last_range_dates: list[str] = []
    cur = span_start
    while cur <= as_of:
        last_range_dates.append(cur.isoformat())
        cur += timedelta(days=1)

    dates_with_data_last = [d for d in last_range_dates if d in by_date]
    dates_missing_last = [d for d in last_range_dates if d not in by_date]

    if all_dates:
        min_d = date.fromisoformat(all_dates[0])
        max_d = date.fromisoformat(all_dates[-1])
        span_missing: list[str] = []
        cur2 = min_d
        while cur2 <= max_d:
            ds = cur2.isoformat()
            if ds not in by_date:
                span_missing.append(ds)
            cur2 += timedelta(days=1)
    else:
        min_d = max_d = None
        span_missing = []

    symbol_rows = [
        {
            "symbol": sym,
            "day_count": len(days),
            "first_date": min(days) if days else None,
            "last_date": max(days) if days else None,
        }
        for sym, days in sorted(by_symbol.items())
    ]

    invalid_rows = [r for r in rows if not r.get("valid", True)]

    return {
        "scanned_at_local": datetime.now().isoformat(timespec="seconds"),
        "as_of_date": as_of.isoformat(),
        "roots": [{"name": r.name, "path": str(r.path), "exists": r.exists} for r in roots],
        "date_count": len(all_dates),
        "symbol_count_unique": len(by_symbol),
        "total_effective_csv": len(rows),
        "date_range": {"min": all_dates[0] if all_dates else None, "max": all_dates[-1] if all_dates else None},
        "march_2026": _month_stats("2026-03"),
        "april_2026": _month_stats("2026-04"),
        "may_2026": _month_stats("2026-05"),
        "last_n_days": {
            "n": last_days,
            "from": span_start.isoformat(),
            "to": as_of.isoformat(),
            "has_data": len(dates_with_data_last) > 0,
            "dates_with_data": dates_with_data_last,
            "dates_missing": dates_missing_last,
            "date_count_with_data": len(dates_with_data_last),
        },
        "missing_dates_in_inventory_span": span_missing,
        "date_csv_counts": date_csv_counts,
        "symbols": symbol_rows,
        "invalid_csv_count": len(invalid_rows),
    }


def main() -> int:
    repo_root, native_root = _paths()

    parser = argparse.ArgumentParser(description="intraday_1m データ在庫監査")
    parser.add_argument("--last-days", type=int, default=30, help="直近 N 日の欠損判定（既定 30）")
    parser.add_argument("--as-of", default=None, help="基準日 YYYY-MM-DD（既定: 今日）")
    parser.add_argument("--no-validate", action="store_true", help="CSV 列検証をスキップ（存在のみ）")
    parser.add_argument("--output-stamp", default=None, help="出力ファイル日付 YYYYMMDD")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    stamp = args.output_stamp or as_of.strftime("%Y%m%d")

    roots = [
        RootScan("kabu_native", native_root / "data" / "intraday_1m", True),
        RootScan("legacy", repo_root / "data" / "intraday_1m", True),
    ]

    native_scan = scan_root(roots[0].path, validate=not args.no_validate)
    legacy_scan = scan_root(roots[1].path, validate=not args.no_validate)
    roots[0] = RootScan(roots[0].name, roots[0].path, roots[0].path.is_dir())
    roots[1] = RootScan(roots[1].name, roots[1].path, roots[1].path.is_dir())

    rows = merge_inventory(native_scan, legacy_scan)
    summary = build_summary(rows, roots=roots, last_days=args.last_days, as_of=as_of)

    out_dir = native_root / "results" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"intraday_inventory_{stamp}.csv"
    json_path = out_dir / f"intraday_inventory_{stamp}.json"

    fieldnames = [
        "trade_date",
        "symbol",
        "in_kabu_native",
        "in_legacy",
        "effective_source",
        "effective_path",
        "valid",
        "issue",
        "bytes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    payload = {
        "meta": {
            "component": "kabu_native.audit_intraday_data",
            "csv_path": str(csv_path.relative_to(repo_root)),
            "json_path": str(json_path.relative_to(repo_root)),
        },
        "summary": summary,
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    s = summary
    print(f"dates={s['date_count']} symbols={s['symbol_count_unique']} csv={s['total_effective_csv']}")
    print(f"march_2026 has_data={s['march_2026']['has_data']} dates={s['march_2026']['date_count']}")
    print(f"april_2026 has_data={s['april_2026']['has_data']} dates={s['april_2026']['date_count']}")
    print(f"may_2026 has_data={s['may_2026']['has_data']} dates={s['may_2026']['date_count']}")
    print(
        f"last_{args.last_days}d with_data={s['last_n_days']['date_count_with_data']} "
        f"missing={len(s['last_n_days']['dates_missing'])}"
    )
    print(f"CSV: {csv_path.relative_to(repo_root)}")
    print(f"JSON: {json_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
