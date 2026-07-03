#!/usr/bin/env python3
"""Phase623B: Phase621 PBv2 revival attribution."""

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
    from research.phase623b_phase621_pbv2_revival_attribution import run_phase623b

    result = run_phase623b(REPO)
    print(f"verdict={result.get('verdict')}")
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2))
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
