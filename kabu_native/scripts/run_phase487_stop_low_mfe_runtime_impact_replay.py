#!/usr/bin/env python3
"""Phase487 stop_low_mfe runtime impact replay runner."""

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
    from research.phase487_stop_low_mfe_runtime_impact_replay import Phase487Job

    job = Phase487Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"phase487 guards {result.get('mandatory_answers', {}).get('guard_count')}", flush=True)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True, default=str), flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
