#!/usr/bin/env python3
"""
kabu_signal_v1 / kabu_exit_v1 構造分析（クラスタ・時間帯・ENTRY/EXIT 分離）。

個別銘柄チューニングは行わない。市場条件で壊れやすい領域を特定する。

例::
    python scripts/kabu_signal_structure_analysis.py --day 2026-05-15
    python scripts/kabu_signal_structure_analysis.py --day 2026-05-15 --tier B
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


_ROOT = _project_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.kabu_signal_structure_analysis import (  # noqa: E402
    run_structure_analysis,
    write_structure_outputs,
)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="kabu signal/exit structure analysis")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD")
    ap.add_argument("--tier", default="B", choices=["A", "B"])
    ap.add_argument("--no-replay-relaxed-gates", action="store_true")
    ap.add_argument("--synthetic-events-per-minute", type=int, default=10)
    ap.add_argument("--out-dir", help="出力先")
    args = ap.parse_args(argv)

    day_dir = _ROOT / "data" / "intraday_1m" / args.day
    if not day_dir.is_dir():
        print(f"not found: {day_dir}", file=sys.stderr)
        return 1

    yahoo_csv_by_symbol = {p.stem: p for p in sorted(day_dir.glob("*.csv"))}
    if not yahoo_csv_by_symbol:
        print("no csv files", file=sys.stderr)
        return 1

    print(f"symbols={len(yahoo_csv_by_symbol)} day={args.day} tier={args.tier}")
    analysis = run_structure_analysis(
        day=args.day,
        yahoo_csv_by_symbol=yahoo_csv_by_symbol,
        tier=args.tier,
        replay_relaxed=not args.no_replay_relaxed_gates,
        synthetic_events_per_minute=args.synthetic_events_per_minute,
    )

    day_key = args.day.replace("-", "")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else _ROOT / "results" / "kabu_signal_structure" / day_key / f"structure_{stamp}"
    )
    write_structure_outputs(out_dir, analysis)

    trades_n = len(analysis["trades"])
    print(f"output: {out_dir}")
    print(f"trades={trades_n}")
    for cluster, stats in sorted(analysis["by_cluster"].items()):
        print(
            f"  [{cluster}] trades={stats.get('trades')} "
            f"bf_rate={stats.get('breakout_failure_rate')} "
            f"low_q_entry={stats.get('low_quality_entry_rate')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
