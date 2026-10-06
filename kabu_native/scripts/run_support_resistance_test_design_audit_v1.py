"""SUPPORT_RESISTANCE_TEST_DESIGN_AUDIT_V1. Discovery only. No PnL. Runtime 0/0/0."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.support_resistance_test_design_audit_v1.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
