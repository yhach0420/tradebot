#!/usr/bin/env python3
"""
Phase268-Capital-Simulation-Reconciliation (review only)

Output:
  kabu_native/results/reports/accepted_vs_rejected.csv
  kabu_native/results/reports/reject_reason_breakdown.csv
  kabu_native/results/reports/phase268_reconciliation_summary.json
  kabu_native/results/reports/phase268_report.md
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


def _load_phase381_trades(*, min_day: str, max_day: str) -> list[dict]:
    from research.phase376_production_daily_pnl_review import discover_session_roots, discover_sessions_for_phase376
    from research.phase381_winner_profile_review import load_session_winner_profile_trades

    sessions = discover_sessions_for_phase376(
        discover_session_roots(REPO),
        min_day=min_day,
        max_day=max_day,
        all_available=True,
    )
    trades: list[dict] = []
    for meta in sessions:
        day = str(meta.get("day_key") or meta.get("day") or "")
        if day < min_day or (max_day and day > max_day):
            continue
        result = load_session_winner_profile_trades(meta, reports_dir=REPORTS)
        if result.get("error") and not result.get("all_trades"):
            continue
        trades.extend(result.get("all_trades") or [])
    return trades


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase268 capital simulation reconciliation")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--period-start", type=str, default=None)
    parser.add_argument("--load-phase381", action="store_true", help="Reload phase381 trades for universe repro")
    parser.add_argument("--min-day", default="20260529")
    parser.add_argument("--max-day", default="20260612")
    args = parser.parse_args()

    _bootstrap()
    from research.capital_simulation_reconciliation import CapitalSimulationReconciliation
    from research.equity_curve_shadow import PERIOD_START

    phase381_trades: list[dict] = []
    if args.load_phase381:
        t_load = time.monotonic()
        phase381_trades = _load_phase381_trades(min_day=args.min_day, max_day=args.max_day)
        print(f"phase381_trades_loaded={len(phase381_trades)} sec={round(time.monotonic()-t_load,1)}", flush=True)

    t0 = time.monotonic()
    job = CapitalSimulationReconciliation(
        repo_root=REPO,
        reports_dir=args.reports_dir,
        period_start=args.period_start or PERIOD_START,
        phase381_trades=phase381_trades,
    )
    result = job.run()
    paths = job.write_outputs(result)

    avr = result.get("accepted_vs_rejected") or {}
    acc = avr.get("accepted") or {}
    rej = avr.get("rejected") or {}
    decomp = result.get("pnl_delta_decomposition") or {}

    print(f"phase268 wall_runtime_sec={round(time.monotonic()-t0,1)}", flush=True)
    print("\n=== Phase268 Reconciliation ===", flush=True)
    print(
        f"accepted: n={acc.get('trade_count')} pnl={acc.get('total_pnl_yen')} PF={acc.get('profit_factor')}",
        flush=True,
    )
    print(
        f"rejected: n={rej.get('trade_count')} counterfactual_pnl={rej.get('total_pnl_yen')} PF={rej.get('profit_factor')}",
        flush=True,
    )
    print(f"gap vs phase388: {decomp.get('total_gap_yen')} yen", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
