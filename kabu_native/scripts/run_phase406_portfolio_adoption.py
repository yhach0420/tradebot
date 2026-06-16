#!/usr/bin/env python3
"""Phase406: Portfolio-level adoption re-evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
DEFAULT_OUTPUT = REPO / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "src", PARENT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase406 portfolio adoption")
    parser.add_argument("--start-day", type=str, default="20260529")
    parser.add_argument("--end-day", type=str, default="20260615")
    parser.add_argument("--trades-csv", type=Path, default=None)
    parser.add_argument("--phase405-policy", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    _bootstrap()
    from research.phase406_portfolio_adoption import run_phase406_portfolio_adoption

    result = run_phase406_portfolio_adoption(
        repo_root=REPO.resolve(),
        trades_path=args.trades_csv.resolve() if args.trades_csv else None,
        phase405_policy_path=args.phase405_policy.resolve() if args.phase405_policy else None,
        output_dir=args.output_dir.resolve(),
        period_start=args.start_day,
        period_end=args.end_day,
    )
    summary = result["summary"]
    print(f"trade_count={summary.get('trade_count')}", flush=True)
    print(f"verdict={summary.get('verdict')}", flush=True)
    headline = summary.get("headline") or ""
    print(headline.encode("ascii", errors="replace").decode("ascii"), flush=True)
    print(json.dumps(summary.get("mandatory_ranks") or {}, indent=2, ensure_ascii=False))
    print(f"recommendation={summary.get('top_recommendation')}", flush=True)
    print(f"report={result.get('report_path')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
