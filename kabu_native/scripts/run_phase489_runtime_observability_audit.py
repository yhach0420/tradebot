#!/usr/bin/env python3
"""Phase489 runtime observability audit runner."""

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
    # Import module file directly — research/__init__.py pulls logic_lab (slow).
    import importlib.util

    mod_path = KABU / "src" / "research" / "phase489_runtime_observability_audit.py"
    spec = importlib.util.spec_from_file_location("phase489_runtime_observability_audit", mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    Phase489Job = mod.Phase489Job

    job = Phase489Job(repo_root=REPO)
    result = job.run()
    paths = job.write_outputs(result)
    print(f"verdict={result.get('verdict')}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, indent=2, ensure_ascii=True, default=str), flush=True)
    print(f"summary={paths.get('summary')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
