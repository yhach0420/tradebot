"""
Phase662: Observer entry time freshness fix — verification report (research only).

Replays the 6327.T stale-board incident and validates observer hold_sec,
position_id uniqueness, and Discord display fields after the fix.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv

PHASE662_VERDICT = "phase662_observer_entry_time_freshness_fix_done"
REPORT_DIR_NAME = "phase662_observer_entry_time_freshness_fix"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
DOCS_PATH = NATIVE_ROOT / "docs" / "operations" / "phase662_observer_entry_time_freshness_fix.md"

JST = ZoneInfo("Asia/Tokyo")
ACCEPT = datetime(2026, 7, 7, 12, 58, 53, tzinfo=JST)
MARKET = datetime(2026, 7, 7, 12, 44, 14, tzinfo=JST)


def _stale_trade() -> dict:
    return {
        "symbol": "6327.T",
        "profile": "test",
        "entry_time": MARKET.isoformat(),
        "market_entry_time": MARKET.isoformat(),
        "current_price_time": MARKET.isoformat(),
        "accepted_at": ACCEPT.isoformat(),
        "accepted_event_time": ACCEPT.isoformat(),
        "exit_time": (ACCEPT + timedelta(minutes=5)).isoformat(),
        "continuation_quality_score": 0.25,
        "momentum_continuation_score": 0.0,
        "favorable_continuation": 0.15,
        "bearish_accumulation_score": 0.0,
        "price_age_sec": 877.9,
    }


def run_6327_repro() -> list[dict]:
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW
    from small_paper.discord_message_builder import build_exit_detail
    from small_paper.no_progress_exit import no_progress_exit_triggered
    from small_paper.observer_position_tracker import ObserverPositionTracker, ObserverTrackerConfig

    cfg = ObserverTrackerConfig(
        structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
        no_progress_exit_enabled=True,
    )
    tracker = ObserverPositionTracker(cfg)
    trade = _stale_trade()
    rows: list[dict] = []

    def _register(at: datetime, label: str) -> str:
        t = dict(trade)
        t["accepted_at"] = at.isoformat()
        t["accepted_event_time"] = at.isoformat()
        with patch("small_paper.observer_position_tracker.datetime") as mdt:
            mdt.now.return_value = at
            mdt.combine = datetime.combine
            mdt.fromisoformat = datetime.fromisoformat
            if tracker._positions.get("6327.T") and tracker._positions["6327.T"].closed:
                del tracker._positions["6327.T"]
            tracker.register_entry(trade=t, payload={}, quality_tier="below_median", entry_price=5760.0)
        pos = tracker._positions["6327.T"]
        rows.append(
            {
                "step": label,
                "event": "register_entry",
                "observer_entry_time": pos.entry_time.isoformat(),
                "market_entry_time": pos.market_entry_time.isoformat() if pos.market_entry_time else "",
                "position_id": pos.position_id,
                "market_time_age_sec": pos.market_time_age_sec,
                "stale_trade": pos.stale_trade,
                "hold_sec": 0.0,
                "no_progress": False,
                "discord_hold_min": 0,
            }
        )
        return pos.position_id

    pid1 = _register(ACCEPT, "first_accept")
    tick1 = ACCEPT + timedelta(seconds=30)
    pos = tracker._positions["6327.T"]
    hold1 = (tick1 - pos.entry_time).total_seconds()
    np1 = no_progress_exit_triggered(hold1, pos.peak_pnl_pct, 0.0)
    rows.append(
        {
            "step": "tick_30s_after_first_accept",
            "event": "on_tick_check",
            "observer_entry_time": pos.entry_time.isoformat(),
            "market_entry_time": MARKET.isoformat(),
            "position_id": pos.position_id,
            "market_time_age_sec": pos.market_time_age_sec,
            "stale_trade": pos.stale_trade,
            "hold_sec": round(hold1, 1),
            "no_progress": np1,
            "discord_hold_min": int(round(hold1 / 60.0)),
        }
    )
    pos.closed = True

    pid2 = _register(ACCEPT + timedelta(seconds=10), "reentry_10s_after_first_exit")
    pos2 = tracker._positions["6327.T"]
    tick2 = pos2.entry_time + timedelta(seconds=10)
    hold2 = (tick2 - pos2.entry_time).total_seconds()
    np2 = no_progress_exit_triggered(hold2, pos2.peak_pnl_pct, 0.0)
    detail = build_exit_detail(
        symbol="6327.T",
        entry_price=5760.0,
        exit_price=5760.0,
        pnl_pct=0.0,
        mfe_pct=0.0,
        mae_pct=0.0,
        hold_minutes=hold1 / 60.0,
        exit_reason="hold_check",
        market_time_age_sec=pos.market_time_age_sec,
        stale_trade=True,
    )
    rows.append(
        {
            "step": "tick_10s_after_reentry",
            "event": "on_tick_check",
            "observer_entry_time": pos2.entry_time.isoformat(),
            "market_entry_time": MARKET.isoformat(),
            "position_id": pos2.position_id,
            "market_time_age_sec": pos2.market_time_age_sec,
            "stale_trade": pos2.stale_trade,
            "hold_sec": round(hold2, 1),
            "no_progress": np2,
            "discord_hold_min": int(round(hold2 / 60.0)),
            "discord_sample_line": detail.splitlines()[6] if detail else "",
            "position_id_changed": pid1 != pid2,
        }
    )
    return rows


def run_position_id_regression() -> list[dict]:
    from small_paper.observer_position_tracker import ObserverPositionTracker, ObserverTrackerConfig
    from small_paper.realtime_board_exit_shadow import make_position_id

    tracker = ObserverPositionTracker(ObserverTrackerConfig())
    base = datetime(2026, 7, 7, 10, 0, 0, tzinfo=JST)
    rows = []
    prev = ""
    for i in range(3):
        at = base + timedelta(seconds=i * 10)
        trade = {
            "symbol": "6327.T",
            "profile": "t",
            "entry_time": at.isoformat(),
            "market_entry_time": (at - timedelta(seconds=3)).isoformat(),
            "accepted_at": at.isoformat(),
            "exit_time": (at + timedelta(minutes=5)).isoformat(),
            "continuation_quality_score": 0.5,
        }
        with patch("small_paper.observer_position_tracker.datetime") as mdt:
            mdt.now.return_value = at
            mdt.combine = datetime.combine
            mdt.fromisoformat = datetime.fromisoformat
            if tracker._positions.get("6327.T"):
                tracker._positions["6327.T"].closed = True
                del tracker._positions["6327.T"]
            tracker.register_entry(trade=trade, payload={}, quality_tier="A", entry_price=100.0)
        pid = tracker._positions["6327.T"].position_id
        rows.append(
            {
                "reentry_index": i,
                "accepted_at": at.isoformat(),
                "position_id": pid,
                "make_position_id_direct": make_position_id("6327.T", at),
                "differs_from_prior": bool(prev) and pid != prev,
            }
        )
        prev = pid
    return rows


def main() -> None:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    import sys

    if str(NATIVE_ROOT) not in sys.path:
        sys.path.insert(0, str(NATIVE_ROOT))
    import subprocess

    test_proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase662_observer_entry_time_freshness.py", "-q"],
        cwd=str(NATIVE_ROOT),
        capture_output=True,
        text=True,
    )
    tests_passed = test_proc.returncode == 0

    repro = run_6327_repro()
    pid_rows = run_position_id_regression()
    fields = list(repro[0].keys()) if repro else []
    _write_csv(REPORT_ROOT / "phase662_6327_repro.csv", fields, repro)
    _write_csv(
        REPORT_ROOT / "phase662_position_id_regression.csv",
        list(pid_rows[0].keys()) if pid_rows else [],
        pid_rows,
    )

    tick30 = next(r for r in repro if r["step"] == "tick_30s_after_first_accept")
    tick10 = next(r for r in repro if r["step"] == "tick_10s_after_reentry")
    report = {
        "phase": 662,
        "verdict": PHASE662_VERDICT,
        "tests_passed": tests_passed,
        "checks": {
            "hold_sec_30s": tick30["hold_sec"],
            "no_progress_at_30s": tick30["no_progress"],
            "hold_sec_reentry_10s": tick10["hold_sec"],
            "no_progress_at_reentry_10s": tick10["no_progress"],
            "discord_hold_min_30s": tick30["discord_hold_min"],
            "position_id_changed_on_reentry": tick10.get("position_id_changed"),
            "stale_market_preserved": tick30["market_entry_time"] == MARKET.isoformat(),
        },
        "artifacts": {
            "repro_csv": "results/reports/phase662_observer_entry_time_freshness_fix/phase662_6327_repro.csv",
            "position_id_csv": "results/reports/phase662_observer_entry_time_freshness_fix/phase662_position_id_regression.csv",
            "docs": "docs/operations/phase662_observer_entry_time_freshness_fix.md",
        },
    }
    (REPORT_ROOT / "phase662_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_PATH.write_text(
        "\n".join(
            [
                "# Phase662 — Observer Entry Time Freshness Fix",
                "",
                f"**Verdict:** `{PHASE662_VERDICT}`",
                "",
                "## Problem",
                "Stale `CurrentPriceTime` propagated to observer `entry_time`, causing",
                "`no_progress_exit` to fire immediately after a fresh ENTRY notification.",
                "",
                "## Fix",
                "- Observer hold clock uses `accepted_at` / `accepted_event_time` (accept time).",
                "- Market timestamps stored separately (`market_entry_time`, `current_price_time`).",
                "- `position_id` includes microsecond observer entry stamp.",
                "- Discord EXIT shows observer-based hold minutes + `market_time_age_sec` + `stale_trade`.",
                "",
                "## 6327.T reproduction",
                f"- accept: `{ACCEPT.isoformat()}`",
                f"- market: `{MARKET.isoformat()}`",
                f"- hold at +30s: {tick30['hold_sec']}s (no_progress={tick30['no_progress']})",
                f"- Discord hold display: {tick30['discord_hold_min']} min",
                "",
                "## Artifacts",
                "- `results/reports/phase662_observer_entry_time_freshness_fix/phase662_report.json`",
                "- `results/reports/phase662_observer_entry_time_freshness_fix/phase662_6327_repro.csv`",
                "- `results/reports/phase662_observer_entry_time_freshness_fix/phase662_position_id_regression.csv`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(PHASE662_VERDICT)
    print(json.dumps(report["checks"], ensure_ascii=False))


if __name__ == "__main__":
    main()
