"""Phase538: OR Open Strength Overlay runtime adoption tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.exposure_gate import (  # noqa: E402
    ExposureGate,
    ExposureGateConfig,
    REJECT_MAX_CONCURRENT,
)
from small_paper.config import load_pilot_config  # noqa: E402
from small_paper.or_overlay_cap import (  # noqa: E402
    ENTRY_TYPE_OR,
    ENTRY_TYPE_PBV2,
    REJECT_OR_CAP_FULL,
    format_split_slot_usage,
    split_pool_open_counts,
)
from small_paper.or_overlay_entry import (  # noqa: E402
    OR_REASON_OPEN_STRENGTH,
    PHASE538_RUNTIME_VERDICT,
    OrOverlayConfig,
    OrOverlaySessionState,
    compute_day_return_rank,
    compute_or_overlay_fields,
    config_from_pilot,
    evaluate_or_overlay_entry,
    passes_o_r003_day_high,
    resolve_or_reason,
)


class _FakeObserver:
    def __init__(self, positions: list[dict[str, str]] | None = None) -> None:
        self._positions = positions or []

    def open_count(self) -> int:
        return len(self._positions)

    def has_open(self, symbol: str) -> bool:
        return any(p["symbol"] == symbol for p in self._positions)

    def open_count_by_entry_type(self) -> tuple[int, int]:
        pbv2 = sum(1 for p in self._positions if p.get("entry_type", ENTRY_TYPE_PBV2) != ENTRY_TYPE_OR)
        or_open = sum(1 for p in self._positions if p.get("entry_type") == ENTRY_TYPE_OR)
        return pbv2, or_open

    def open_positions(self) -> list[dict[str, str]]:
        return list(self._positions)


def _gate() -> ExposureGate:
    cfg = ExposureGateConfig(
        profile="momentum_volume_v13_combined",
        min_continuation_quality=0.70,
        max_concurrent_positions=5,
        position_cap_mode=True,
    )
    return ExposureGate(cfg)


class TestOrOverlayCap(unittest.TestCase):
    def test_split_pool_counts(self) -> None:
        obs = _FakeObserver(
            [
                {"symbol": "1111.T", "entry_type": ENTRY_TYPE_PBV2},
                {"symbol": "2222.T", "entry_type": ENTRY_TYPE_PBV2},
                {"symbol": "3333.T", "entry_type": ENTRY_TYPE_OR},
            ]
        )
        pbv2, or_open, total = split_pool_open_counts(obs)
        self.assertEqual((pbv2, or_open, total), (2, 1, 3))

    def test_split_slot_usage_format(self) -> None:
        text = format_split_slot_usage(pbv2_open=3, or_open=1, cap_pbv2=4, cap_or=1)
        self.assertIn("PBv2 3/4", text)
        self.assertIn("OR 1/1", text)


class TestOrOverlayEntry(unittest.TestCase):
    def test_day_return_rank(self) -> None:
        returns = {"A.T": 5.0, "B.T": 2.0, "C.T": 8.0}
        self.assertEqual(compute_day_return_rank("C.T", day_returns=returns), 1)
        self.assertEqual(compute_day_return_rank("A.T", day_returns=returns), 2)

    def test_o_r003_requires_day_high_and_updates(self) -> None:
        trade = {"entry_near_day_high_pct": 0.05, "update_count_before_entry": 8}
        payload = {"CurrentPrice": 1000, "HighPrice": 1000}
        self.assertTrue(
            passes_o_r003_day_high(
                trade,
                payload,
                price_ring=[],
                entry_ts=1_750_000_000.0,
                max_update_count=8,
            )
        )
        trade_fail = {"entry_near_day_high_pct": 2.0, "update_count_before_entry": 8}
        self.assertFalse(
            passes_o_r003_day_high(
                trade_fail,
                payload,
                price_ring=[],
                entry_ts=1_750_000_000.0,
                max_update_count=8,
            )
        )

    def test_resolve_or_reason_open_strength(self) -> None:
        row = {
            "minutes_from_open": 45,
            "day_return_rank": 3,
            "vwap_distance": 0.5,
        }
        self.assertEqual(resolve_or_reason(row), OR_REASON_OPEN_STRENGTH)

    def test_evaluate_or_overlay_accepts_candidate(self) -> None:
        gate = _gate()
        or_st = OrOverlaySessionState(
            config=OrOverlayConfig(enabled=True, cap_pbv2=4, cap_or=1),
            day_return_by_symbol={"5074.T": 6.0, "7203.T": 1.0},
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
            trade=trade,
            payload=payload,
            price_ring=[],
            entry_ts=1_750_000_000.0,
            observer=_FakeObserver(),
            or_state=or_st,
            universe_symbols=["5074.T", "7203.T"],
        )
        self.assertTrue(decision.accept)
        self.assertEqual(trade.get("entry_type"), ENTRY_TYPE_OR)
        self.assertEqual(trade.get("or_reason"), OR_REASON_OPEN_STRENGTH)

    def test_or_pool_full_blocks(self) -> None:
        gate = _gate()
        or_st = OrOverlaySessionState(
            config=OrOverlayConfig(enabled=True, cap_or=1),
            day_return_by_symbol={"5074.T": 6.0},
        )
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "5074.T",
            "entry_time": "2026-06-24T10:15:00+09:00",
            "trade_date": "2026-06-24",
            "continuation_quality_score": 0.5,
            "entry_near_day_high_pct": 0.05,
            "update_count_before_entry": 2,
            "entry_vwap_dev_pct": 0.4,
        }
        payload = {"CurrentPrice": 1000, "HighPrice": 1000}
        obs = _FakeObserver([{"symbol": "9999.T", "entry_type": ENTRY_TYPE_OR}])
        decision = evaluate_or_overlay_entry(
            gate=gate,
            trade=trade,
            payload=payload,
            price_ring=[],
            entry_ts=1_750_000_000.0,
            observer=obs,
            or_state=or_st,
        )
        self.assertFalse(decision.accept)
        self.assertEqual(decision.reason, REJECT_OR_CAP_FULL)
        self.assertEqual(or_st.or_cap_full_count, 1)

    def test_pbv2_pool_independent_of_or(self) -> None:
        obs = _FakeObserver([{"symbol": "9999.T", "entry_type": ENTRY_TYPE_OR}])
        pbv2, or_open, total = split_pool_open_counts(obs)
        self.assertEqual((pbv2, or_open, total), (0, 1, 1))
        from small_paper.or_overlay_entry import pbv2_cap_kwargs

        cap_kw = pbv2_cap_kwargs(
            type("Cfg", (), {"or_overlay_enabled": True, "cap_pbv2": 4, "cap_or": 1})(),
            obs,
            "5074.T",
        )
        self.assertEqual(cap_kw["observer_open_count"], 0)
        self.assertEqual(cap_kw["max_concurrent_positions"], 4)

    def test_summary_fields_present(self) -> None:
        or_st = OrOverlaySessionState(config=OrOverlayConfig(enabled=True))
        or_st.or_entry_count = 2
        or_st.pbv2_count = 5
        or_st.or_count = 2
        fields = or_st.summary_fields(events=[], observer=_FakeObserver())
        for key in (
            "or_entry_count",
            "or_exit_count",
            "or_active_positions",
            "or_realized_pnl",
            "or_unrealized_pnl",
            "or_win_rate",
            "or_pf",
            "or_blocked_count",
            "or_cap_full_count",
            "pbv2_count",
            "or_count",
        ):
            self.assertIn(key, fields)


class TestOrOverlayConfig(unittest.TestCase):
    def test_yaml_loads_or_overlay(self) -> None:
        cfg_path = (
            REPO
            / "kabu_native"
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        if not cfg_path.exists():
            cfg_path = REPO / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        if not cfg_path.exists():
            self.skipTest("pilot yaml not found")
        cfg = load_pilot_config(cfg_path)
        ocfg = config_from_pilot(cfg)
        self.assertTrue(ocfg.enabled)
        self.assertEqual(ocfg.cap_pbv2, 4)
        self.assertEqual(ocfg.cap_or, 1)

    def test_rollback_flag_disables(self) -> None:
        class _Cfg:
            or_overlay_enabled = False

        self.assertIsNone(
            __import__("small_paper.or_overlay_entry", fromlist=["build_or_overlay_state"]).build_or_overlay_state(
                _Cfg()
            )
        )


class TestPhase538Verdict(unittest.TestCase):
    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE538_RUNTIME_VERDICT, "phase538_or_overlay_runtime_adopted")


if __name__ == "__main__":
    unittest.main()
