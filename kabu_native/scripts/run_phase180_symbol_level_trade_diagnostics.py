#!/usr/bin/env python3
"""
Phase180: Per-symbol diagnostics from small_paper_events (live shadow sessions).

Writes:
  kabu_native/results/reports/phase180_symbol_level_trade_diagnostics.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    repo = script.parents[2]
    native = script.parents[1]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo


def main() -> int:
    repo = _bootstrap()
    from small_paper.phase180_symbol_diagnostics import (
        aggregate_symbol_diagnostics,
        discover_session_dirs_for_day,
    )

    parser = argparse.ArgumentParser(description="Phase180 symbol-level trade diagnostics")
    parser.add_argument(
        "--day-stamp",
        default=datetime.now(JST).strftime("%Y%m%d"),
        help="YYYYMMDD session folder under small_paper/",
    )
    parser.add_argument(
        "--session-dir",
        action="append",
        default=[],
        help="Explicit session dir (repeatable); overrides day discovery",
    )
    args = parser.parse_args()

    out = Path("kabu_native/results/reports/phase180_symbol_level_trade_diagnostics.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.session_dir:
        dirs = [Path(p) if Path(p).is_absolute() else repo / p for p in args.session_dir]
    else:
        dirs = discover_session_dirs_for_day(repo, args.day_stamp)

    report = aggregate_symbol_diagnostics(dirs)
    report["day_stamp"] = args.day_stamp
    report["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out} symbols={report.get('symbol_count', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
