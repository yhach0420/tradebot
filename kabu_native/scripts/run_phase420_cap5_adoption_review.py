#!/usr/bin/env python3
"""Phase420 CAP5 adoption review (Part A only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent


def _bootstrap() -> None:
    for p in (REPO / "src", PARENT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from research.phase420_cap5_adoption_review import Phase420Job
    from research.structural_trade_normalize import resolve_reports_dir

    reports = resolve_reports_dir(REPO)
    job = Phase420Job(repo_root=REPO, reports_dir=reports)
    result = job.run()
    paths = job.write_outputs(result)
    summary = result.get("summary") or {}
    cond = summary.get("adoption_conditions") or {}
    delta = summary.get("cap3_vs_cap5") or {}
    print(f"adoption_ready={cond.get('adoption_ready')}", flush=True)
    print(json.dumps(delta, indent=2, ensure_ascii=False), flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    print(f"daily={paths.get('daily')}", flush=True)
    print(f"runtime_readiness={paths.get('runtime_readiness')}", flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

