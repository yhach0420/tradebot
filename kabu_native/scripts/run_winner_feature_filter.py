#!/usr/bin/env python
"""Run Winner Feature Filter forward validation (observe-only; writes 3 artifacts)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.winner_feature_filter.forward_pipeline import run_forward_pipeline


def main() -> int:
    payload = run_forward_pipeline(native=ROOT)
    out = ROOT / "results" / "research" / "winner_feature_filter"
    v = payload.get("verdict") or {}
    print("verdict", v.get("verdict"))
    print("reason", v.get("reason"))
    print("n_trades", payload.get("n_trades"), "n_days", payload.get("n_days"))
    print("wrote", out / "report.md")
    print("wrote", out / "report.json")
    print("wrote", out / "audit.xlsx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
