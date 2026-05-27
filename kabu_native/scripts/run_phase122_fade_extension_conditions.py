#!/usr/bin/env python3
"""Phase 122: Fade extension condition analysis (review only)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
SMALL_PAPER = NATIVE / "results" / "small_paper"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for row in rows:
        for k in row:
            if k not in cols:
                cols.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 122 fade extension conditions")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.fade_extension_conditions import analyze_fade_extension_conditions
    from research.mfe_mae_exit_review import discover_sessions

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_fade_extension_conditions(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase122_fade_extension_conditions.json"
    cluster_csv = REPORTS / "phase122_fade_clusters.csv"
    rules_csv = REPORTS / "phase122_fade_rule_candidates.csv"

    report: dict[str, Any] = {
        "phase": 122,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "conditional_fade_extension_promising",
            "B": "fade_extension_not_predictable",
            "C": "take_reached_is_key_signal",
            "D": "quality_or_vol_liq_required",
        },
        "fade_trade_count": result["fade_trade_count"],
        "cluster_counts": result["cluster_counts"],
        "group_comparison": result["group_comparison"],
        "top_rule_candidates": result["rule_candidates"][:15],
        "methodology": {
            "hold_whatif": "+60s after actual fade exit (Phase121 C_hold_60s)",
            "cluster_A": "hold60_delta > 0.01% and not loss_expanded",
            "cluster_B": "no meaningful improvement",
            "cluster_C": "hold60 worse than exit and hold60 < 0",
            "features": [
                "mfe_pct",
                "mae_pct",
                "pnl_at_exit",
                "hold_sec",
                "quality_score",
                "vol_liq_score",
                "vwap_distance",
                "candidate_rank",
                "take_reached",
                "overlap_replaced",
            ],
            "no_implementation": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "clusters_csv": _rel(cluster_csv),
            "rules_csv": _rel(rules_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(cluster_csv, result["cluster_rows"])
    _write_csv(rules_csv, result["rule_candidates"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "fade_trades": result["fade_trade_count"],
                "clusters": result["cluster_counts"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
