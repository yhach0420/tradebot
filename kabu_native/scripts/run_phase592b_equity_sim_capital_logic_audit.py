#!/usr/bin/env python3
"""Phase592B — Equity simulation capital logic audit (research only)."""

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
    cap = 5
    if "--cap" in sys.argv[1:]:
        i = sys.argv.index("--cap")
        cap = int(sys.argv[i + 1])

    from research.phase592b_equity_sim_capital_logic_audit import Phase592BJob

    job = Phase592BJob(repo_root=REPO, cap=cap)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True), flush=True)
    for label, path in paths.items():
        print(f"{label}={path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
