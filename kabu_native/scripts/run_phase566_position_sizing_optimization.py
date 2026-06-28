#!/usr/bin/env python3
"""Phase566 — Position sizing optimization study."""

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
    from research.phase566_position_sizing_optimization import Phase566Job

    job = Phase566Job(repo_root=KABU)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    for label, path in paths.items():
        print(f"{label}={path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
