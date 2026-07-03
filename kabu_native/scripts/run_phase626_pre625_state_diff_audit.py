#!/usr/bin/env python3
"""Phase626: Pre625 vs HEAD runtime state differential audit."""

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
    parser = argparse.ArgumentParser(description="Phase626 pre625 vs HEAD state diff")
    parser.add_argument("--force-replay", action="store_true", help="Run missing replays even when disk usage exceeds 76 percent")
    args = parser.parse_args()
    from research.phase626_pre625_state_diff_audit import run_phase626

    result = run_phase626(REPO, force_replay=args.force_replay)
    print(f"verdict={result.get('verdict')}")
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2))
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
