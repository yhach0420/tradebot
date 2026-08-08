#!/usr/bin/env python
"""Run E1_X7 Pullback Flow Quality study (research-only)."""
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

from research.e1_x7_pfq.run_study import run  # noqa: E402

if __name__ == "__main__":
    rep = run()
    print("E1_X7_PFQ_DONE", rep.get("run_id"), rep.get("verdict"), flush=True)
