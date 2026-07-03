#!/usr/bin/env python3
"""Phase602: PUSH raw timestamp trace audit."""

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
    from research.phase602_push_raw_timestamp_trace_audit import run_phase602

    result = run_phase602(REPO)
    print(f"verdict={result.get('verdict')}", flush=True)
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}", flush=True)
    print(json.dumps(result.get("mandatory_answers") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
