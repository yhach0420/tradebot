"""Phase411 same-symbol reentry shadow tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase409_boundary_forward_shadow import FORWARD_PERIOD_START, load_structural_trades_for_day  # noqa: E402
from research.phase410_duplicate_reentry_audit import apply_counterfactual_policy  # noqa: E402
from research.phase411_same_symbol_reentry_shadow import (  # noqa: E402
    SameSymbolReentryShadowLogger,
    build_shadow_trade_rows,
    run_same_symbol_reentry_shadow,
)
from research.structural_trade_normalize import (  # noqa: E402
    normalize_structural_trade_row,
    resolve_kabu_root,
    resolve_reports_dir,
)


class TestPhase411SameSymbolReentryShadow(unittest.TestCase):
    def test_close_time_maps_to_exit_time(self) -> None:
        row = normalize_structural_trade_row(
            {
                "symbol": "9984",
                "entry_time": "2026-06-16T09:00:00+09:00",
                "close_time": "2026-06-16T09:15:00+09:00",
                "entry_price": 10000,
                "close_price": 10100,
            },
            day=FORWARD_PERIOD_START,
            session="live_session_test",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["exit_time"], "2026-06-16T09:15:00+09:00")
        self.assertEqual(row["symbol"], "9984.T")

    def test_resolve_kabu_root_avoids_double_nested_path(self) -> None:
        self.assertEqual(resolve_kabu_root(REPO), REPO.resolve())
        reports = resolve_reports_dir(REPO)
        self.assertTrue(str(reports).endswith("results/reports") or str(reports).endswith("results\\reports"))

    def test_same_symbol_open_reentry_reject_keeps_first_position(self) -> None:
        trades = [
            {
                "symbol": "9984.T",
                "entry_time": "2026-06-16T09:00:00+09:00",
                "exit_time": "2026-06-16T09:05:00+09:00",
                "exit_reason": "trailing_mfe",
                "pnl_yen_100": 100,
                "hold_sec": 300,
            },
            {
                "symbol": "9984.T",
                "entry_time": "2026-06-16T09:01:00+09:00",
                "exit_time": "2026-06-16T09:02:00+09:00",
                "exit_reason": "overlap_replaced_review",
                "pnl_yen_100": -50,
                "hold_sec": 60,
            },
        ]
        kept = apply_counterfactual_policy(trades, policy="same_symbol_open_reentry_reject")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["entry_time"], "2026-06-16T09:00:00+09:00")
        self.assertNotEqual(kept[0].get("exit_reason"), "overlap_replaced_review")

    def test_shadow_trade_rows_mark_rejected(self) -> None:
        baseline = [
            {"session": "s1", "symbol": "9984.T", "entry_time": "t1", "exit_time": "t2", "pnl_yen_100": 10},
            {"session": "s1", "symbol": "7203.T", "entry_time": "t3", "exit_time": "t4", "pnl_yen_100": 20},
        ]
        shadow = [baseline[0]]
        rows = build_shadow_trade_rows(baseline, shadow, day=FORWARD_PERIOD_START, logged_at="now")
        self.assertEqual(len(rows), 2)
        rejected = [r for r in rows if not r["shadow_included"]]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["symbol"], "7203.T")

    @patch("research.phase411_same_symbol_reentry_shadow.load_structural_trades_for_day")
    def test_logger_writes_outputs(self, mock_load) -> None:
        mock_load.return_value = [
            {
                "symbol": "9984.T",
                "entry_time": "2026-06-16T09:00:00+09:00",
                "exit_time": "2026-06-16T09:30:00+09:00",
                "exit_reason": "trailing_mfe",
                "pnl_yen_100": 100,
                "pnl_yen_100_float": 100.0,
                "hold_sec": 1800,
                "day": FORWARD_PERIOD_START,
                "session": "live_session_test",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            result = run_same_symbol_reentry_shadow(
                repo_root=REPO,
                reports_dir=reports,
                day=FORWARD_PERIOD_START,
            )
            job = SameSymbolReentryShadowLogger(repo_root=REPO, reports_dir=reports)
            paths = job.write_outputs(result)
            payload = json.loads(paths["summary"].read_text(encoding="utf-8"))
            self.assertEqual(payload["forward_summary"]["day_count"], 1)
            self.assertEqual(payload["forward_summary"]["shadow_trade_count"], 1)

    def test_616_fixture_matches_phase410_counterfactual(self) -> None:
        from research.phase410_duplicate_reentry_audit import (
            AUDIT_DAY,
            AM_SESSION,
            PM_SESSION,
            apply_counterfactual_policy,
            load_session_trades,
            _resolve_kabu_root,
        )

        kabu = _resolve_kabu_root(REPO)
        p410 = load_session_trades(
            kabu / "results" / "small_paper" / AUDIT_DAY / AM_SESSION, day=AUDIT_DAY
        ) + load_session_trades(
            kabu / "results" / "small_paper" / AUDIT_DAY / PM_SESSION, day=AUDIT_DAY
        )
        if not p410:
            self.skipTest("no 20260616 fixture")
        p409 = load_structural_trades_for_day(REPO, AUDIT_DAY)
        kept410 = apply_counterfactual_policy(p410, policy="same_symbol_open_reentry_reject")
        kept409 = apply_counterfactual_policy(p409, policy="same_symbol_open_reentry_reject")
        self.assertEqual(len(p409), len(p410))
        self.assertEqual(len(kept409), len(kept410))
        pnl410 = round(sum(float(t.get("pnl_yen_100_float") or 0) for t in kept410), 2)
        pnl409 = round(sum(float(t.get("pnl_yen_100_float") or 0) for t in kept409), 2)
        self.assertEqual(pnl409, pnl410)
        self.assertEqual(len(kept409), 394)

    def test_runtime_artifacts_unchanged(self) -> None:
        from small_paper import pilot_runner

        src = Path(pilot_runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("same_symbol_open_reentry_reject", src)
        self.assertNotIn("phase411", src)


if __name__ == "__main__":
    unittest.main()
