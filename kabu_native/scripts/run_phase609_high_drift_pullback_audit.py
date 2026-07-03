#!/usr/bin/env python3
"""Run Phase609 high_drift_pullback root cause audit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase609_high_drift_pullback_audit import run_phase609


def main() -> int:
    report = run_phase609(repo_root=ROOT)
    print(report["verdict"])
    for k in sorted(report.get("mandatory_answers", {})):
        print(f"{k}: {report['mandatory_answers'][k]}")
    print("output:", report["output_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
