#!/usr/bin/env python
"""Run Price-Flow EXIT offline research."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.price_flow_exit.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    payload = run_pipeline(native=ROOT)
    v = payload.get("verdict") or {}
    print("verdict:", v.get("final"))
    print("codes:", ",".join(v.get("codes") or []))
    print("cohorts:", payload.get("cohort_sizes"))
    print("out:", payload.get("out_dir"))
    print("submit/cancel/live_order:", payload.get("submit"), payload.get("cancel"), payload.get("live_order"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
