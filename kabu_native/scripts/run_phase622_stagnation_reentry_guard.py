#!/usr/bin/env python3
"""Phase622: Stagnation Exit Re-entry Guard + Liquidity Stale Attribution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> int:
    from research.phase622_stagnation_reentry_guard import run_phase622

    report = run_phase622(repo_root=ROOT)
    print(report["verdict"])
    print(json.dumps(report.get("mandatory_answers", {}), ensure_ascii=False, indent=2))
    print("report:", report.get("output_paths", {}).get("report"))
    return 0 if report["verdict"].endswith("_done") else 1


if __name__ == "__main__":
    raise SystemExit(main())
