#!/usr/bin/env python3
"""Phase616B: ExtensionBus session_end TypeError fix verification."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> int:
    from research.phase616b_extension_bus_session_end_fix import run_phase616b

    report = run_phase616b(repo_root=ROOT)
    print(report["verdict"])
    v = report.get("verification", {})
    print("unit:", v.get("unit_tests_ok"))
    print("smoke:", v.get("production_startup_smoke_ok"))
    print("preflight:", v.get("preflight_ok"))
    print("report:", report.get("output_path"))
    return 0 if report["verdict"].endswith("_done") else 1


if __name__ == "__main__":
    raise SystemExit(main())
