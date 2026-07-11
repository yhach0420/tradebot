"""Phase658 full-period shadow revalidation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase658_full_period_shadow_revalidation import (  # noqa: E402
    PHASE658_VERDICT,
    ShadowEval,
    _eval_entry_block,
    _revised_decision,
    _bool_val,
    EvalContext,
    run_phase658,
)
from research.phase632_pbv2_profit_filter_counterfactual import _metrics  # noqa: E402
from research.phase649_flat_band_guard_counterfactual import block_flat_plus_overheat  # noqa: E402


class Phase658RevalidationTests(unittest.TestCase):
    def test_bool_val(self) -> None:
        self.assertTrue(_bool_val("true"))
        self.assertFalse(_bool_val(""))

    def test_entry_block_synthetic(self) -> None:
        trades = [
            {"day": "2026-06-20", "session": "s1", "symbol": "A", "entry_time": "t1", "entry_pool": "PBV2", "pnl_yen_100": -1000.0},
            {"day": "2026-06-20", "session": "s1", "symbol": "B", "entry_time": "t2", "entry_pool": "PBV2", "pnl_yen_100": 500.0},
        ]
        ctx = EvalContext(
            trades=trades,
            sessions=[{"session": "s1", "day": "2026-06-20"}],
            session_dirs={},
            baseline=_metrics(trades),
            summaries=[],
            summary_by_session={},
            shadow_defs={},
        )
        ev = _eval_entry_block(
            ctx,
            "test_shadow",
            category="entry_runtime",
            block_fn=lambda t: str(t.get("symbol")) == "A",
            pool="PBV2_ONLY",
        )
        self.assertEqual(ev.delta_pnl_yen, 1000.0)
        self.assertEqual(ev.rescued_losers, 1)

    def test_revised_adopt_threshold(self) -> None:
        ev = ShadowEval(shadow_id="pbv2_flat_band_shadow", evaluable=True, delta_pnl_yen=60000, blocked_winners=10)
        self.assertEqual(_revised_decision(ev), "ADOPT")

    def test_run_on_repo_when_data_present(self) -> None:
        if not (NATIVE / "results" / "small_paper").is_dir():
            self.skipTest("session data not present")
        result = run_phase658(repo_root=NATIVE, skip_slow=True)
        self.assertEqual(result["verdict"], PHASE658_VERDICT)
        m = result["mandatory_answers"]
        self.assertIn("1_top10_improved_shadows", m)
        self.assertIn("10_flat_band_mainline_candidate", m)
        self.assertGreater(len(result.get("evaluations") or []), 10)


if __name__ == "__main__":
    unittest.main()
