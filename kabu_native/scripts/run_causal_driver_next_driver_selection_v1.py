"""Select next Causal Driver family. Runtime 0/0/0. Does not start the next precommit."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + str(os.environ.get("PYTHONPATH", ""))

from research.causal_driver_pb1.next_driver_selection.__main__ import main

if __name__ == "__main__":
    out = main()
    raise SystemExit(0 if out.get("ok") else 1)
