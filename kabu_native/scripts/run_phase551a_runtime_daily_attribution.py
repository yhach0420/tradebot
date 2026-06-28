#!/usr/bin/env python3
"""Phase551A — runtime daily attribution from live-window Current Runtime."""

from __future__ import annotations

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
    from research.phase551a_runtime_daily_attribution import Phase551AJob

    job = Phase551AJob(repo_root=KABU)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(f"total_pnl={result.get('total_pnl_yen_100')}", flush=True)
    print(json.dumps(result.get("loss_cause_ranking") or [], indent=2, ensure_ascii=False), flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
