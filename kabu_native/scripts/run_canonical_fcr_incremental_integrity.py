#!/usr/bin/env python3
"""Run Canonical FCR Incremental Integrity Closure."""
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
            [sys.executable, "-m", "pytest", "tests/test_canonical_fcr_incremental_integrity.py", "-q", "--tb=line"],
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
            "rows": [{"name": "test_canonical_fcr_incremental_integrity", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed), flush=True)

    from research.canonical_fcr_incremental_integrity.runner import run_integrity

    payload = run_integrity(run_id=args.run_id, test_results=test_results)
    c = payload.get("completion") or {}
    print("final:", c.get("1_final_verdict"))
    print("integrity:", c.get("2_integrity_verdict"))
    print("stride:", c.get("3_stride_meaning"), "parity:", c.get("11_stride1_parity"))
    print("nesting:", c.get("26_arm_nesting"), "spread:", c.get("31_f5_spec_conformance"))
    print("F0..F5:", c.get("38_matched_F0_n"), c.get("39_matched_F1_n"), c.get("40_matched_F2_n"), c.get("41_matched_F3_n"), c.get("42_matched_F4_n"), c.get("43_matched_F5_n"))
    print("train_gate:", c.get("65_train_gate"))
    print("out:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
