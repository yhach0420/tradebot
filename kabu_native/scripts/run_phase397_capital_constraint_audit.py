#!/usr/bin/env python3
"""Phase397: Capital constraint runtime audit (investigation only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent


def _bootstrap() -> None:
    for p in (REPO / "src", PARENT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from research.phase397_capital_constraint_runtime_audit import run_phase397

    summary = run_phase397(REPO.resolve())
    print(f"verdict={summary.get('verdict')}: {summary.get('verdict_detail')}")
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0 if summary.get("verdict") in ("PASS", "WARN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
