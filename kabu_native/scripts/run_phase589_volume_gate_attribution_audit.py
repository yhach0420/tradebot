#!/usr/bin/env python3
"""Phase589 — Volume gate attribution audit."""

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
    workers = 4
    if "--workers" in sys.argv[1:]:
        i = sys.argv.index("--workers")
        workers = int(sys.argv[i + 1])

    from research.phase589_volume_gate_attribution_audit import Phase589Job

    job = Phase589Job(repo_root=REPO, workers=workers)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(f"all_pass={result.get('all_pass')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True), flush=True)
    for label, path in paths.items():
        print(f"{label}={path}", flush=True)
    return 0 if result.get("all_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
