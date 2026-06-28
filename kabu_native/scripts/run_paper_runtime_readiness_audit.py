#!/usr/bin/env python3
"""Paper Runtime Readiness Audit — Phase591/592/593 hook safety before Tuesday paper trade."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"


def _bootstrap() -> None:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    max_rows = 8000
    if "--max-push-rows" in sys.argv[1:]:
        i = sys.argv.index("--max-push-rows")
        max_rows = int(sys.argv[i + 1])

    from research.paper_runtime_readiness_audit import PaperRuntimeReadinessJob

    job = PaperRuntimeReadinessJob(repo_root=REPO, max_push_rows=max_rows)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(f"ready={result.get('ready')}", flush=True)
    ma = result.get("mandatory_answers") or {}
    print(f"run_paper_trade_bat_safe={ma.get('run_paper_trade_bat_safe_for_tuesday')}", flush=True)
    for label, path in paths.items():
        print(f"{label}={path}", flush=True)
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
