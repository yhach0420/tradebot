#!/usr/bin/env python3
"""Phase657 — Shadow portfolio final review."""

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
    from research.phase657_shadow_portfolio_review import Phase657Job

    job = Phase657Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
