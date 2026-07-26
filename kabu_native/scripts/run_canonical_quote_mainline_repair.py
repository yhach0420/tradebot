#!/usr/bin/env python3
"""Run Canonical Quote Mainline Repair & Dual Replay Closure."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", default=None)
    p.add_argument("--stride", type=int, default=5)
    p.add_argument("--days", nargs="*", default=None)
    args = p.parse_args()
    from research.canonical_quote_mainline_repair.pipeline import run_repair

    payload = run_repair(run_id=args.run_id, days=args.days, stride=args.stride)
    c = payload.get("completion") or {}
    print("paper_readiness:", c.get("31_paper_readiness"))
    print("integrity:", c.get("6_canonical_integrity"))
    print("P3:", c.get("18_P3"))
    print("out_dir:", payload.get("out_dir"))
    print("submit/cancel/live:", c.get("35_submit"), c.get("36_cancel"), c.get("37_live_order"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
