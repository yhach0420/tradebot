#!/usr/bin/env python3
"""Run Phase611 disk-safe parallel trace: cleanup then 4 jobs + aggregate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _kill_stuck_research_workers() -> None:
    """Stop orphaned multiprocessing.spawn workers (not pilot_runner)."""
    try:
        import psutil
    except ImportError:
        return
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "multiprocessing.spawn" in cmd and "spawn_main" in cmd:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def main() -> int:
    _kill_stuck_research_workers()

    cleanup = ROOT / "scripts" / "disk_cleanup_research_artifacts.py"
    print("=== P0 disk cleanup ===")
    subprocess.run([sys.executable, str(cleanup)], check=True, cwd=str(ROOT))

    from research.phase611_disk_safe_parallel_trace import run_parallel

    print("=== P2 parallel jobs (4 workers) ===")
    report = run_parallel(repo_root=ROOT, max_workers=4)
    print(report["verdict"])
    for k, v in sorted(report.get("mandatory_answers", {}).items()):
        print(f"{k}: {v}")
    print("output:", ROOT / "results" / "reports" / "phase611_parallel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
