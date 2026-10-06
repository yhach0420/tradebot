"""SUPPORT_RESISTANCE_FIRST_INTERACTION_MATCHED_CAUSAL_TEST_V1. Discovery only. Frozen detector. No PnL. Runtime 0/0/0."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.support_resistance_first_interaction_matched_causal_test_v1.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
