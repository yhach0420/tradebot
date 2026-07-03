#!/usr/bin/env python3
"""Run Phase616 CoreRuntimeMode audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase616_core_runtime_mode_audit import run_phase616


def main() -> int:
    report = run_phase616(repo_root=ROOT)
    print(report["verdict"])
    print(json.dumps(report.get("mode_matrix", {}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
