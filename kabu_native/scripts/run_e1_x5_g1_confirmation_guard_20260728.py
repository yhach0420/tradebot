#!/usr/bin/env python3
"""E1_X5_G1 mirror — copies published canonical triad only. No recompute / no hash mutation."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UNIFY_OUT = REPO / "results" / "research" / "e1_x5_canonical_path_unify_20260728"
OUT = REPO / "results" / "research" / "e1_x5_g1_confirmation_guard_20260728"
ARTIFACTS = ("report.json", "report.md", "audit.xlsx")


def main() -> int:
    missing = [n for n in ARTIFACTS if not (UNIFY_OUT / n).is_file()]
    if missing:
        print(f"missing canonical artifacts {missing} — run run_e1_x5_sot_clean_publish_20260728.py first", flush=True)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    for n in ARTIFACTS:
        shutil.copy2(UNIFY_OUT / n, OUT / n)
        print(f"mirrored {n}", flush=True)
    # pointer only
    cur = UNIFY_OUT / "CURRENT_RUN_ID.txt"
    if cur.is_file():
        shutil.copy2(cur, OUT / "CURRENT_RUN_ID.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
