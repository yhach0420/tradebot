#!/usr/bin/env python
"""Run Winner Multiclass offline research (observe-only; writes 3 artifacts)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.winner_multiclass.pipeline import run_pipeline


def main() -> int:
    payload = run_pipeline(native=ROOT)
    out = ROOT / "results" / "research" / "winner_multiclass"
    c = payload.get("completion") or {}
    print("verdict", c.get("1_verdict"))
    print("days/n", c.get("2_days_n"))
    print("classes", c.get("3_class_counts"))
    print("best_model", c.get("6_best_model"), "macro_f1", c.get("7_macro_f1"))
    print("oos pnl/pf", c.get("11_oos_pnl_pf"), "delta", c.get("12_delta_vs_pbv2"))
    print("best_keep", c.get("13_best_keep_rate"))
    print("wrote", out / "report.md")
    print("wrote", out / "report.json")
    print("wrote", out / "audit.xlsx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
