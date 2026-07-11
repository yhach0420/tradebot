"""Phase671 — Early STOP / churn / feature discovery tests."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase671_early_stop_feature_discovery import (  # noqa: E402
    _analyze_churn,
    _early_stop_summary,
    _hold_sec,
    _is_early_stop,
    _is_stop_hit,
    run_audit,
)

JST = timezone(timedelta(hours=9))


class TestEarlyStopHelpers(unittest.TestCase):
    def test_early_stop_detection(self) -> None:
        trade = {"exit_reason": "stop_hit", "hold_sec": 120.0}
        self.assertTrue(_is_stop_hit(trade))
        self.assertTrue(_is_early_stop(trade))

    def test_non_early_stop(self) -> None:
        trade = {"exit_reason": "stop_hit", "hold_sec": 400.0}
        self.assertFalse(_is_early_stop(trade))

    def test_hold_sec_from_times(self) -> None:
        et = datetime(2026, 6, 1, 9, 10, tzinfo=JST)
        xt = datetime(2026, 6, 1, 9, 12, tzinfo=JST)
        trade = {"entry_time": et.isoformat(), "exit_time": xt.isoformat(), "exit_reason": "stop_hit"}
        self.assertEqual(_hold_sec(trade), 120.0)
        self.assertTrue(_is_early_stop(trade))

    def test_churn_detects_reentry_within_30m(self) -> None:
        et1 = datetime(2026, 6, 1, 9, 10, tzinfo=JST)
        xt1 = datetime(2026, 6, 1, 9, 11, tzinfo=JST)
        et2 = datetime(2026, 6, 1, 9, 20, tzinfo=JST)
        trades = [
            {
                "day": "2026-06-01",
                "symbol": "1111.T",
                "entry_time": et1.isoformat(),
                "exit_time": xt1.isoformat(),
                "exit_reason": "stop_hit",
                "hold_sec": 60.0,
                "pnl_yen_100": -1000.0,
                "early_stop": True,
            },
            {
                "day": "2026-06-01",
                "symbol": "1111.T",
                "entry_time": et2.isoformat(),
                "exit_time": (et2 + timedelta(minutes=2)).isoformat(),
                "exit_reason": "stop_hit",
                "hold_sec": 120.0,
                "pnl_yen_100": -800.0,
                "early_stop": True,
            },
        ]
        rows, summary = _analyze_churn(trades)
        self.assertEqual(len(rows), 1)
        self.assertEqual(summary["same_symbol_reentry_after_stop_30m_count"], 1)
        self.assertTrue(summary["reentry_after_stop_is_loss_source"])

    def test_early_stop_summary(self) -> None:
        trades = [
            {"exit_reason": "stop_hit", "hold_sec": 60.0, "pnl_yen_100": -100, "early_stop": True},
            {"exit_reason": "trailing_mfe_exit", "hold_sec": 600.0, "pnl_yen_100": 200, "early_stop": False},
        ]
        s = _early_stop_summary(trades)
        self.assertEqual(s["early_stop_count"], 1)
        self.assertEqual(s["stop_hit_count"], 1)


def test_phase671_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    report = run_audit()
    assert report["entry_count"] == 3192
    assert report["verdict"] in {
        "FOUND_SIGNAL",
        "FOUND_CHURN_BUG",
        "HOLD",
        "REJECT",
    }
    assert (root / "results" / "reports" / "phase671_early_stop" / "phase671_early_stop_report.json").is_file()


if __name__ == "__main__":
    unittest.main()
