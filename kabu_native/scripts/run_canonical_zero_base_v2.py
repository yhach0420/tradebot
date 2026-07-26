#!/usr/bin/env python3
"""Run Canonical Zero-Base v2 Full Feature Discovery & Joint ENTRY–EXIT Rebuild."""
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

    test_results = None
    if not args.skip_tests:
        import subprocess

        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_canonical_zero_base_v2.py", "-q", "--tb=line"],
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
            "rows": [{"name": "test_canonical_zero_base_v2", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed), flush=True)

    from research.canonical_zero_base_v2.runner import run_v2

    payload = run_v2(run_id=args.run_id, stride=args.stride, test_results=test_results)
    c = payload.get("completion") or {}
    print("final_verdict:", c.get("1_final_verdict"))
    print("train/val/oos:", c.get("3_train"), c.get("4_validation"), c.get("5_strict_oos"))
    print("entry_features:", c.get("9_entry_features"), "exit_features:", c.get("10_exit_features"))
    print("train_entry_pass:", c.get("27_train_entry_pass"))
    print("val_pair_pass:", c.get("35_val_pair_pass"))
    print("E1/S1:", c.get("49_E1_S1"))
    print("out_dir:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
