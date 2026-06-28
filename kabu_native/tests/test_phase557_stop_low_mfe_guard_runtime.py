"""Phase557: stop_low_mfe guard (G554_022) runtime adoption tests."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
for p in (REPO, KABU / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.exposure_gate import (  # noqa: E402
    ExposureGate,
    ExposureGateConfig,
    REJECT_ENTRY_CLUSTER_GUARD,
    REJECT_STOP_LOW_MFE_GUARD,
)
from small_paper.config import SmallPaperPilotConfig, load_pilot_config  # noqa: E402
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402
from small_paper.entry_cluster_classifier import EntryClusterModel  # noqa: E402
from small_paper.entry_cluster_guard import (  # noqa: E402
    CLUSTER_GUARD_REJECTED,
    EntryClusterGuardConfig,
    EntryClusterGuardState,
    load_default_model,
)
from small_paper.or_overlay_entry import (  # noqa: E402
    OrOverlayConfig,
    OrOverlaySessionState,
    evaluate_or_overlay_entry,
)
from small_paper.pilot_runner import _stop_low_mfe_guard_summary_fields  # noqa: E402
from small_paper.production_startup_smoke_test import run_production_startup_smoke_test  # noqa: E402
from small_paper.stop_low_mfe_guard import (  # noqa: E402
    DEFAULT_THRESHOLD,
    PHASE557_RUNTIME_VERDICT,
    StopLowMfeGuardConfig,
    StopLowMfeGuardState,
    build_stop_low_mfe_guard_state,
    config_from_pilot,
    volume_acceleration_5m,
)
from storage.intraday_recorder import PushMinuteBarBuilder  # noqa: E402


def _base_trade(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "5074.T",
        "entry_time": "2026-06-24T10:15:00+09:00",
        "trade_date": "2026-06-24",
        "continuation_quality_score": 0.72,
        "momentum_continuation_score": 0.22,
        "entry_expectancy_score_v2": 5,
        "entry_order_book_imbalance": 0.55,
        "board_update_frequency": 0.01,
        "relative_volume": 2.0,
    }
    base.update(overrides)
    return base


def _slm_guard(*, enabled: bool = True, threshold: float = DEFAULT_THRESHOLD) -> StopLowMfeGuardState:
    return StopLowMfeGuardState(
        config=StopLowMfeGuardConfig(
            enabled=enabled,
            threshold=threshold,
            missing_policy="pass",
            pbv2_only=True,
        )
    )


def _cluster_guard() -> EntryClusterGuardState:
    model = load_default_model(repo_root=KABU)
    return EntryClusterGuardState(
        config=EntryClusterGuardConfig(enabled=True, exception_enabled=True),
        model=model,
    )


def _gate(
    *,
    slm: StopLowMfeGuardState | None = None,
    cluster: EntryClusterGuardState | None = None,
) -> ExposureGate:
    return ExposureGate(
        ExposureGateConfig(
            profile="momentum_volume_v13_combined",
            min_continuation_quality=0.55,
            reject_below_quality=False,
            max_concurrent_positions=10,
            entry_score_v2_min=3,
            momentum_score_cutoff_max=0.2546,
        ),
        entry_cluster_guard=cluster,
        stop_low_mfe_guard=slm,
    )


class _FakeObserver:
    def open_count(self) -> int:
        return 0

    def has_open(self, symbol: str) -> bool:
        return False

    def open_count_by_entry_type(self) -> tuple[int, int]:
        return 0, 0

    def open_positions(self) -> list[dict[str, str]]:
        return []


class TestStopLowMfeGuardFeature(unittest.TestCase):
    def test_volume_acceleration_5m_from_volumes(self) -> None:
        volumes = [100.0] * 9 + [200.0]
        accel = volume_acceleration_5m(volumes)
        self.assertIsNotNone(accel)
        self.assertGreater(accel, 0.0)

    def test_volume_acceleration_insufficient_bars_missing(self) -> None:
        self.assertIsNone(volume_acceleration_5m([1.0, 2.0, 3.0]))

    def test_push_minute_bar_builder_snapshot(self) -> None:
        builder = PushMinuteBarBuilder()
        now = datetime(2026, 6, 24, 10, 15, tzinfo=ZoneInfo("Asia/Tokyo"))
        for i in range(12):
            ts = now.replace(minute=9 + i)
            builder.ingest_push_payload(
                {"CurrentPrice": 1000 + i, "TradingVolume": 1000 * (i + 1)},
                recorded_at=ts,
            )
        vols = builder.snapshot_minute_volumes()
        self.assertGreaterEqual(len(vols), 10)
        self.assertIsNotNone(volume_acceleration_5m(vols))

    def test_no_lookahead_uses_completed_minutes_only(self) -> None:
        guard = _slm_guard()
        trade = _base_trade()
        builder = guard._builders.setdefault("5074.T", PushMinuteBarBuilder())
        now = datetime(2026, 6, 24, 10, 15, tzinfo=ZoneInfo("Asia/Tokyo"))
        for i in range(10):
            builder.ingest_push_payload(
                {"CurrentPrice": 1000, "TradingVolume": 100 + i * 50},
                recorded_at=now.replace(minute=5 + i),
            )
        accel = guard.compute_volume_acceleration(trade)
        self.assertIsNotNone(accel)


class TestStopLowMfeGuardCore(unittest.TestCase):
    def test_disabled_passes(self) -> None:
        guard = _slm_guard(enabled=False)
        self.assertFalse(guard.check(_base_trade()).blocked)

    def test_missing_passes(self) -> None:
        guard = _slm_guard()
        result = guard.check(_base_trade())
        self.assertFalse(result.blocked)
        self.assertTrue(result.missing)
        self.assertEqual(guard.missing_count, 1)

    def test_threshold_reject(self) -> None:
        guard = _slm_guard()
        trade = _base_trade(volume_acceleration_5m=0.02)
        result = guard.check(trade)
        self.assertTrue(result.blocked)
        self.assertAlmostEqual(result.volume_acceleration_5m, 0.02)

    def test_threshold_pass(self) -> None:
        guard = _slm_guard()
        trade = _base_trade(volume_acceleration_5m=0.005)
        result = guard.check(trade)
        self.assertFalse(result.blocked)

    def test_pbv2_only_or_skipped(self) -> None:
        guard = _slm_guard()
        trade = _base_trade(entry_type="OR_OVERLAY", volume_acceleration_5m=0.5)
        self.assertFalse(guard.check(trade).blocked)

    def test_rollback_enabled_false(self) -> None:
        config = SmallPaperPilotConfig(stop_low_mfe_guard_enabled=False)
        self.assertIsNone(build_stop_low_mfe_guard_state(config))
        gate = config.make_exposure_gate(repo_root=KABU)
        self.assertIsNone(getattr(gate, "stop_low_mfe_guard", None))


class TestStopLowMfeGuardExposureGate(unittest.TestCase):
    def test_exposure_gate_rejects_with_reason(self) -> None:
        guard = _slm_guard()
        decision = _gate(slm=guard).evaluate_entry(
            _base_trade(volume_acceleration_5m=0.02)
        )
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_STOP_LOW_MFE_GUARD)

    def test_cluster_guard_runs_before_slm(self) -> None:
        slm = _slm_guard()
        cluster = _cluster_guard()
        gate = _gate(slm=slm, cluster=cluster)
        with patch.object(
            EntryClusterModel,
            "classify",
            return_value={
                "cluster_id": 5,
                "subcluster_id": -1,
                "new_subcluster_id": -1,
                "liquidity_burst": 0.01,
                "cluster_reject_candidate": True,
            },
        ):
            decision = gate.evaluate_entry(_base_trade(volume_acceleration_5m=0.02))
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_ENTRY_CLUSTER_GUARD)
        self.assertEqual(slm.reject_count, 0)
        self.assertEqual(cluster.reject_count, 1)

    def test_slm_blocks_when_cluster_passes(self) -> None:
        slm = _slm_guard()
        cluster = _cluster_guard()
        gate = _gate(slm=slm, cluster=cluster)
        with patch.object(
            EntryClusterModel,
            "classify",
            return_value={
                "cluster_id": 1,
                "subcluster_id": -1,
                "new_subcluster_id": -1,
                "liquidity_burst": 0.01,
                "cluster_reject_candidate": False,
            },
        ):
            decision = gate.evaluate_entry(_base_trade(volume_acceleration_5m=0.02))
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_STOP_LOW_MFE_GUARD)


class TestStopLowMfeGuardOrNonImpact(unittest.TestCase):
    def test_or_overlay_unaffected(self) -> None:
        slm = _slm_guard()
        gate = _gate(slm=slm)
        or_st = OrOverlaySessionState(
            config=OrOverlayConfig(enabled=True, cap_pbv2=4, cap_or=1),
            day_return_by_symbol={"5074.T": 6.0},
        )
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "5074.T",
            "entry_time": "2026-06-24T10:15:00+09:00",
            "trade_date": "2026-06-24",
            "continuation_quality_score": 0.5,
            "entry_near_day_high_pct": 0.05,
            "update_count_before_entry": 3,
            "entry_vwap_dev_pct": 0.4,
        }
        payload = {"CurrentPrice": 1000, "HighPrice": 1000}
        decision = evaluate_or_overlay_entry(
            gate=gate,
            trade=dict(trade),
            payload=payload,
            price_ring=[],
            entry_ts=1_750_000_000.0,
            observer=_FakeObserver(),
            or_state=or_st,
        )
        self.assertTrue(decision.accept)
        self.assertEqual(slm.reject_count, 0)


class TestStopLowMfeGuardSummaryAndDiscord(unittest.TestCase):
    def test_summary_fields_keys(self) -> None:
        guard = _slm_guard()
        keys = {
            "stop_low_mfe_guard_reject_count",
            "stop_low_mfe_guard_missing_count",
            "stop_low_mfe_guard_blocked_loss",
            "stop_low_mfe_guard_blocked_winner",
            "stop_low_mfe_guard_blocked_big_winner",
            "stop_low_mfe_guard_net_shadow",
            "stop_low_mfe_guard_volume_accel_threshold",
        }
        self.assertTrue(keys.issubset(guard.summary_fields().keys()))

    def test_pilot_runner_summary_wrapper(self) -> None:
        from small_paper.pilot_runner import _LiveRunState

        guard = _slm_guard()
        gate = _gate(slm=guard)
        state = _LiveRunState(started_mono=0.0)
        fields = _stop_low_mfe_guard_summary_fields(gate, state)
        self.assertTrue(fields["stop_low_mfe_guard_enabled"])

    def test_discord_summary_line(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "stop_low_mfe_guard_enabled": True,
                "stop_low_mfe_guard_reject_count": 3,
                "stop_low_mfe_guard_missing_count": 1,
                "stop_low_mfe_guard_net_shadow": 1200.0,
            }
        )
        joined = "\n".join(lines)
        self.assertIn("StopLowMFEGuard: reject=3 missing=1 net_shadow=1200", joined)


class TestStopLowMfeGuardConfig(unittest.TestCase):
    def test_pilot_config_loads_guard(self) -> None:
        cfg_path = (
            KABU
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        self.assertTrue(config.stop_low_mfe_guard_enabled)
        self.assertAlmostEqual(config.stop_low_mfe_guard_threshold, 0.009)
        self.assertEqual(config.stop_low_mfe_guard_missing_policy, "pass")
        self.assertTrue(config.stop_low_mfe_guard_pbv2_only)

    def test_make_exposure_gate_attaches_guard(self) -> None:
        cfg_path = (
            KABU
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        gate = config.make_exposure_gate(repo_root=KABU)
        self.assertIsNotNone(getattr(gate, "stop_low_mfe_guard", None))

    def test_config_from_pilot(self) -> None:
        config = SmallPaperPilotConfig(
            stop_low_mfe_guard_enabled=True,
            stop_low_mfe_guard_threshold=0.009,
            stop_low_mfe_guard_missing_policy="pass",
            stop_low_mfe_guard_pbv2_only=True,
        )
        cfg = config_from_pilot(config)
        self.assertTrue(cfg.enabled)
        self.assertAlmostEqual(cfg.threshold, 0.009)


class TestStopLowMfeGuardSession(unittest.TestCase):
    def test_reset_session_clears_builders(self) -> None:
        guard = _slm_guard()
        guard.ingest_push("5074.T", {"CurrentPrice": 1000, "TradingVolume": 100})
        self.assertIn("5074.T", guard._builders)
        guard.reset_session()
        self.assertEqual(guard._builders, {})

    def test_am_pm_separate_builders(self) -> None:
        am = _slm_guard()
        pm = _slm_guard()
        am.ingest_push("5074.T", {"CurrentPrice": 1000, "TradingVolume": 100})
        self.assertNotIn("5074.T", pm._builders)


class TestPhase557Verdict(unittest.TestCase):
    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE557_RUNTIME_VERDICT, "phase557_stop_low_mfe_guard_runtime_ready")

    def test_production_startup_smoke_test(self) -> None:
        smoke = run_production_startup_smoke_test(repo_root=REPO)
        self.assertTrue(smoke.checks.get("stop_low_mfe_guard"))
        self.assertTrue(smoke.checks.get("stop_low_mfe_guard_summary"))


if __name__ == "__main__":
    unittest.main()
