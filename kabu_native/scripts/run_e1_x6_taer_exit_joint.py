"""Run TAER EXIT joint completion audit (does not overwrite prior TAER run)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "research"))

from research.e1_x6_taer.exit_joint_audit import run_exit_joint_audit  # noqa: E402


def main() -> None:
    print("=== TAER EXIT Joint Completion Audit ===", flush=True)
    print("prior=e1x6_taer_20260803_232514 -> TAER_ENTRY_PATH_READY / INSUFFICIENT_EXIT_EVIDENCE", flush=True)
    rep = run_exit_joint_audit()
    print("=== PUBLISHED ===", flush=True)
    print("run_id", rep["run_id"], flush=True)
    print("verdict", rep["verdict"], flush=True)
    print("joint_status", rep["joint_status"], flush=True)
    print("distinct", rep["ledger_sha_distinct"], flush=True)
    for k, v in (rep.get("published") or {}).items():
        print(k, v, flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
