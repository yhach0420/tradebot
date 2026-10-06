#!/usr/bin/env python
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
runpy.run_module("research.uniform10_entry_rebuild", run_name="__main__")
