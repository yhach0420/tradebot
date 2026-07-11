"""Phase549: V6 Balanced Reject + E4 Liquidity Burst runtime adoption tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
)
from small_paper.config import SmallPaperPilotConfig, load_pilot_config  # noqa: E402
from small_paper.discord_message_builder import build_entry_detail  # noqa: E402
from small_paper.entry_cluster_classifier import (  # noqa: E402
    EntryClusterModel,
    load_default_model,
    resolve_entry_cluster_guard_model_path,
)
from small_paper.entry_cluster_guard import (  # noqa: E402
    CLUSTER_GUARD_EXCEPTION,
    CLUSTER_GUARD_PASSED,
    CLUSTER_GUARD_REJECTED,
    DEFAULT_LIQUIDITY_BURST_THRESHOLD,
    EntryClusterGuardConfig,
    EntryClusterGuardState,
    PHASE549_RUNTIME_VERDICT,
    PHASE552_SMOKE_VERDICT,
    build_entry_cluster_guard_state,
    config_from_pilot,
    validate_entry_cluster_guard_model,
)
from small_paper.or_overlay_entry import (  # noqa: E402
    OrOverlayConfig,
    OrOverlaySessionState,
    evaluate_or_overlay_entry,
)


def _base_trade(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "5074.T",
        "entry_time": "2026-06-24T10:15:00+09:00",
        "exit_time": "2026-06-24T10:30:00+09:00",
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


def _complete_trade(**overrides: object) -> dict[str, object]:
    """Phase627: reject path requires reject-stage features present (non-null)."""
    model = load_default_model(repo_root=KABU)
    trade = _base_trade(**overrides)
    for feat in model.cluster_features:
        trade.setdefault(feat, 0.1)
    for feat in model.csub_features:
        trade.setdefault(feat, 0.01)
    trade.setdefault("liquidity_burst", 0.01)
    trade.setdefault("entry_score_v2", 5)
    return trade


def _guard(
    *,
    enabled: bool = True,
    exception_enabled: bool = True,
    threshold: float = DEFAULT_LIQUIDITY_BURST_THRESHOLD,
) -> EntryClusterGuardState:
    model = load_default_model(repo_root=KABU)
    return EntryClusterGuardState(
        config=EntryClusterGuardConfig(
            enabled=enabled,
            exception_enabled=exception_enabled,
            liquidity_burst_threshold=threshold,
        ),
        model=model,
    )


def _gate_with_cluster_guard(guard: EntryClusterGuardState) -> ExposureGate:
    return ExposureGate(
        ExposureGateConfig(
            profile="momentum_volume_v13_combined",
            min_continuation_quality=0.55,
            reject_below_quality=False,
            max_concurrent_positions=10,
            entry_score_v2_min=3,
            momentum_score_cutoff_max=0.2546,
        ),
        entry_cluster_guard=guard,
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


class TestEntryClusterGuardCore(unittest.TestCase):
    def test_guard_disabled_passes(self) -> None:
        guard = _guard(enabled=False)
        result = guard.check(_base_trade())
        self.assertFalse(result.blocked)
        self.assertEqual(result.cluster_guard_status, CLUSTER_GUARD_PASSED)

    def test_non_reject_cluster_passes(self) -> None:
        guard = _guard()
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
            result = guard.check(_base_trade())
        self.assertFalse(result.blocked)
        self.assertEqual(result.cluster_guard_status, CLUSTER_GUARD_PASSED)

    def test_reject_cluster5_blocks(self) -> None:
        guard = _guard()
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
            result = guard.check(_complete_trade())
        self.assertTrue(result.blocked)
        self.assertEqual(result.cluster_guard_status, CLUSTER_GUARD_REJECTED)

    def test_reject_csub_blocks(self) -> None:
        guard = _guard()
        with patch.object(
            EntryClusterModel,
            "classify",
            return_value={
                "cluster_id": 3,
                "subcluster_id": 1,
                "new_subcluster_id": 2,
                "liquidity_burst": 0.01,
                "cluster_reject_candidate": True,
            },
        ):
            result = guard.check(_complete_trade())
        self.assertTrue(result.blocked)

    def test_exception_liquidity_burst_passes(self) -> None:
        guard = _guard()
        with patch.object(
            EntryClusterModel,
            "classify",
            return_value={
                "cluster_id": 5,
                "subcluster_id": -1,
                "new_subcluster_id": -1,
                "liquidity_burst": 0.06,
                "cluster_reject_candidate": True,
            },
        ):
            result = guard.check(_complete_trade())
        self.assertFalse(result.blocked)
        self.assertTrue(result.via_exception)
        self.assertEqual(result.cluster_guard_status, CLUSTER_GUARD_EXCEPTION)

    def test_exception_disabled_blocks_high_burst(self) -> None:
        guard = _guard(exception_enabled=False)
        with patch.object(
            EntryClusterModel,
            "classify",
            return_value={
                "cluster_id": 5,
                "subcluster_id": -1,
                "new_subcluster_id": -1,
                "liquidity_burst": 0.10,
                "cluster_reject_candidate": True,
            },
        ):
            result = guard.check(_complete_trade())
        self.assertTrue(result.blocked)

    def test_threshold_boundary(self) -> None:
        guard = _guard(threshold=0.052267)
        with patch.object(
            EntryClusterModel,
            "classify",
            return_value={
                "cluster_id": 5,
                "subcluster_id": -1,
                "new_subcluster_id": -1,
                "liquidity_burst": 0.052267,
                "cluster_reject_candidate": True,
            },
        ):
            result = guard.check(_complete_trade())
        self.assertFalse(result.blocked)
        self.assertTrue(result.via_exception)

    def test_record_reject_increments_counts(self) -> None:
        guard = _guard()
        with patch.object(
            EntryClusterModel,
            "classify",
            return_value={
                "cluster_id": 5,
                "subcluster_id": -1,
                "new_subcluster_id": 3,
                "liquidity_burst": 0.01,
                "cluster_reject_candidate": True,
            },
        ):
            chk = guard.check(_complete_trade())
        guard.record_reject(_complete_trade(), chk)
        self.assertEqual(guard.reject_count, 1)
        self.assertEqual(guard.blocked_cluster_counts.get("c5_s3"), 1)

    def test_record_exit_tracks_exception_metrics(self) -> None:
        guard = _guard()
        guard.record_exit(
            {
                "cluster_guard_status": CLUSTER_GUARD_EXCEPTION,
                "realized_pnl_pct": 1.5,
                "peak_mfe_pct": 1.2,
            }
        )
        guard.record_exit(
            {
                "cluster_guard_status": CLUSTER_GUARD_EXCEPTION,
                "realized_pnl_pct": -0.5,
                "peak_mfe_pct": 0.0,
            }
        )
        summary = guard.summary_fields()
        self.assertEqual(summary["cluster_guard_exception_count"], 0)
        self.assertEqual(summary["cluster_guard_exception_pnl"], 100.0)
        self.assertEqual(summary["cluster_guard_exception_mfe0"], 1)
        self.assertEqual(summary["cluster_guard_exception_big_winner"], 1)


class TestEntryClusterGuardExposureGate(unittest.TestCase):
    def test_exposure_gate_rejects_with_reason(self) -> None:
        guard = _guard()
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
            decision = _gate_with_cluster_guard(guard).evaluate_entry(_complete_trade())
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_ENTRY_CLUSTER_GUARD)
        self.assertEqual(decision.cluster_guard_status, CLUSTER_GUARD_REJECTED)

    def test_exposure_gate_exception_accept(self) -> None:
        guard = _guard()
        with patch.object(
            EntryClusterModel,
            "classify",
            return_value={
                "cluster_id": 5,
                "subcluster_id": -1,
                "new_subcluster_id": -1,
                "liquidity_burst": 0.08,
                "cluster_reject_candidate": True,
            },
        ):
            decision = _gate_with_cluster_guard(guard).evaluate_entry(_complete_trade())
        self.assertTrue(decision.accept)
        self.assertEqual(decision.cluster_guard_status, CLUSTER_GUARD_EXCEPTION)
        self.assertTrue(decision.entry_cluster_guard_via_exception)
        self.assertEqual(guard.exception_count, 1)


class TestEntryClusterGuardConfig(unittest.TestCase):
    def test_pilot_config_loads_cluster_guard(self) -> None:
        cfg_path = (
            KABU
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        self.assertTrue(config.entry_cluster_guard_enabled)
        self.assertTrue(config.entry_cluster_guard_exception_enabled)
        self.assertAlmostEqual(
            config.entry_cluster_guard_liquidity_burst_threshold,
            0.052267,
        )
        self.assertEqual(config.raw.get("entry_cluster_guard_reject_clusters"), [5])
        # Phase606: csub reject list rolled back to empty in production YAML
        self.assertEqual(config.raw.get("entry_cluster_guard_reject_csubs"), [])
        self.assertEqual(
            config.raw.get("entry_cluster_guard_model_path"),
            "kabu_native/configs/entry_cluster_guard_model.json",
        )

    def test_make_exposure_gate_attaches_cluster_guard(self) -> None:
        cfg_path = (
            KABU
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        gate = config.make_exposure_gate(repo_root=KABU)
        self.assertIsNotNone(getattr(gate, "entry_cluster_guard", None))
        gate_prod = config.make_exposure_gate(repo_root=REPO)
        self.assertIsNotNone(getattr(gate_prod, "entry_cluster_guard", None))

    def test_rollback_enabled_false(self) -> None:
        config = SmallPaperPilotConfig(
            entry_cluster_guard_enabled=False,
            raw={"entry_cluster_guard_reject_clusters": [5]},
        )
        self.assertIsNone(build_entry_cluster_guard_state(config, repo_root=KABU))
        gate = config.make_exposure_gate(repo_root=KABU)
        self.assertIsNone(getattr(gate, "entry_cluster_guard", None))

    def test_config_reject_lists_from_raw(self) -> None:
        config = SmallPaperPilotConfig(
            entry_cluster_guard_enabled=True,
            raw={
                "entry_cluster_guard_reject_clusters": [5, 9],
                "entry_cluster_guard_reject_csubs": [1, 2],
            },
        )
        cfg = config_from_pilot(config)
        self.assertEqual(cfg.reject_clusters, frozenset({5, 9}))
        self.assertEqual(cfg.reject_csubs, frozenset({1, 2}))


class TestEntryClusterGuardSummaryAndDiscord(unittest.TestCase):
    def test_summary_fields_keys(self) -> None:
        guard = _guard()
        keys = {
            "cluster_guard_reject_count",
            "cluster_guard_exception_count",
            "cluster_guard_rejected_pnl",
            "cluster_guard_exception_pnl",
            "cluster_guard_exception_win_rate",
            "cluster_guard_exception_pf",
            "cluster_guard_exception_big_winner",
            "cluster_guard_exception_mfe0",
            "cluster_guard_blocked_cluster_counts",
        }
        summary = guard.summary_fields()
        self.assertTrue(keys.issubset(summary.keys()))

    def test_discord_entry_detail_cluster_guard(self) -> None:
        detail = build_entry_detail(
            symbol="5074.T",
            entry_price=1000.0,
            stop_price=990.0,
            slot_usage="1/5",
            entry_score_v2=5,
            data={
                "entry_type": "PBV2",
                "cluster_guard_status": CLUSTER_GUARD_EXCEPTION,
            },
        )
        self.assertIn("ENTRY_TYPE: PBV2", detail)
        self.assertIn(f"ClusterGuard: {CLUSTER_GUARD_EXCEPTION}", detail)


class TestEntryClusterGuardOrNonImpact(unittest.TestCase):
    def test_or_overlay_path_unaffected_by_cluster_guard(self) -> None:
        guard = _guard()
        gate = _gate_with_cluster_guard(guard)
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
        self.assertEqual(guard.reject_count, 0)


class TestPhase552ModelPathResolution(unittest.TestCase):
    def test_resolve_from_tradebotfile_repo_root(self) -> None:
        path = resolve_entry_cluster_guard_model_path(repo_root=REPO)
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent.parent.name, "kabu_native")

    def test_resolve_from_kabu_repo_root_with_yaml_path(self) -> None:
        path = resolve_entry_cluster_guard_model_path(
            repo_root=KABU,
            yaml_path="kabu_native/configs/entry_cluster_guard_model.json",
        )
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent.name, "configs")

    def test_load_default_model_from_tradebotfile_repo_root(self) -> None:
        model = load_default_model(repo_root=REPO)
        self.assertTrue(model.cluster_features)

    def test_build_guard_state_from_tradebotfile_repo_root(self) -> None:
        cfg_path = (
            KABU
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        state = build_entry_cluster_guard_state(config, repo_root=REPO)
        self.assertIsNotNone(state)

    def test_validate_entry_cluster_guard_model(self) -> None:
        cfg_path = (
            KABU
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        config = load_pilot_config(cfg_path)
        state, errors = validate_entry_cluster_guard_model(config, repo_root=REPO)
        self.assertEqual(errors, [])
        self.assertIsNotNone(state)

    def test_missing_model_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            resolve_entry_cluster_guard_model_path(repo_root=Path("/nonexistent/repo"))


class TestPhase549Verdict(unittest.TestCase):
    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE549_RUNTIME_VERDICT, "phase549_runtime_v6_e4_adopted")

    def test_phase552_verdict_constant(self) -> None:
        from small_paper.entry_cluster_guard import PHASE552_SMOKE_VERDICT

        self.assertEqual(
            PHASE552_SMOKE_VERDICT,
            "phase552_production_startup_smoke_test_and_model_path_fix_done",
        )


if __name__ == "__main__":
    unittest.main()
