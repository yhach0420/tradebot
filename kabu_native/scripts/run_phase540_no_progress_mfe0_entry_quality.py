#!/usr/bin/env python3
"""Phase540 — NoProgress / MFE0 entry quality root cause study runner."""

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
    parser = argparse.ArgumentParser(description="Phase540 NoProgress / MFE0 entry quality study")
    parser.add_argument("--day", default="20260625", help="Target day YYYYMMDD (repeatable)")
    parser.add_argument("--session", default=None, help="Specific live_session_* folder name")
    parser.add_argument("--all-sessions", action="store_true", help="Merge all sessions for each day")
    parser.add_argument("--days", nargs="*", help="Optional multiple days")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    days = args.days if args.days else [args.day]
    parallel = args.parallel and not args.no_parallel

    from research.phase540_no_progress_mfe0_entry_quality import Phase540Job

    job = Phase540Job(
        repo_root=REPO,
        days=days,
        session=args.session,
        all_sessions=args.all_sessions,
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
