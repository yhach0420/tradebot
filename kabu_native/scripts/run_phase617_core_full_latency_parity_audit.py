#!/usr/bin/env python3
"""Phase617: CORE_ONLY vs FULL_EXTENSION latency parity audit (4 parallel, disk-safe)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _free_gb() -> float:
    return shutil.disk_usage("C:").free / (1024**3)


def main() -> int:
    free = _free_gb()
    print(f"disk_free_gb: {free:.2f}")
    if free < 20:
        print("ABORT: disk free < 20GB")
        return 1

    from research.phase617_core_full_latency_parity_audit import run_phase617

    report = run_phase617(repo_root=ROOT)
    print(report["verdict"])
    for k, v in sorted(report.get("mandatory_answers", {}).items()):
        print(f"{k}: {v}")
    print("output:", ROOT / "results" / "reports" / "phase617_parallel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
