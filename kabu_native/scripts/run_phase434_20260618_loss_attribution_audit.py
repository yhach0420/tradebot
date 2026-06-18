#!/usr/bin/env python3
"""Phase434 — 20260618 loss attribution audit."""

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
    from research.phase434_20260618_loss_attribution_audit import Phase434Job

    job = Phase434Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)
    summary = result.get("mandatory_answers") or {}
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    print(f"report={paths.get('report')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
