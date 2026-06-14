#!/usr/bin/env python3
"""
Phase252-SectorHeat-Trade-Attribution (review only)

Decompose Phase249 shadow pattern outperformance vs actual.
Observation only — no Runtime / Universe / Entry / YAML changes.

Output:
  kabu_native/results/reports/phase252_sector_heat_trade_attribution_summary.json
  kabu_native/results/reports/phase252_added_removed_attribution.csv
  kabu_native/results/reports/phase252_avoided_loss_analysis.csv
  kabu_native/results/reports/phase252_pattern_similarity.csv
  kabu_native/results/reports/phase252_day_level_delta.csv
  kabu_native/results/reports/phase252_sector_heat_report.md
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
    parser = argparse.ArgumentParser(description="Phase252 sector heat trade attribution")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from research.market_sector_heat_trade_attribution import MarketSectorHeatTradeAttribution

    t0 = time.monotonic()
    job = MarketSectorHeatTradeAttribution(repo_root=REPO, reports_dir=args.reports_dir)
    result = job.run()
    paths = job.write_outputs(result)

    overlap = result.get("trade_overlap_days") or []
    print(
        f"phase252_trade_attribution wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase252 Sector Heat Trade Attribution ===", flush=True)
    print(f"trade overlap days: {len(overlap)} ({', '.join(overlap)})", flush=True)
    for row in result.get("_avoided_loss_rows") or []:
        print(
            f"{row.get('pattern')}: net={row.get('net_attribution_pnl_yen_100')} "
            f"driver={row.get('primary_driver')}",
            flush=True,
        )
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
