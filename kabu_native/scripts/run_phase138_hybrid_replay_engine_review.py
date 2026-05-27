#!/usr/bin/env python3
"""Phase 138: Hybrid live accepted + structural exit replay engine review."""

from __future__ import annotations

import argparse
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
PHASE134_PAIRS = REPORTS / "phase134_fade_switch_pairs.csv"


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
    import csv

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 138 hybrid replay engine")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--phase134-pairs", default=str(PHASE134_PAIRS))
    args = parser.parse_args()

    _bootstrap()
    from research.hybrid_replay_engine_review import analyze_hybrid_replay_engine
    from research.replay_fidelity_review import discover_fidelity_sessions

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_fidelity_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_hybrid_replay_engine(
        session_dirs,
        phase134_pairs_path=Path(args.phase134_pairs),
    )
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase138_hybrid_replay_engine_review.json"
    timeline_csv = REPORTS / "phase138_hybrid_timeline.csv"
    pair_csv = REPORTS / "phase138_switch_match_diagnostics.csv"
    fidelity_csv = REPORTS / "phase138_replay_fidelity_summary.csv"

    report: dict[str, Any] = {
        "phase": 138,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "hybrid_replay_ready",
            "B": "hybrid_replay_partial",
            "C": "hybrid_replay_still_mismatched",
            "D": "need_live_engine_trace",
        },
        "aggregate": result["aggregate"],
        "phase137_baseline": {
            "phase134_pair_match_rate_cap3": 0.2755,
            "structural_replay_trade_match_rate": 0.9742,
        },
        "methodology": {
            "review_only": True,
            "replay_mode": "hybrid_live_accepted_structural_exit",
            "entry_source": "structural_trades.csv entry_time (live accepted path)",
            "exit_source": "structural_trades.csv close_time / close_reason",
            "cap_state": "rebuilt from hybrid timeline (max 3 slots)",
            "switch_detection": "fade exit + cross-symbol entry within 300s",
            "whatif": "policy tags only; no production logic change",
        },
        "outputs": {
            "json": _rel(out_json),
            "timeline_csv": _rel(timeline_csv),
            "pair_csv": _rel(pair_csv),
            "fidelity_csv": _rel(fidelity_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(timeline_csv, result["timeline"])
    _write_csv(pair_csv, result["pair_diagnostics"])
    _write_csv(fidelity_csv, result["fidelity_summary"])

    print(
        json.dumps(
            {"verdict": result["verdict"], "aggregate": result["aggregate"]},
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
