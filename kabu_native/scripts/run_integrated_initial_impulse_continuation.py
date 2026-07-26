#!/usr/bin/env python3
"""Run Integrated Initial Impulse Continuation (IIC) research."""
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
            [sys.executable, "-m", "pytest", "tests/test_integrated_initial_impulse_continuation.py", "-q", "--tb=line"],
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
            "rows": [{"name": "test_integrated_initial_impulse_continuation", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed), flush=True)

    from research.integrated_initial_impulse_continuation.runner import run_iic

    payload = run_iic(run_id=args.run_id, test_results=test_results)
    c = payload.get("completion") or {}
    print("final:", c.get("29_final_verdict"))
    print("episodes/entries:", c.get("4_all_episodes"), c.get("5_entry_n"))
    print("A5 pf/pnl/mean:", (c.get("8_pf") or {}).get("A5"), (c.get("9_pnl") or {}).get("A5"), (c.get("10_mean") or {}).get("A5"))
    print("train:", c.get("21_train"))
    print("out:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
