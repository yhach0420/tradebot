#!/usr/bin/env python3
"""Phase575 — Vol/Liq startup cache production adoption validation."""

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
    parser_args = sys.argv[1:]
    workers = 4
    if "--workers" in parser_args:
        i = parser_args.index("--workers")
        workers = int(parser_args[i + 1])

    from research.phase575_vol_liq_startup_cache_production_adoption import Phase575Job

    job = Phase575Job(repo_root=REPO, workers=workers)
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
