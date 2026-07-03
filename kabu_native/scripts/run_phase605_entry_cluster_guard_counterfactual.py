#!/usr/bin/env python3
"""Run Phase605 entry_cluster_guard PBv2 block counterfactual."""

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

from research.phase605_entry_cluster_guard_counterfactual import run_phase605


def main() -> int:
    report = run_phase605(repo_root=ROOT)
    print(report["verdict"])
    ans = report["mandatory_answers"]
    for key in sorted(ans):
        if key.startswith(("1_", "2_", "3_", "4_", "5_")):
            print(f"{key}: {ans[key]}")
    print("output:", report["output_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
