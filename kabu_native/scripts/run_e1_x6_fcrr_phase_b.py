"""Run E1_X6_FCRR Phase B (P1_ENTRY_PRECOMMIT + Reachability)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "research"))

from research.e1_x6_fcrr.phase_b import run_phase_b  # noqa: E402


def main() -> None:
    print("=== Phase B: P1_ENTRY_PRECOMMIT + Reachability (no economics) ===", flush=True)
    rep = run_phase_b()
    print("=== PHASE_B_PUBLISHED ===", flush=True)
    print("phase_b_run_id", rep["phase_b_run_id"], flush=True)
    print("selection_status", rep["selection_status"], flush=True)
    print("reachable", rep["reachable_candidate_ids"], flush=True)
    for k, v in (rep.get("published") or {}).items():
        print(k, v, flush=True)
    print("STOP_PHASE_B" if not rep["reachable_candidate_ids"] else "NEXT_PHASE_C", flush=True)


if __name__ == "__main__":
    main()
