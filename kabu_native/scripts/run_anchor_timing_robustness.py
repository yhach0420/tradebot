#!/usr/bin/env python
"""Offline CLOCK_GRID local-perturbation robustness. Does not start Paper. Does not change Runtime."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
