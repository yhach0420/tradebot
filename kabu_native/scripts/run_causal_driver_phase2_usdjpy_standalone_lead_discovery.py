"""Causal Driver Phase 2 USDJPY standalone causal lead discovery. Runtime 0/0/0. Does not start Phase 3."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + str(os.environ.get("PYTHONPATH", ""))

from research.causal_driver_pb1.phase2_discovery.__main__ import main

if __name__ == "__main__":
    out = main()
    blocked = out.get("VERDICT") == "USDJPY_STANDALONE_CAUSAL_LEAD_DISCOVERY_BLOCKED_V1"
    raise SystemExit(1 if blocked else 0)
