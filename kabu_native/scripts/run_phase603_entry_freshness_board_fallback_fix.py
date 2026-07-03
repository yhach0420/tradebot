#!/usr/bin/env python3
"""Phase603: Entry freshness board fallback fix validation."""

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
    from research.phase603_entry_freshness_board_fallback_fix import run_phase603

    result = run_phase603(REPO)
    print(f"verdict={result.get('verdict')}", flush=True)
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}", flush=True)
    print(json.dumps(result.get("mandatory") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
