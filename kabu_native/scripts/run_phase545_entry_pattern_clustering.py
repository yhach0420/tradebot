#!/usr/bin/env python3
"""Phase545 — ENTRY pattern clustering runner."""

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
    parser = argparse.ArgumentParser(description="Phase545 entry pattern clustering")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--period-end", default="20260625")
    args = parser.parse_args()

    from research.phase545_entry_pattern_clustering import Phase545Job

    job = Phase545Job(
        repo_root=REPO,
        dataset_path=Path(args.dataset) if args.dataset else None,
        period_end=args.period_end,
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
