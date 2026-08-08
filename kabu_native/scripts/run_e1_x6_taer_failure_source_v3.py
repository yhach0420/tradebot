#!/usr/bin/env python
"""Run E1_X6 TAER Failure Source Analysis V3 (research-only)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "research"))

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from research.e1_x6_taer.failure_source.run_v3 import run  # noqa: E402

if __name__ == "__main__":
    rep = run()
    print("FSA_V3_DONE", rep.get("run_id"), rep.get("verdict"), flush=True)
