#!/usr/bin/env python3
"""Phase418 Phase273/274 full revalidation on no_overlap_replace Baseline B."""

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
    from research.phase418_live_config_revalidation import Phase418Job
    from research.structural_trade_normalize import resolve_reports_dir

    reports = resolve_reports_dir(REPO)
    job = Phase418Job(repo_root=REPO, reports_dir=reports)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"status={result.get('status')}", flush=True)
    print(f"trade_count={result.get('input_validation', {}).get('trade_count')}", flush=True)
    print(f"p273_recommendation={result.get('phase273', {}).get('recommended_candidate_key')}", flush=True)
    print(
        f"p274_adoption={(result.get('phase274', {}).get('adoption_verdict') or {}).get('adoption_verdict')}",
        flush=True,
    )
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=False))
    print(f"summary={paths.get('summary')}", flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
