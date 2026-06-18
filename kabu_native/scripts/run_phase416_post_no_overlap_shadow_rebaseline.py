#!/usr/bin/env python3
"""Phase416 post-no-overlap historical shadow rebaseline."""

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
    from research.phase416_post_no_overlap_shadow_rebaseline import Phase416Job
    from research.structural_trade_normalize import resolve_reports_dir

    reports = resolve_reports_dir(REPO)
    job = Phase416Job(repo_root=REPO, reports_dir=reports)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"status={result.get('status')}", flush=True)
    print(json.dumps(result.get("baselines", {}), indent=2, ensure_ascii=False, default=str))
    print(f"summary={paths.get('summary')}", flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

