#!/usr/bin/env python3
"""Phase537 — Open strength rank feature repair + Phase536 re-run."""

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
    parser = argparse.ArgumentParser(description="Phase537 open strength rank repair")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    parallel = args.parallel and not args.no_parallel

    from research.phase537_open_strength_rank_feature_repair import Phase537Job

    job = Phase537Job(repo_root=REPO, parallel=parallel, max_workers=args.max_workers)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("repair_validation") or {}, indent=2, ensure_ascii=True, default=str), flush=True)
    print(f"repair_report={paths.get('repair_report')}", flush=True)
    print(f"debug={paths.get('debug')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
