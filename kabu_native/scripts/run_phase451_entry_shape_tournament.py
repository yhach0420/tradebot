#!/usr/bin/env python3
"""Phase451 entry shape filter tournament runner."""

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
    from research.phase451_entry_shape_tournament import Phase451Job

    job = Phase451Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)
    summary = result.get("summary") or {}
    print(f"verdict={summary.get('verdict')}", flush=True)
    print(f"best={summary.get('best_variant')}", flush=True)
    print(json.dumps(summary.get("mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
