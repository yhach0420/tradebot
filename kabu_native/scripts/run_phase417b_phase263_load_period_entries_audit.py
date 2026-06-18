#!/usr/bin/env python3
"""Phase417B Phase263 load_period_entries audit and recompute."""

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
    from research.phase417b_phase263_load_period_entries_audit import Phase417BJob
    from research.structural_trade_normalize import resolve_reports_dir

    reports = resolve_reports_dir(REPO)
    job = Phase417BJob(repo_root=REPO, reports_dir=reports)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"base_entry_count={result.get('recomputed', {}).get('base_entry_count')}", flush=True)
    print(json.dumps(result.get("recomputed", {}).get("verdict", {}), indent=2, ensure_ascii=False))
    print(f"audit={paths.get('audit')}", flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
