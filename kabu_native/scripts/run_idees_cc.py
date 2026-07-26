"""Run IDEES-CC offline research."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.idees_fixed_candidate_concentration_oos.runner import run_idees_cc
from research.idees_fixed_candidate_concentration_oos.tests import run_tests


def main() -> None:
    tests = run_tests()
    print(f"[idees-cc] tests passed={tests['passed']} {tests['n_passed']}/{tests['n_passed']+tests['n_failed']}", flush=True)
    payload = run_idees_cc(test_results=tests)
    print(f"[idees-cc] out={payload.get('out_dir')}", flush=True)
    print(f"[idees-cc] verdict={payload.get('verdict')}", flush=True)


if __name__ == "__main__":
    main()
