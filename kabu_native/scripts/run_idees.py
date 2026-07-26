"""Run IDEES offline research."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.integrated_directional_entry_exit_strategy.runner import run_idees
from research.integrated_directional_entry_exit_strategy.tests import run_tests


def main() -> None:
    tests = run_tests()
    print(f"[idees] tests passed={tests['passed']} {tests['n_passed']}/{tests['n_passed']+tests['n_failed']}", flush=True)
    payload = run_idees(test_results=tests)
    print(f"[idees] out={payload.get('out_dir')}", flush=True)
    print(f"[idees] verdict={payload.get('verdict')}", flush=True)


if __name__ == "__main__":
    main()
