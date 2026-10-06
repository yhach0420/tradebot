"""PB1_V3_2_DISCOVERY_UNSEEN_FACE_VERIFY. Frozen V3.2. Runtime 0/0/0."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.pb1_v3_2_discovery_unseen_face_verify.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
