#!/usr/bin/env python3
"""Phase558 — Current Runtime replay after Phase557 stop_low_mfe guard."""

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
    from research.phase558_current_runtime_after_phase557 import Phase558Job

    job = Phase558Job(repo_root=KABU)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=False), flush=True)
    print(f"comparison={paths.get('comparison')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
