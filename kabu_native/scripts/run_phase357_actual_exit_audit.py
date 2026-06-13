#!/usr/bin/env python3
"""Phase357: Actual EXIT audit on Phase355 post-population."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
REPORTS = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from research.phase357_actual_exit_audit import (
        MAX_DAY,
        MIN_DAY,
        Phase357ExitAudit,
        _load_session_trades,
    )
    from small_paper.phase356_live_session_evaluation import discover_live_sessions_for_phase356

    sessions = discover_live_sessions_for_phase356(SMALL_PAPER, min_day=MIN_DAY)
    sessions = [s for s in sessions if str(s.get("day_key") or "") <= MAX_DAY]

    audit = Phase357ExitAudit(reports_dir=REPORTS)
    for meta in sessions:
        result = _load_session_trades(meta, reports_dir=REPORTS)
        audit.ingest_session(result)

    paths = audit.finalize_outputs()
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))

    print("=== Phase357 Actual EXIT Audit ===", flush=True)
    print(f"sessions: {summary.get('sessions_loaded')}", flush=True)
    print(f"kept trades: {summary.get('trade_count_kept')}", flush=True)
    print(f"excluded pullback: {summary.get('trade_count_excluded_pullback')}", flush=True)
    print(f"total pnl: {summary.get('actual_total_pnl_yen_100')} PF={summary.get('actual_pf')}", flush=True)
    print("exit breakdown:", flush=True)
    for reason, met in sorted(
        (summary.get("exit_reason_breakdown") or {}).items(),
        key=lambda x: -(x[1].get("count") or 0),
    ):
        if met.get("count"):
            print(
                f"  {reason}: n={met.get('count')} pnl={met.get('total_pnl_yen_100')} "
                f"avg_mfe={met.get('avg_peak_mfe_pct')} avg_hold={met.get('avg_hold_sec')}s",
                flush=True,
            )
    loss = summary.get("loss_attribution") or {}
    print(f"dominant driver: {loss.get('dominant_driver')} - {loss.get('rationale')}", flush=True)
    print(f"outputs: {paths}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
