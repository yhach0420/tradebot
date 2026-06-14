#!/usr/bin/env python3
"""
Phase246-SectorHeat-Observation (review only)

Measure predictive power of sector heat continuing to the next day.
Observation only — no Universe or Entry changes.

Output:
  kabu_native/results/reports/phase246_sector_heat_*.json|csv|md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase246 sector heat observation")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--min-day", type=str, default=None, help="YYYYMMDD inclusive")
    parser.add_argument("--max-day", type=str, default=None, help="YYYYMMDD inclusive")
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat import MarketSectorHeatObservation

    t0 = time.monotonic()
    audit = MarketSectorHeatObservation(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        min_day=args.min_day,
        max_day=args.max_day,
    )
    result = audit.run()
    paths = audit.write_outputs(result)

    summary = {k: v for k, v in result.items() if not k.startswith("_")}
    val = summary.get("validation_summary") or {}
    print(
        f"phase246_sector_heat wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase246 Sector Heat Observation ===", flush=True)
    print(f"intraday days: {summary.get('intraday_day_count')}", flush=True)
    print(f"tomorrow top3 rows: {summary.get('tomorrow_top3_row_count')}", flush=True)
    print(f"validation pairs: {val.get('validation_day_count')}", flush=True)
    print(
        f"predicted sector PF: {val.get('predicted_sector_profit_factor_aggregate')} "
        f"PnL: {val.get('predicted_sector_pnl_yen_100_total')} "
        f"entries: {val.get('predicted_sector_trade_count_total')} "
        f"win_rate: {val.get('predicted_sector_win_rate_aggregate')}",
        flush=True,
    )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
