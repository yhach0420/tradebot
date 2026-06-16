#!/usr/bin/env python3
"""Phase405: Time-based MFE / STOP boundary inference."""

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
    parser = argparse.ArgumentParser(description="Phase405 time boundary inference")
    parser.add_argument("--start-day", type=str, default="20260529")
    parser.add_argument("--end-day", type=str, default="20260615")
    parser.add_argument("--trades-csv", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    _bootstrap()
    from research.phase405_time_boundary_inference import run_phase405_inference

    result = run_phase405_inference(
        repo_root=REPO.resolve(),
        trades_path=args.trades_csv.resolve() if args.trades_csv else None,
        output_dir=args.output_dir.resolve(),
        period_start=args.start_day,
        period_end=args.end_day,
    )
    summary = result["summary"]
    print(f"trade_count={summary.get('trade_count')}", flush=True)
    headline = summary.get("headline") or ""
    print(headline.encode("ascii", errors="replace").decode("ascii"), flush=True)
    print(json.dumps(summary.get("mandatory_answers") or {}, indent=2, ensure_ascii=False))
    print(f"report={result.get('report_path')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
