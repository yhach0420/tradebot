#!/usr/bin/env python3
"""Phase621: freshness semantics v2 temporary production implementation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> int:
    from research.phase621_freshness_semantics_v2 import run_phase621

    report = run_phase621(repo_root=ROOT)
    print(report["verdict"])
    v = report.get("verification", {})
    print("unit:", v.get("unit_tests_ok"))
    print("preflight:", v.get("preflight_ok"))
    print("rollback:", v.get("rollback_yaml_false_restores_v1"))
    print("rescued:", v.get("parity_rescued_from_data_stale_price"))
    print("report:", report.get("output_path"))
    return 0 if report["verdict"].endswith("_done") else 1


if __name__ == "__main__":
    raise SystemExit(main())
