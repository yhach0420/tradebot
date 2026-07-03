#!/usr/bin/env python3
"""Run Phase615 Core / Extension runtime separation design audit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase615_core_extension_runtime_separation import run_phase615


def main() -> int:
    report = run_phase615(repo_root=ROOT)
    print(report["verdict"])
    for k, v in report.get("mandatory_answers", {}).items():
        if k.endswith("_rationale") or k.endswith("_intrusion") or k.startswith("4_") or k.startswith("5_") or k.startswith("6_"):
            continue
        print(f"{k}: {v}")
    print("output:", ROOT / "results" / "reports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
