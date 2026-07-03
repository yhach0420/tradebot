#!/usr/bin/env python3
"""Run Phase604B PBv2=0 implementation block audit."""

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

from research.phase604b_pbv2_zero_impl_block_audit import run_phase604b


def main() -> int:
    report = run_phase604b(repo_root=ROOT)
    print(report["verdict"])
    ans = report["mandatory_answers"]
    print("630 pbv2 eval calls:", ans.get("1_pbv2_evaluate_entry_calls_630"))
    print("630 pbv2 accept branch replay:", ans.get("3b_pbv2_accept_branch_replay_630"))
    print("630 true blockers:", ans.get("6_true_first_blocker_630_replay"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
