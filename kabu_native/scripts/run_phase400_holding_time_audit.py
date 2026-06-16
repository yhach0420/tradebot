#!/usr/bin/env python3
"""Phase400: Holding time audit on Phase399 Position-CAP backfill."""

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
    parser = argparse.ArgumentParser(description="Phase400 holding time audit")
    parser.add_argument("--start-day", type=str, default="20260529")
    parser.add_argument("--end-day", type=str, default="20260615")
    parser.add_argument("--trades-csv", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    _bootstrap()
    from research.phase400_holding_time_audit import run_holding_time_audit

    result = run_holding_time_audit(
        repo_root=REPO.resolve(),
        trades_path=args.trades_csv.resolve() if args.trades_csv else None,
        output_dir=args.output_dir.resolve(),
        period_start=args.start_day,
        period_end=args.end_day,
    )
    ans = result["summary"].get("mandatory_answers") or {}
    print(f"avg_hold_sec={ans.get('1_position_cap_avg_hold_sec')}", flush=True)
    print(f"median_hold_sec={ans.get('2_median_hold_sec')}", flush=True)
    print(f"report={result.get('report_path')}", flush=True)
    print(json.dumps(ans, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
