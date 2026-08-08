"""Run TAER economic integrity fix + same-condition rerun."""
from __future__ import annotations

import os
import sys
from pathlib import Path

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "research"))

from research.e1_x6_taer.integrity_rerun import run  # noqa: E402


def main() -> None:
    print("=== TAER Economic Integrity Fix + Same-Condition Rerun ===", flush=True)
    print("freeze e1x6_taer_exit_joint_20260804_001315 = TAER_V1_JOINT_INVALID_ECONOMIC_INTEGRITY", flush=True)
    rep = run()
    print("=== PUBLISHED ===", flush=True)
    print("run_id", rep.get("run_id"), flush=True)
    print("verdict", rep.get("verdict"), flush=True)
    print("economic_integrity_status", rep.get("economic_integrity_status"), flush=True)
    print("ab_ok", (rep.get("determinism") or {}).get("ab_ok"), flush=True)
    for k, v in (rep.get("published") or {}).items():
        print(k, v, flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
