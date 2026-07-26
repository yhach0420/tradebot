#!/usr/bin/env python3
"""Run Canonical VCIE exact-method rebuild."""
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
    p.add_argument("--stride", type=int, default=4)
    p.add_argument("--skip-tests", action="store_true")
    args = p.parse_args()

    test_results = None
    if not args.skip_tests:
        import subprocess

        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_canonical_vcie_exact_method.py", "-q", "--tb=line"],
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
            "rows": [{"name": "test_canonical_vcie_exact_method", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed), flush=True)

    from research.canonical_vcie_exact_method.runner import run_vcie

    payload = run_vcie(run_id=args.run_id, stride=args.stride, test_results=test_results)
    c = payload.get("completion") or {}
    print("final_verdict:", c.get("1_final_verdict"))
    print("lineage:", c.get("2_volume_lineage"), c.get("3_trade_direction_lineage"), c.get("4_session_time_lineage"), c.get("5_canonical_execution_lineage"))
    print("V1..V4 n:", c.get("20_V1_n"), c.get("21_V2_n"), c.get("22_V3_n"), c.get("23_V4_n"))
    print("train/val pass:", c.get("33_train_pass"), c.get("35_val_pass"))
    print("entry_verdict:", c.get("64_entry_verdict"), "exit:", c.get("65_exit_research"))
    print("E1:", c.get("52_E1"))
    print("out_dir:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
