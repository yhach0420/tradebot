"""
Phase 42: Daily data accumulation status for kabu_native storage roots.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from research.oos_data_availability import build_data_availability_for_oos, collect_trading_days
from storage.intraday_recorder import IntradayRecorder, validate_intraday_csv
from storage.push_recorder import PushRecorder
from storage.symbol_sources import SymbolSpec


def build_data_accumulation_status(
    *,
    native_root: Path,
    repo_root: Path,
    trade_date: str,
    expected_symbols: Sequence[SymbolSpec],
) -> dict[str, Any]:
    sym_list = [s.symbol for s in expected_symbols]
    recorder = IntradayRecorder(native_root)
    push = PushRecorder(native_root, trade_date)

    intraday_rows: list[dict[str, Any]] = []
    missing_rows = 0
    invalid_rows = 0
    for spec in expected_symbols:
        p = recorder.csv_path(trade_date, spec.symbol)
        if not p.is_file():
            missing_rows += 1
            intraday_rows.append(
                {
                    "symbol": spec.symbol,
                    "csv_exists": False,
                    "row_count": 0,
                    "valid": False,
                    "issues": "missing_csv",
                }
            )
            continue
        v = validate_intraday_csv(p)
        if not v.ok:
            invalid_rows += 1
        intraday_rows.append(
            {
                "symbol": spec.symbol,
                "csv_exists": True,
                "row_count": v.row_count,
                "valid": v.ok,
                "issues": ";".join(v.issues) if v.issues else "",
                "path": str(p),
            }
        )

    push_summary = push.summarize(sym_list)
    native_days = collect_trading_days([recorder.intraday_root])
    legacy_days = collect_trading_days([repo_root / "data" / "intraday_1m"])

    oos_native_only = build_data_availability_for_oos(
        data_roots=[recorder.intraday_root],
        push_jsonl_paths=[native_root / "data" / "push_jsonl"],
    )

    coverage_ratio = (
        (len(sym_list) - missing_rows) / len(sym_list) if sym_list else 0.0
    )

    return {
        "phase": 42,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "expected_symbol_count": len(sym_list),
        "intraday_csv_present": len(sym_list) - missing_rows,
        "intraday_csv_valid": sum(1 for r in intraday_rows if r.get("valid")),
        "intraday_csv_missing": missing_rows,
        "intraday_csv_invalid": invalid_rows,
        "symbol_coverage_ratio": round(coverage_ratio, 4),
        "push_jsonl_present": push_summary.get("jsonl_present"),
        "push_jsonl": push_summary,
        "intraday_symbols": intraday_rows,
        "kabu_native_trading_days": native_days,
        "kabu_native_trading_day_count": len(native_days),
        "legacy_trading_day_count": len(legacy_days),
        "oos_readiness_native": oos_native_only,
        "pilot_sample_notes": {
            "may16_plus_required_for_oos_may_late": True,
            "combined_trades_gate": 100,
            "symbols_coverage_gate": 0.70,
        },
    }


def write_accumulation_reports(
    report: dict[str, Any],
    *,
    reports_dir: Path,
    day_key: str | None = None,
) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    key = day_key or date.today().strftime("%Y%m%d")
    json_path = reports_dir / f"data_accumulation_status_{key}.json"
    csv_path = reports_dir / f"data_accumulation_status_{key}.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = report.get("intraday_symbols") or []
    fields = ["symbol", "csv_exists", "row_count", "valid", "issues", "path"]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return json_path, csv_path
