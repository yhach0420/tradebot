#!/usr/bin/env python3
"""Run UEIA economic gate repair + flow delay audit."""
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
            [sys.executable, "-m", "pytest", "tests/test_ueia_economic_gate_and_flow_delay.py", "-q", "--tb=line"],
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
            "rows": [{"name": "test_ueia_repair", "status": "PASS" if passed else "FAIL"}],
        }
        print("tests:", test_results["summary"], "passed=" + str(passed), flush=True)

    from research.ueia_economic_gate_and_flow_delay.runner import run_repair

    payload = run_repair(run_id=args.run_id, test_results=test_results)
    c = payload.get("completion") or {}
    print("final:", c.get("40_final_verdict"))
    print("fixed:", c.get("18_fixed_candidate"), "val:", c.get("21_val_verdict"), "hold:", c.get("24_hold_verdict"))
    print("B4_H3_as_fixed:", c.get("b4_h3_selected_as_fixed"))
    print("out:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
