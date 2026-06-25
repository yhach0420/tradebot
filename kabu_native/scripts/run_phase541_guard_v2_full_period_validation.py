#!/usr/bin/env python3
"""Phase541 — Guard v2 full-period validation runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase541 Guard v2 full-period validation")
    parser.add_argument("--period-start", default="20260616")
    parser.add_argument("--period-end", default=None)
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    parallel = args.parallel and not args.no_parallel

    from research.phase541_guard_v2_full_period_validation import Phase541Job

    job = Phase541Job(
        repo_root=REPO,
        period_start=args.period_start,
        period_end=args.period_end,
        parallel=parallel,
        max_workers=args.max_workers,
    )
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(
        json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True, default=str),
        flush=True,
    )
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
