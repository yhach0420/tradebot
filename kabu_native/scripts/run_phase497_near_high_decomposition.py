#!/usr/bin/env python3
"""Phase497 near day high decomposition runner."""

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
    parser = argparse.ArgumentParser(description="Phase497 near day high decomposition")
    args = parser.parse_args()
    _ = args

    mod_path = KABU / "src" / "research" / "phase497_near_high_decomposition.py"
    spec = importlib.util.spec_from_file_location("phase497_decomp", mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    job = mod.Phase497Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True, default=str), flush=True)
    print(f"discovery={paths.get('discovery')}", flush=True)
    print(f"ranking={paths.get('ranking')}", flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
