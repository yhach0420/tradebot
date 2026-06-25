#!/usr/bin/env python3
"""Phase520 — G3_G4 forward shadow runner."""

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
    parser = argparse.ArgumentParser(description="Phase520 G3_G4 forward shadow")
    parser.add_argument("--parallel", action="store_true", default=True)
    parser.add_argument("--no-parallel", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    parallel = args.parallel and not args.no_parallel

    from research.phase520_g3_g4_forward_shadow import Phase520Job

    job = Phase520Job(repo_root=REPO, parallel=parallel, max_workers=args.max_workers)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True, default=str), flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
