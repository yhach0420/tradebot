#!/usr/bin/env python3
"""Phase496 MST near-high threshold optimization runner."""

from __future__ import annotations

import argparse
import importlib.util
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
    parser = argparse.ArgumentParser(description="Phase496 MST near-high threshold optimization")
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()

    mod_path = KABU / "src" / "research" / "phase496_mst_near_high_optimization.py"
    spec = importlib.util.spec_from_file_location("phase496_mst", mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    job = mod.Phase496Job(repo_root=REPO, parallel=args.parallel, max_workers=args.max_workers)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True, default=str), flush=True)
    print(f"grid={paths.get('grid')}", flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
