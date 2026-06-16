#!/usr/bin/env python3
"""Phase402: Time-decayed MFE / stop shadow exit replay."""

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
    parser = argparse.ArgumentParser(description="Phase402 time-decay exit shadow")
    parser.add_argument("--start-day", type=str, default="20260529")
    parser.add_argument("--end-day", type=str, default="20260615")
    parser.add_argument("--trades-csv", type=Path, default=None)
    parser.add_argument("--phase400-summary", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    _bootstrap()
    from research.phase402_time_decay_exit_shadow import run_phase402_shadow

    result = run_phase402_shadow(
        repo_root=REPO.resolve(),
        trades_path=args.trades_csv.resolve() if args.trades_csv else None,
        phase400_summary_path=args.phase400_summary.resolve() if args.phase400_summary else None,
        output_dir=args.output_dir.resolve(),
        period_start=args.start_day,
        period_end=args.end_day,
    )
    summary = result["summary"]
    print(f"trade_count={summary.get('position_cap_accepted_trade_count')}", flush=True)
    print(f"verdict={summary.get('verdict')}", flush=True)
    headline = summary.get("headline") or ""
    print(headline.encode("ascii", errors="replace").decode("ascii"), flush=True)
    print(f"adopt_candidate_count={summary.get('adopt_candidate_count')}", flush=True)
    if summary.get("best_adopt_policy"):
        print(json.dumps(summary["best_adopt_policy"], indent=2, ensure_ascii=False))
    print(f"report={result.get('report_path')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
