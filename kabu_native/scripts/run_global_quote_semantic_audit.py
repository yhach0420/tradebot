#!/usr/bin/env python3
"""Run Global Quote Semantic Integrity Audit (S0). Offline only."""
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
    p.add_argument("--days", nargs="*", default=None)
    args = p.parse_args()
    from research.global_quote_semantic_audit.pipeline import run_audit
    from research.global_quote_semantic_audit.constants import AUDIT_DAYS

    days = tuple(args.days) if args.days else AUDIT_DAYS
    payload = run_audit(run_id=args.run_id, days=days)
    c = payload.get("completion") or {}
    print("final_verdict:", c.get("1_final_verdict"))
    print("out_dir:", payload.get("out_dir"))
    print("submit/cancel/live:", c.get("21_submit"), c.get("22_cancel"), c.get("23_live_order"))
    print("mainline_changed:", c.get("24_mainline_changed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
