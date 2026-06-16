#!/usr/bin/env python3
"""Phase399: Historical Position-CAP backfill with session-parallel execution."""

from __future__ import annotations

import argparse
import json
import sys
import time
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
    parser = argparse.ArgumentParser(description="Phase399 historical position-CAP backfill")
    parser.add_argument("--start-day", type=str, default="20260529")
    parser.add_argument("--end-day", type=str, default="20260615")
    parser.add_argument("--parallel", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument(
        "--force-structural-backfill",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    _bootstrap()
    from research.phase399_historical_position_cap_backfill import run_phase399_backfill

    t0 = time.monotonic()
    result = run_phase399_backfill(
        repo_root=REPO.resolve(),
        start_day=args.start_day,
        end_day=args.end_day,
        output_dir=args.output_dir.resolve(),
        parallel=args.parallel,
        max_workers=max(1, args.max_workers),
        force_structural_backfill=args.force_structural_backfill,
    )
    elapsed = round(time.monotonic() - t0, 2)
    summary = result["summary"]
    summary["elapsed_sec"] = elapsed

    print(f"discovered_sessions={summary.get('discovered_sessions')}", flush=True)
    print(f"processed_sessions={summary.get('processed_sessions')}", flush=True)
    print(f"skipped_push_replay={summary.get('skipped_push_replay')}", flush=True)
    print(f"skipped_debug={summary.get('skipped_debug')}", flush=True)
    print(f"structural_backfilled={summary.get('structural_backfilled')}", flush=True)
    print(f"failed_sessions={summary.get('failed_session_count')}", flush=True)
    print(f"elapsed_sec={elapsed}", flush=True)
    print(f"max_workers={summary.get('max_workers')}", flush=True)
    print(f"verdict={summary.get('verdict')}", flush=True)
    print(f"report={result.get('report_path')}", flush=True)

    out_json = args.output_dir / "phase399_historical_position_cap_backfill_summary.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if summary.get("verdict") == "historical_backfill_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
