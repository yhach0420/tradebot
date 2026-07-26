"""Run DEECPA offline research."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.directional_edge_economic_closure_passive_execution.runner import run_deecpa
from research.directional_edge_economic_closure_passive_execution.tests import run_tests


def main() -> None:
    tests = run_tests()
    print(f"[deecpa] tests passed={tests['passed']} {tests['n_passed']}/{tests['n_passed']+tests['n_failed']}", flush=True)
    payload = run_deecpa(test_results=tests)
    print(f"[deecpa] out={payload.get('out_dir')}", flush=True)
    print(f"[deecpa] verdict={payload.get('verdict')}", flush=True)


if __name__ == "__main__":
    main()
