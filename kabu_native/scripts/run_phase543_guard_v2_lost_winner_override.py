#!/usr/bin/env python3
"""Phase543A — Guard v2 lost winner / override design runner."""

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
    parser = argparse.ArgumentParser(description="Phase543A lost winner override design")
    parser.add_argument("--period-start", default="20260616")
    parser.add_argument("--period-end", default=None)
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    from research.phase543_guard_v2_lost_winner_override import Phase543Job

    job = Phase543Job(
        repo_root=REPO,
        period_start=args.period_start,
        period_end=args.period_end,
        parallel=args.parallel and not args.no_parallel,
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
