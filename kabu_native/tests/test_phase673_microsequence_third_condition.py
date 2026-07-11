"""Phase673 — Third condition search tests."""

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

from research.phase673_microsequence_third_condition import (  # noqa: E402
    _base_combo,
    _eval_third_rule,
    _score_rule,
    run_audit,
)

BASE_BOUNCE = 0.2182
BASE_FALL = -0.1735


def _trade(**kwargs: object) -> dict:
    base = {
        "day": "2026-06-01",
        "symbol": "1111.T",
        "entry_time": "2026-06-01T09:10:00+09:00",
        "pnl_yen_100": -1000.0,
        "microsequence_ok": True,
        "bounce_from_recent_low": 0.3,
        "fall_from_recent_high": -0.2,
        "early_stop": True,
        "post_flat_band_entry": True,
    }
    base.update(kwargs)
    return base


class TestThirdConditionHelpers(unittest.TestCase):
    def test_base_combo_match(self) -> None:
        t = _trade()
        self.assertTrue(_base_combo(t))
        self.assertFalse(_base_combo(_trade(bounce_from_recent_low=0.1)))

    def test_third_rule_blocks_subset(self) -> None:
        trades = [
            _trade(symbol="1111.T", pnl_yen_100=-1000.0, down_tick_ratio=0.7),
            _trade(symbol="2222.T", pnl_yen_100=5000.0, down_tick_ratio=0.4),
            _trade(symbol="3333.T", pnl_yen_100=200.0, bounce_from_recent_low=0.05),
        ]
        base = _eval_third_rule(
            trades,
            rule_id="base_only",
            third_pred=lambda t: True,
            baseline_early_stop=1,
        )
        refined = _eval_third_rule(
            trades,
            rule_id="base_plus_down",
            third_pred=lambda t: (_num := float(t.get("down_tick_ratio") or 0)) >= 0.6,
            baseline_early_stop=1,
        )
        self.assertEqual(base["blocked_count"], 2)
        self.assertEqual(refined["blocked_count"], 1)
        self.assertLess(refined["blocked_winners"], base["blocked_winners"])


def test_phase673_audit_on_canonical_dataset():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper").is_dir():
        pytest.skip("small_paper results missing")
    if not (root / "data" / "push_jsonl").is_dir():
        pytest.skip("push_jsonl missing")
    report = run_audit()
    assert report["entry_count"] == 3192
    assert report["verdict"] in {"FOUND_CANDIDATE", "HOLD", "REJECT", "DATA_GAP"}
    out = root / "results" / "reports" / "phase673_microsequence_third_condition"
    assert (out / "phase673_third_condition_report.json").is_file()
    assert (out / "phase673_decision.md").is_file()


if __name__ == "__main__":
    unittest.main()
