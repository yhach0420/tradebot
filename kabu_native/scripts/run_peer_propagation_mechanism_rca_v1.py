"""PEER_PROPAGATION_MECHANISM_RCA_V1. RCA only. Runtime 0/0/0."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.peer_propagation_mechanism_rca_v1.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
