#!/usr/bin/env python3
"""Run Phase606 restore pre-6/25 PBv2 full code diff audit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PARENT = ROOT.parent
for p in (SRC, PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase606_restore_pre625_pbv2_audit import run_phase606


def main() -> int:
    report = run_phase606(repo_root=ROOT)
    print(report["verdict"])
    for k in sorted(report.get("mandatory_answers", {})):
        print(f"{k}: {report['mandatory_answers'][k]}")
    print("output:", report["output_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
