#!/usr/bin/env python3
"""Run Integrated Order Flow Absorption Reversal (IOAR)."""
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
            [sys.executable, "-m", "pytest", "tests/test_integrated_order_flow_absorption_reversal.py", "-q", "--tb=line"],
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
            "rows": [{"name": "test_ioar", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed), flush=True)

    from research.integrated_order_flow_absorption_reversal.runner import run_ioar

    payload = run_ioar(run_id=args.run_id, test_results=test_results)
    c = payload.get("completion") or {}
    print("final:", c.get("51_final_verdict"))
    print("train_days/entry:", c.get("2_train_days"), c.get("13_entry_n"))
    print("S1..S5:", c.get("8_S1"), c.get("9_S2"), c.get("10_S3"), c.get("11_S4"), c.get("12_S5"))
    print("A5 pf/pnl:", (c.get("18_pf") or {}).get("A5"), (c.get("19_pnl") or {}).get("A5"))
    print("cause:", c.get("42_train_fail_cause"))
    print("out:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
