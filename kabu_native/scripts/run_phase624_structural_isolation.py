#!/usr/bin/env python3
"""Phase624: CORE_ONLY vs FULL_EXTENSION structural isolation on identical PUSH input."""

from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description="Phase624 structural isolation experiment")
    parser.add_argument("--day", default="2026-06-25", help="Push JSONL day (YYYY-MM-DD)")
    parser.add_argument(
        "--skip-replay",
        action="store_true",
        help="Reuse existing _phase624 replay outputs if present",
    )
    args = parser.parse_args()

    from research.phase624_structural_isolation import run_phase624

    result = run_phase624(REPO, day=args.day, skip_replay=args.skip_replay)
    print(f"verdict={result.get('verdict')}")
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2))
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
