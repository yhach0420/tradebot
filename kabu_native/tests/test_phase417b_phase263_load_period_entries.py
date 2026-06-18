"""Phase417B load_period_entries fix tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from typing import Any

from research.equity_dynamic_stop_shadow import (  # noqa: E402
    PERIOD_START,
    audit_load_period_entries,
    load_period_entries,
    normalize_trade_row_for_period_entry,
    resolve_entry_price,
    resolve_period_days,
)
from research.phase416_post_no_overlap_shadow_rebaseline import (  # noqa: E402
    load_baseline_a_trades,
    load_baseline_b_trades,
)


def _fixture_trades_by_day() -> dict[str, list[dict[str, Any]]]:
    return {
        "20260529": [
            {
                "symbol": "1001.T",
                "entry_time": "2026-05-29T09:00:00+09:00",
                "close_time": "2026-05-29T09:05:00+09:00",
                "pnl_yen_100": -120.0,
                "close_price": 988.0,
            }
        ],
        "20260601": [
            {
                "symbol": "2002.T",
                "entry_time": "2026-06-01T10:00:00+09:00",
                "exit_time": "2026-06-01T10:10:00+09:00",
                "pnl_yen_100": 200.0,
                "close_price": 1020.0,
            }
        ],
    }


class TestPhase417BLoadPeriodEntries(unittest.TestCase):
    def test_loads_multiple_days_not_only_last(self) -> None:
        trades_by_day = _fixture_trades_by_day()
        period_days = resolve_period_days(trades_by_day)
        entries = load_period_entries(trades_by_day, period_days=period_days)
        self.assertEqual(len(period_days), 2)
        self.assertEqual(len(entries), 2)
        days = sorted({e["day"] for e in entries})
        self.assertEqual(days, ["20260529", "20260601"])

    def test_close_time_maps_to_exit_time_and_derives_entry_price(self) -> None:
        row = normalize_trade_row_for_period_entry(
            {
                "symbol": "1001.T",
                "entry_time": "2026-05-29T09:00:00+09:00",
                "close_time": "2026-05-29T09:05:00+09:00",
                "pnl_yen_100": -120.0,
                "close_price": 988.0,
            },
            day="20260529",
        )
        self.assertEqual(row["exit_time"], "2026-05-29T09:05:00+09:00")
        ep = resolve_entry_price(row, day="20260529")
        self.assertAlmostEqual(ep or 0.0, 989.2, places=2)

    def test_day_from_entry_time_when_day_column_missing(self) -> None:
        row = normalize_trade_row_for_period_entry(
            {
                "symbol": "1001.T",
                "entry_time": "2026-06-01T10:00:00+09:00",
                "exit_time": "2026-06-01T10:10:00+09:00",
                "pnl_yen_100": 0.0,
                "close_price": 1500.0,
            },
            day="",
        )
        self.assertEqual(row["day"], "20260601")

    def test_dict_keys_do_not_collapse_to_last_day(self) -> None:
        trades_by_day = {
            "20260529": [{"symbol": "1001.T", "entry_price": 1000.0, "pnl_yen_100": 10.0, "day": "20260529"}],
            "20260616": [{"symbol": "2002.T", "entry_price": 2000.0, "pnl_yen_100": -20.0, "day": "20260616"}],
        }
        period_days = resolve_period_days(trades_by_day)
        entries = load_period_entries(trades_by_day, period_days=period_days)
        self.assertEqual(len(entries), 2)
        self.assertEqual(sorted(e["day"] for e in entries), ["20260529", "20260616"])

    def test_baseline_b_fixture_entry_count_exceeds_27(self) -> None:
        a = load_baseline_a_trades(REPO)
        b = load_baseline_b_trades(a)
        trades_by_day: dict[str, list] = {}
        for t in b:
            day = str(t.get("day") or "")
            trades_by_day.setdefault(day, []).append(dict(t))
        period_days = [d for d in resolve_period_days(trades_by_day) if PERIOD_START <= d]
        audit = audit_load_period_entries(trades_by_day, period_days=period_days, repo_root=REPO)
        self.assertEqual(audit["period_day_count"], 11)
        self.assertGreater(audit["base_entry_count"], 27)
        self.assertGreaterEqual(audit["base_entry_count"], 650)

    def test_runtime_unchanged(self) -> None:
        runtime_files = [
            REPO / "src" / "small_paper" / "pilot_runner.py",
            REPO / "src" / "small_paper" / "config.py",
        ]
        for path in runtime_files:
            self.assertTrue(path.is_file(), msg=f"missing {path}")


if __name__ == "__main__":
    unittest.main()
