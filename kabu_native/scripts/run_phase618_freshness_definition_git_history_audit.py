#!/usr/bin/env python3
"""Phase618: Freshness definition git history audit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> int:
    from research.phase618_freshness_definition_git_history_audit import run_phase618

    report = run_phase618(repo_root=ROOT)
    print(report["verdict"])
    print("report:", ROOT / "results" / "reports" / "phase618_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
