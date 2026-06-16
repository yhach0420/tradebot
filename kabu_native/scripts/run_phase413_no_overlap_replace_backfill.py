#!/usr/bin/env python3
"""Phase413 no-overlap-replace backfill adoption review."""

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
    from research.phase413_no_overlap_replace_backfill import Phase413BackfillJob
    from research.structural_trade_normalize import resolve_reports_dir

    reports = resolve_reports_dir(REPO)
    job = Phase413BackfillJob(repo_root=REPO, reports_dir=reports)
    result = job.run()
    print(f"verdict={result.get('summary', {}).get('verdict')}", flush=True)
    print(json.dumps(result.get('summary', {}), indent=2, ensure_ascii=False, default=str))
    print("report=docs/operations/phase413_no_overlap_replace_adoption_review.md", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

