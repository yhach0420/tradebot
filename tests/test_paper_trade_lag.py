"""paper_trade 遅延ガード・設定マージのユニットテスト（標準 unittest）。"""
from __future__ import annotations

import unittest

import yahoo_kabu_watch as ykw


class PaperTradeLagTests(unittest.TestCase):
    def test_merge_defaults(self) -> None:
        d = ykw._paper_trade_merge_runtime_controls({}, None)
        self.assertTrue(d.get("lag_guard_enabled"))
        self.assertEqual(float(d.get("max_signal_notify_lag_sec") or 0), 120.0)
        self.assertFalse(d.get("fetch_timeouts_enabled"))
        self.assertTrue(d.get("candidate_state_notify_enabled"))
        self.assertEqual(float(d.get("price_change_notify_threshold_pct") or 0), 0.5)
        self.assertEqual(float(d.get("symbol_notify_cooldown_sec") or 0), 180.0)

    def test_cli_overrides_file(self) -> None:
        file_cfg = {"paper_trade": {"max_signal_notify_lag_sec": 90.0, "lag_guard_enabled": True}}
        cli = {"max_signal_notify_lag_sec": 60.0, "lag_guard_enabled": False}
        d = ykw._paper_trade_merge_runtime_controls(file_cfg, cli)
        self.assertEqual(float(d["max_signal_notify_lag_sec"]), 60.0)
        self.assertFalse(d["lag_guard_enabled"])

    def test_hhmm_parse(self) -> None:
        self.assertEqual(ykw._paper_trade_hhmm_to_minutes("09:10"), 9 * 60 + 10)
        self.assertIsNone(ykw._paper_trade_hhmm_to_minutes(""))


if __name__ == "__main__":
    unittest.main()
