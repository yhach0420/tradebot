#!/usr/bin/env python3
"""Run Phase613 runtime latency freshness timeout audit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase613_runtime_latency_freshness_timeout_audit import run_phase613


def main() -> int:
    report = run_phase613(repo_root=ROOT)
    print(report["verdict"])
    for k in sorted(report.get("mandatory_answers", {})):
        print(f"{k}: {report['mandatory_answers'][k]}")
    print("output:", ROOT / "results" / "reports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
