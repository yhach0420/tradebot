#!/usr/bin/env python
"""Offline edge-decay RCA. Does not start Paper. Does not change Runtime."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "scripts"))

from research.edge_decay_rca.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
