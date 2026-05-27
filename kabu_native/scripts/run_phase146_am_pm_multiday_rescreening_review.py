#!/usr/bin/env python3
"""Phase 146: Multi-day AM/PM rescreening review (features + universe + performance)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
SMALL_PAPER = NATIVE / "results" / "small_paper"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for row in rows:
        for k in row:
            if k not in cols:
                cols.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 146 AM/PM multi-day rescreening review")
    parser.add_argument("--trade-date", action="append", default=None)
    parser.add_argument("--force-features", action="store_true", help="Regenerate features CSVs")
    parser.add_argument("--force-universe", action="store_true", help="Rebuild AM/PM universe CSVs")
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.am_pm_multiday_rescreening_review import (
        TARGET_TRADE_DATES,
        run_phase146_review,
    )

    trade_dates = args.trade_date or list(TARGET_TRADE_DATES)
    result = run_phase146_review(
        repo_root=ROOT,
        reports_dir=REPORTS,
        small_paper_root=SMALL_PAPER,
        push_root=PUSH_ROOT,
        trade_dates=trade_dates,
        force_features=args.force_features,
        force_universe=args.force_universe,
    )

    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")
    out_json = REPORTS / "phase146_am_pm_multiday_rescreening_review.json"
    daily_csv = REPORTS / "phase146_am_pm_daily_summary.csv"
    pm_added_csv = REPORTS / "phase146_pm_added_symbol_performance.csv"
    am_removed_csv = REPORTS / "phase146_am_removed_symbol_performance.csv"

    report: dict[str, Any] = {
        "phase": 146,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "trade_dates": result["trade_dates"],
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "am_pm_rescreening_worthwhile",
            "B": "am_pm_rescreening_not_needed",
            "C": "need_more_intraday_data",
            "D": "mixed_result",
        },
        "aggregate": result.get("aggregate"),
        "feature_generation": result.get("feature_generation"),
        "universe_rebuild": result.get("universe_rebuild"),
        "core_count": result.get("core_count"),
        "master_symbol_count": result.get("master_symbol_count"),
        "methodology": {
            "review_only": True,
            "same_score_am_pm": True,
            "universe": "Core10 + Dynamic40 (Phase117)",
            "pm_rescreen": "pm_composite same coefficients; intraday push for PM ranking only",
            "no_pilot_yaml_change": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "daily_csv": _rel(daily_csv),
            "pm_added_csv": _rel(pm_added_csv),
            "am_removed_csv": _rel(am_removed_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(daily_csv, result["daily_rows"])
    _write_csv(pm_added_csv, result["pm_added_symbol_performance"])
    _write_csv(am_removed_csv, result["am_removed_symbol_performance"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "aggregate": result.get("aggregate"),
                "daily_rows": len(result["daily_rows"]),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
