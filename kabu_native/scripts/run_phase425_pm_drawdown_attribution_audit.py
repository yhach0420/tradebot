#!/usr/bin/env python3
"""Phase425 — 20260617 PM drawdown attribution audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from research.phase425_pm_drawdown_attribution_audit import Phase425Job

    job = Phase425Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)
    summary = result.get("summary") or {}
    print(f"verdict={summary.get('verdict')}", flush=True)
    print(json.dumps(summary.get("mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
