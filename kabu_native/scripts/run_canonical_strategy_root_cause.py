#!/usr/bin/env python3
"""Run Canonical Strategy Root Cause Closure (offline)."""
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
    from research.canonical_strategy_root_cause.pipeline import run_root_cause

    payload = run_root_cause(run_id=args.run_id, days=args.days, stride=args.stride)
    c = payload.get("completion") or {}
    print("final_verdict:", c.get("1_final_verdict"))
    print("primary:", (payload.get("verdict") or {}).get("primary_root_cause"))
    print("C0:", (c.get("17_C0_C8") or {}).get("C0"))
    print("C8:", (c.get("17_C0_C8") or {}).get("C8"))
    print("out_dir:", payload.get("out_dir"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
