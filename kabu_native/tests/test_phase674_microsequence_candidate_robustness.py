"""Phase674 — Microsequence candidate robustness tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase674_microsequence_candidate_robustness import (  # noqa: E402
    _build_candidate_rules,
    _eval_slice,
    _parse_third_operator,
    _rule_b,
    run_audit,
)


class TestPhase674Helpers(unittest.TestCase):
    def test_candidate_rules_include_a_through_f(self) -> None:
        rules = _build_candidate_rules()
        ids = {r[0] for r in rules}
        self.assertTrue({"A", "B", "C", "D", "E", "F"}.issubset(ids))

    def test_parse_operator_le(self) -> None:
        pred = _parse_third_operator("high_update_failure_count<=11")
        t = {"high_update_failure_count": 10}
        self.assertTrue(pred(t))
        self.assertFalse(pred({"high_update_failure_count": 12}))

    def test_eval_slice_metrics(self) -> None:
        trades = [
            {
                "day": "2026-06-01",
                "symbol": "1111.T",
                "entry_time": "2026-06-01T09:10:00+09:00",
                "pnl_yen_100": -1000.0,
                "bounce_from_recent_low": 0.3,
                "fall_from_recent_high": -0.2,
                "high_update_failure_count": 8,
                "early_stop": True,
                "microsequence_ok": True,
                "post_flat_band_entry": True,
            },
            {
                "day": "2026-06-01",
                "symbol": "2222.T",
                "entry_time": "2026-06-01T09:20:00+09:00",
                "pnl_yen_100": 6000.0,
                "bounce_from_recent_low": 0.05,
                "fall_from_recent_high": -0.05,
                "early_stop": False,
                "microsequence_ok": True,
                "post_flat_band_entry": True,
            },
        ]
        row = _eval_slice(trades, rule_id="B", rule_label="test", slice_id="t", block_pred=_rule_b)
        self.assertEqual(row["blocked_count"], 1)
        self.assertEqual(row["blocked_early_stop"], 1)


def test_phase674_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper missing")
    if not (root / "data" / "push_jsonl").is_dir():
        pytest.skip("push_jsonl missing")
    report = run_audit()
    assert report["verdict"] in {"SHADOW_CANDIDATE", "HOLD", "REJECT", "DATA_GAP"}
    out = root / "results" / "reports" / "phase674_microsequence_candidate_robustness"
    assert (out / "phase674_microsequence_candidate_report.json").is_file()


if __name__ == "__main__":
    unittest.main()
