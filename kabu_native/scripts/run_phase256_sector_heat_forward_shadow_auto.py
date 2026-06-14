#!/usr/bin/env python3
"""
Phase256-SectorHeat-Forward-Shadow-Auto (research orchestration)

Manually trigger the same auto-run hook used after live paper session aggregation.

Example::
    python kabu_native/scripts/run_phase256_sector_heat_forward_shadow_auto.py --day 20260525
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    repo_root = script.parents[2]
    for p in (repo_root / "kabu_native" / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase256 sector heat forward shadow auto-run")
    parser.add_argument("--day", type=str, default=None, help="Validation day YYYYMMDD (default: today JST)")
    parser.add_argument("--session-dir", type=Path, default=None, help="Optional live session output directory")
    args = parser.parse_args()

    repo_root = _bootstrap()
    from small_paper.sector_heat_forward_shadow_auto import run_sector_heat_forward_shadow_auto

    day = args.day or datetime.now(JST).strftime("%Y%m%d")
    block = run_sector_heat_forward_shadow_auto(
        repo_root=repo_root,
        output_dir=args.session_dir,
        day=day,
    )
    return 0 if block.get("status") != "warning" else 0


if __name__ == "__main__":
    raise SystemExit(main())
