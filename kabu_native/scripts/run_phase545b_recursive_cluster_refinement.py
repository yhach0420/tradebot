#!/usr/bin/env python3
"""Phase545B — Cluster3 recursive refinement runner."""

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
    parser = argparse.ArgumentParser(description="Phase545B cluster3 refinement")
    parser.add_argument("--cluster-dataset", default=None)
    parser.add_argument("--phase544-dataset", default=None)
    args = parser.parse_args()

    from research.phase545b_recursive_cluster_refinement import Phase545BJob

    job = Phase545BJob(
        repo_root=REPO,
        cluster_dataset=Path(args.cluster_dataset) if args.cluster_dataset else None,
        phase544_dataset=Path(args.phase544_dataset) if args.phase544_dataset else None,
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
