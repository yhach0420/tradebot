#!/usr/bin/env python3
"""Phase377: Daily regime breakdown from Phase376 outputs."""

from __future__ import annotations

import argparse
import json
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
    parser = argparse.ArgumentParser(description="Phase377 daily regime breakdown")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()

    _bootstrap()
    from research.phase377_daily_regime_breakdown import (
        PERIOD_A_ID,
        PERIOD_B_ID,
        PRIMARY_STACK,
        Phase377DailyRegimeBreakdown,
    )

    t0 = time.monotonic()
    max_workers = max(1, args.max_workers) if args.parallel else 1
    audit = Phase377DailyRegimeBreakdown(reports_dir=args.reports_dir)
    result = audit.run(max_workers=max_workers)
    paths = audit.write_outputs(result)

    summary = {k: v for k, v in result.items() if not k.startswith("_")}
    c_a = (summary.get("period_metrics") or {}).get(PERIOD_A_ID, {}).get(PRIMARY_STACK, {})
    c_b = (summary.get("period_metrics") or {}).get(PERIOD_B_ID, {}).get(PRIMARY_STACK, {})
    loss = summary.get("loss_concentration") or {}
    verdict = summary.get("final_verdict") or {}

    print(
        f"phase377 parallel={args.parallel} max_workers={max_workers} "
        f"wall_runtime_sec={round(time.monotonic() - t0, 1)}",
        flush=True,
    )
    print("\n=== Phase377 Summary (Stack C) ===", flush=True)
    print(f"Period A pnl: {c_a.get('total_pnl_yen_100')} pf: {c_a.get('profit_factor')}", flush=True)
    print(f"Period B pnl: {c_b.get('total_pnl_yen_100')} pf: {c_b.get('profit_factor')}", flush=True)
    print(f"loss_concentration_period: {verdict.get('loss_concentration_period')}", flush=True)
    print(f"q1_max_dd_in_period_a: {loss.get('q1_max_dd_majority_in_period_a')}", flush=True)
    print(f"q2_period_b_positive: {loss.get('q2_period_b_total_pnl_positive')}", flush=True)
    print(f"q3_period_b_pf_above_1: {loss.get('q3_period_b_pf_above_1')}", flush=True)
    for label, path in paths.items():
        print(f"{label}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
