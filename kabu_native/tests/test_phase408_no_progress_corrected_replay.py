"""Phase408 corrected replay tests."""

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

from research.phase404_no_progress_exit_shadow import (  # noqa: E402
    NoProgressPolicySpec,
    build_tick_states,
    no_progress_matches,
)
from research.phase408_no_progress_corrected_replay import (  # noqa: E402
    PHASE407A_CAPPED_NET_DELTA,
    PHASE407A_CAPPED_TOLERANCE_YEN,
    cap_price_series,
    prepare_corrected_trade_context,
    run_phase408_corrected_replay,
    simulate_corrected_no_progress,
    with_baseline_fallback,
)


class TestPhase408CorrectedReplay(unittest.TestCase):
    def test_cap_series_excludes_post_baseline_ticks(self) -> None:
        cap = 1000.0
        series = [(900.0, 100.0), (1000.0, 101.0), (1100.0, 102.0)]
        capped = cap_price_series(series, cap)
        self.assertEqual(len(capped), 2)
        self.assertTrue(all(ts <= cap for ts, _ in capped))

    def test_baseline_fallback_when_no_shadow_trigger(self) -> None:
        ctx = {
            "baseline_cap_ts": 2000.0,
            "baseline_pnl_yen_100": 500.0,
            "baseline_exit_reason": "trailing_mfe",
        }
        sim = {
            "shadow_exit_reason": "session_close",
            "shadow_exit_ts": 2000.0,
            "shadow_pnl_yen_100": 300.0,
        }
        out = with_baseline_fallback(ctx, sim)
        self.assertTrue(out.get("used_baseline_fallback"))
        self.assertEqual(out["shadow_pnl_yen_100"], 500.0)

    def test_no_progress_requires_elapsed_on_capped_path(self) -> None:
        policy = NoProgressPolicySpec(900.0, 0.8, 0.2, "none", "none")
        entry_ts = 1_000_000.0
        states = build_tick_states(
            [(entry_ts + 100, 1000.0)],
            entry_ts=entry_ts,
            entry_price=1000.0,
            session_end_ts=entry_ts + 500,
            entry_vwap_dev_pct=None,
        )
        early = states[0]
        self.assertFalse(no_progress_matches(early, policy))

    def test_run_phase408(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        result = run_phase408_corrected_replay(
            repo_root=REPO,
            trades_path=src,
            output_dir=REPO / "results" / "reports",
        )
        summary = result["summary"]
        audit = summary.get("replay_audit") or {}
        self.assertEqual(audit.get("post_baseline_usage_count"), 0)
        self.assertEqual(audit.get("status"), "PASS")
        self.assertTrue(summary.get("portfolio_ranking"))
        ma = summary.get("mandatory_answers") or {}
        p404_delta = ma.get("phase404_best_policy_corrected_net_delta_yen")
        if p404_delta is not None:
            self.assertLessEqual(
                abs(float(p404_delta) - PHASE407A_CAPPED_NET_DELTA),
                PHASE407A_CAPPED_TOLERANCE_YEN + 15000.0,
            )

    def test_corrected_sim_respects_cap(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        from research.phase400_holding_time_audit import enrich_trade, load_phase399_trades

        raw = load_phase399_trades(src)
        trade = enrich_trade(next(r for r in raw if str(r.get("position_cap_accepted", "")).lower() in ("true", "1")))
        ctx = prepare_corrected_trade_context(
            trade,
            repo_root=REPO,
            session_cache={},
            p90_hold=1290.6,
        )
        if ctx is None:
            self.skipTest("context unavailable")
        policy = NoProgressPolicySpec(900.0, 0.8, 0.2, "none", "none")
        sim = simulate_corrected_no_progress(ctx, policy=policy)
        cap = float(ctx["baseline_cap_ts"])
        self.assertLessEqual(float(sim["shadow_exit_ts"]), cap + 1e-6)


if __name__ == "__main__":
    unittest.main()
