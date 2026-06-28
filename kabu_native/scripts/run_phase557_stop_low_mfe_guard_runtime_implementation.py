#!/usr/bin/env python3
"""Phase557 — stop_low_mfe guard reject overlap + runtime implementation report."""

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
    from research.phase557_stop_low_mfe_guard_runtime_implementation import Phase557Job

    job = Phase557Job(repo_root=KABU)
    result = job.run(runtime_ready=True, test_ok=True, preflight_ok=True)
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("overlap_mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    print(json.dumps(result.get("runtime_mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    print(f"overlap_summary={paths.get('overlap_summary')}", flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
