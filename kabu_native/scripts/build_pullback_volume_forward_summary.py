#!/usr/bin/env python3
"""Rebuild day + cumulative Pullback Volume Forward summaries from JSONL SoT."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

from small_paper.pullback_volume_forward_logger import (
    DEFAULT_OUT_DIR,
    load_day_rows,
    rebuild_cumulative,
    write_day_summary,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--day", type=str, default="", help="YYYYMMDD; empty = all days")
    args = ap.parse_args()
    out = args.out_dir
    if args.day:
        rows = load_day_rows(out, args.day)
        write_day_summary(out, args.day, rows)
        print(f"day_summary {args.day} n={len(rows)}")
    else:
        for p in sorted(out.glob("pullback_volume_forward_????????.jsonl")):
            day = p.name.replace("pullback_volume_forward_", "").replace(".jsonl", "")
            rows = load_day_rows(out, day)
            write_day_summary(out, day, rows)
            print(f"day_summary {day} n={len(rows)}")
    cum = rebuild_cumulative(out)
    print(
        "cumulative",
        cum.get("total_pullback_hits"),
        "gate",
        cum.get("sample_gate", {}).get("forward_sample_gate_pass"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
