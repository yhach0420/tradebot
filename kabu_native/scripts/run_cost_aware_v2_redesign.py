#!/usr/bin/env python
"""Run Cost-Aware V2 redesign research → report.md / report.json / audit.xlsx."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.cost_aware_v2.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    payload = run_pipeline(native=ROOT)
    print("verdict:", payload.get("verdict"))
    print("n_trades:", payload.get("n_trades"))
    print("n_days_usable:", payload.get("n_days_usable"))
    print("best:", payload.get("best_candidate"))
    print("delta_5bps:", (payload.get("best_metrics") or {}).get("delta_5bps"))
    print("out:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
