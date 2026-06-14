#!/usr/bin/env python3
"""
Phase265-Structural-Trades-Backfill-For-Research (review only)

Backfill structural_trades.csv for 20260529-20260612 live sessions, then rerun Phase263.

Output:
  kabu_native/results/reports/phase265_structural_trades_backfill_summary.json
  kabu_native/results/reports/phase265_structural_trades_backfill_by_session.csv
  kabu_native/results/reports/phase265_phase263_rerun_summary.json
  kabu_native/results/reports/phase265_report.md

Example::
    python kabu_native/scripts/run_phase265_structural_trades_backfill.py
    python kabu_native/scripts/run_phase265_structural_trades_backfill.py --skip-phase263-rerun
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
    parser = argparse.ArgumentParser(description="Phase265 structural trades backfill")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--period-start", type=str, default=None)
    parser.add_argument("--period-end", type=str, default=None)
    parser.add_argument("--skip-phase263-rerun", action="store_true")
    args = parser.parse_args()

    _bootstrap()
    from research.structural_trades_backfill import StructuralTradesBackfillJob

    t0 = time.monotonic()
    job = StructuralTradesBackfillJob(repo_root=REPO, reports_dir=args.reports_dir)
    run_kwargs: dict = {"skip_phase263_rerun": args.skip_phase263_rerun}
    if args.period_start:
        run_kwargs["period_start"] = args.period_start
    if args.period_end:
        run_kwargs["period_end"] = args.period_end
    result = job.run(**run_kwargs)
    paths = job.write_outputs(result)

    backfill_summary = (result.get("backfill") or {}).get("summary") or {}
    verification = (result.get("phase263_rerun") or {}).get("verification") or {}

    print(f"phase265_structural_trades_backfill wall_runtime_sec={round(time.monotonic() - t0, 1)}", flush=True)
    print("\n=== Phase265 Structural Trades Backfill ===", flush=True)
    for key in (
        "processed_session_count",
        "generated_structural_trades_count",
        "skipped_existing_count",
        "skipped_push_replay_count",
        "failed_session_count",
        "rows_generated_total",
    ):
        print(f"{key}: {backfill_summary.get(key)}", flush=True)
    if verification:
        print("\n=== Phase263 rerun verification ===", flush=True)
        for key in (
            "period_days_nonzero",
            "trade_count_nonzero",
            "summary_by_equity_risk_pct_generated",
            "all_checks_passed",
        ):
            print(f"{key}: {verification.get(key)}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
