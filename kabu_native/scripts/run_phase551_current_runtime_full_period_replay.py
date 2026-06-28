#!/usr/bin/env python3
"""Phase551 — current runtime full-period replay & equity simulation."""

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
    parser = argparse.ArgumentParser(description="Phase551 runtime full-period replay")
    parser.add_argument("--period-start", default="20260616")
    parser.add_argument("--period-end", default="20260625")
    parser.add_argument("--extended-start", default="20260529")
    args = parser.parse_args()

    from research.phase551_current_runtime_full_period_replay import Phase551Job

    job = Phase551Job(
        repo_root=KABU,
        period_start=args.period_start,
        period_end=args.period_end,
        extended_start=args.extended_start,
    )
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=False, default=str), flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
