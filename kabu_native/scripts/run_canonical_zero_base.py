#!/usr/bin/env python3
"""Run Canonical Zero-Base Entry–Exit Strategy Rebuild."""
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
    p.add_argument("--stride", type=int, default=12)
    p.add_argument("--skip-tests", action="store_true")
    args = p.parse_args()
    from research.canonical_zero_base.runner import run_zero_base

    test_results = None
    if not args.skip_tests:
        import subprocess

        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_canonical_zero_base.py", "-q", "--tb=line"],
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
            "rows": [{"name": "test_canonical_zero_base", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed))

    print("running zero-base pipeline stride=", args.stride, flush=True)
    payload = run_zero_base(run_id=args.run_id, stride=args.stride, test_results=test_results)
    c = payload.get("completion") or {}
    print("final_verdict:", c.get("1_final_verdict"))
    print("train/val/oos:", c.get("3_train"), c.get("4_validation"), c.get("5_strict_oos"))
    print("Z1 oos:", c.get("20_Z1_oos"))
    print("integrated:", c.get("30_integrated_cap5"))
    print("out_dir:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
