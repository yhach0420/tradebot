#!/usr/bin/env python3
"""Phase625: live_feature_bridge differential audit."""

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
    from research.phase625_feature_bridge_audit import run_phase625

    result = run_phase625(REPO)
    print(f"verdict={result.get('verdict')}")
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2))
    for k, v in (result.get("output_paths") or {}).items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
