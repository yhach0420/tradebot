#!/usr/bin/env python3
"""Phase 125: Post-fade reacceleration detection review (no implementation)."""

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
            flat = dict(row)
            if isinstance(flat.get("rule"), dict):
                flat["rule_json"] = json.dumps(flat.pop("rule"), ensure_ascii=False)
            w.writerow(flat)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 125 reacceleration review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.mfe_mae_exit_review import discover_sessions
    from research.reacceleration_review import analyze_reacceleration

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_reacceleration(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase125_reacceleration_review.json"
    cluster_csv = REPORTS / "phase125_reacceleration_clusters.csv"
    rules_csv = REPORTS / "phase125_reacceleration_rule_candidates.csv"
    paths_csv = REPORTS / "phase125_post_fade_price_paths.csv"

    report: dict[str, Any] = {
        "phase": 125,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "reacceleration_detectable",
            "B": "reacceleration_partially_detectable",
            "C": "need_additional_intraday_features",
            "D": "fade_is_terminal",
        },
        "fade_trade_count": result["fade_trade_count"],
        "horizon_summary": result["horizon_summary"],
        "clusters": result["clusters"],
        "top_rules": result["rule_candidates"][:15],
        "methodology": {
            "horizons_sec": [30, 60, 120],
            "reacceleration_definition": (
                "new_high_after_fade AND price_above_fade AND "
                "(momentum_recovery OR new_mfe_created)"
            ),
            "price_source": "small_paper_events.csv current_price",
            "no_implementation": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "clusters_csv": _rel(cluster_csv),
            "rules_csv": _rel(rules_csv),
            "paths_csv": _rel(paths_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(cluster_csv, result["clusters"])
    _write_csv(rules_csv, result["rule_candidates"])
    _write_csv(paths_csv, result["price_paths"])

    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "fade_trades": result["fade_trade_count"],
                "horizon_summary": result["horizon_summary"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
