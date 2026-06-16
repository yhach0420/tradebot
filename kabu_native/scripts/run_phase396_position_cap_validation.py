#!/usr/bin/env python3
"""Phase396: Validate position-CAP runtime expectations on 2026-06-15 PM session."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent


def _bootstrap() -> None:
    for p in (REPO / "src", PARENT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()
    from research.equity_curve_shadow import normalize_structural_trade
    from research.phase271_leverage_attribution_and_robustness import simulate_audited
    from research.phase385_cap_sensitivity_study import simulate_cap

    session_dir = REPO / "results" / "small_paper" / "20260615" / "live_session_122531"
    structural_path = session_dir / "structural_trades.csv"
    if not structural_path.is_file():
        print(json.dumps({"ok": False, "error": f"missing {structural_path}"}, indent=2))
        return 1

    import csv

    trades = []
    with structural_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = dict(row)
            t["exit_time"] = t.get("close_time") or t.get("exit_time")
            trades.append(normalize_structural_trade(t))

    cap_sim = simulate_cap(trades, cap=3, initial_equity=1_500_000.0, equity_floor=750_000.0)
    audited = simulate_audited(
        trades,
        starting_equity=1_500_000,
        leverage=2.0,
        cap=3,
        stop_policy="fixed_stop_1p2",
    )
    pnl = round(float(audited.get("final_equity") or 1_500_000) - 1_500_000, 2)
    burst = sum(
        1
        for e in []
    )
    # session_close burst from prior session artifacts
    summary_path = session_dir / "small_paper_summary.json"
    if summary_path.is_file():
        old = json.loads(summary_path.read_text(encoding="utf-8"))
        burst = sum(
            1
            for _ in range(int(old.get("observer_exit_count") or 0))
            if False
        )
    events_path = session_dir / "small_paper_events.csv"
    if events_path.is_file():
        with events_path.open(encoding="utf-8", newline="") as f:
            burst = sum(
                1
                for row in csv.DictReader(f)
                if row.get("event_type") == "observer_exit"
                and str(row.get("exit_time", "")).startswith("2026-06-15T15:23")
            )

    result = {
        "ok": True,
        "session": "20260615/live_session_122531",
        "position_cap_mode_expected": {
            "accepted": 22,
            "rejected_by_cap": 58,
            "pnl_yen_100": 18700.0,
            "session_close_burst_max": 3,
        },
        "simulate_cap": {
            "accepted_trade_count": cap_sim.get("accepted_trade_count"),
            "position_cap_reject_count": cap_sim.get("position_cap_reject_count"),
            "total_pnl_yen_100": cap_sim.get("total_pnl_yen_100"),
        },
        "simulate_audited": {
            "accepted_trade_count": audited.get("accepted_trade_count"),
            "reject_reason_counts": audited.get("reject_reason_counts"),
            "pnl_yen_100": pnl,
        },
        "legacy_session_observer_exit_burst_1523": burst,
        "pass": (
            int(cap_sim.get("accepted_trade_count") or 0) == 22
            and int(cap_sim.get("position_cap_reject_count") or 0) == 58
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
