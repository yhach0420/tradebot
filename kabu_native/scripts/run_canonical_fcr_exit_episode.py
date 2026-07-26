#!/usr/bin/env python3
"""Run Canonical FCR EXIT episode construction."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", default=None)
    p.add_argument("--skip-tests", action="store_true")
    args = p.parse_args()

    test_results = None
    if not args.skip_tests:
        import subprocess

        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_canonical_fcr_exit_episode.py", "-q", "--tb=line"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        out = (r.stdout or "") + (r.stderr or "")
        passed = r.returncode == 0
        test_results = {
            "all_passed": passed,
            "returncode": r.returncode,
            "summary": out.strip().splitlines()[-1] if out.strip() else "",
            "rows": [{"name": "test_canonical_fcr_exit_episode", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed), flush=True)

    from research.canonical_fcr_exit_episode.runner import run_exit

    payload = run_exit(run_id=args.run_id, test_results=test_results)
    c = payload.get("completion") or {}
    print("final:", c.get("20_final_verdict"))
    print("classes:", c.get("3_healthy_n"), c.get("4_temporary_n"), c.get("5_false_reclaim_n"), c.get("6_noprogress_n"), c.get("7_winner_giveback_n"))
    print("X5 pf/pnl:", c.get("16_final_pf"), c.get("17_final_pnl"))
    print("val:", c.get("19_validation"))
    print("out:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
