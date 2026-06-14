#!/usr/bin/env python3
"""
Phase262 auto-run wrapper for risk-aware sizing forward shadow after paper session.

Example::
    python kabu_native/scripts/run_phase262_risk_sizing_forward_shadow_auto.py --day 20260525
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase262 risk sizing forward shadow auto")
    parser.add_argument("--day", type=str, default=None)
    parser.add_argument("--reports-dir", type=Path, default=REPORTS)
    args = parser.parse_args()

    _bootstrap()
    from small_paper.risk_sizing_forward_shadow_auto import run_risk_sizing_forward_shadow_auto

    block = run_risk_sizing_forward_shadow_auto(
        repo_root=REPO,
        day=args.day,
        reports_dir=args.reports_dir,
    )
    print(f"status={block.get('status')}", flush=True)
    return 0 if block.get("status") != "warning" else 1


if __name__ == "__main__":
    raise SystemExit(main())
