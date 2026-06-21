#!/usr/bin/env python3
"""Phase480 PBv2 loss cluster audit + trend shadow runner."""

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
    parser = argparse.ArgumentParser(description="Phase480 PBv2 loss cluster audit + trend shadow")
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    del args

    from research.phase480_pbv2_loss_cluster_audit import Phase480Job

    job = Phase480Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=False, default=str), flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
