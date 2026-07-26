#!/usr/bin/env python3
"""Run Canonical FCR exact-method rebuild (Flow Confirmed Reclaim Entry)."""
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
    p.add_argument("--stride", type=int, default=6)
    p.add_argument("--skip-tests", action="store_true")
    args = p.parse_args()

    test_results = None
    if not args.skip_tests:
        import subprocess

        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_canonical_fcr_exact_method.py", "-q", "--tb=line"],
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
            "rows": [{"name": "test_canonical_fcr_exact_method", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed), flush=True)

    from research.canonical_fcr_exact_method.runner import run_fcr

    payload = run_fcr(run_id=args.run_id, stride=args.stride, test_results=test_results)
    c = payload.get("completion") or {}
    print("final_verdict:", c.get("1_final_verdict"))
    print("F0..F5 n:", c.get("21_F0_n"), c.get("22_F1_n"), c.get("23_F2_n"), c.get("24_F3_n"), c.get("25_F4_n"), c.get("26_F5_n"))
    print("train/val pass:", c.get("42_train_pass"), c.get("44_val_pass"))
    print("entry_verdict:", c.get("81_entry_verdict"), "exit:", c.get("82_exit_research"))
    print("E1:", c.get("65_E1"))
    print("out_dir:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
