"""Run E1X5-FWD offline parity + forward readiness."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.e1_x5_forward_shadow.runner import run_e1x5_fwd
from research.e1_x5_forward_shadow.tests import run_tests


def main() -> None:
    tests = run_tests()
    print(f"[e1x5-fwd] tests passed={tests['passed']} {tests['n_passed']}/{tests['n_passed']+tests['n_failed']}", flush=True)
    payload = run_e1x5_fwd(test_results=tests)
    print(f"[e1x5-fwd] out={payload.get('out_dir')}", flush=True)
    print(f"[e1x5-fwd] verdict={payload.get('verdict')}", flush=True)


if __name__ == "__main__":
    main()
