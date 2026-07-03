#!/usr/bin/env python3
"""Run Phase612A HEAD vs pre625 runtime structure comparison."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase612a_pre625_runtime_structure_mode_audit import run_phase612a


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head-session", type=Path, default=None)
    parser.add_argument("--pre625-session", type=Path, default=None)
    args = parser.parse_args()
    report = run_phase612a(
        repo_root=ROOT,
        head_session=args.head_session,
        pre625_session=args.pre625_session,
    )
    print(report["verdict"])
    print("head_pre625_mode:", report.get("head_pre625_mode"))
    print("pre625_pre625_mode:", report.get("pre625_pre625_mode"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
