#!/usr/bin/env python3
"""P0 disk cleanup + P1-P4 Phase613 disk-safe parallel latency audit."""

from __future__ import annotations

import shutil
import subprocess
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
    free_before = _free_gb()
    print(f"disk_free_before_gb: {free_before:.2f}")

    cleanup = ROOT / "scripts" / "disk_cleanup_research_artifacts.py"
    if cleanup.is_file():
        subprocess.run([sys.executable, str(cleanup)], check=False, cwd=str(ROOT))

    free_after = _free_gb()
    freed = free_after - free_before
    print(f"disk_free_after_gb: {free_after:.2f}")
    print(f"disk_freed_gb: {freed:.2f}")

    from research.phase613_disk_safe_parallel_latency_audit import run_disk_safe_pipeline

    report = run_disk_safe_pipeline(repo_root=ROOT, disk_freed_gb=round(freed, 3))
    print(report["verdict"])
    for k in sorted(report.get("mandatory_answers", {})):
        print(f"{k}: {report['mandatory_answers'][k]}")
    print("output:", ROOT / "results" / "reports" / "phase613_parallel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
