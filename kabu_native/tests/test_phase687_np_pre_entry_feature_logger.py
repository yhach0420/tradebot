"""Phase687 — No-Progress Pre-Entry Board/Volume Forward Logger tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.live_writer import LiveSessionWriter  # noqa: E402
from small_paper.np_pre_entry_feature_logger import (  # noqa: E402
    OUTCOME_KEYS,
    WINDOWS_SEC,
    append_board_snap,
    build_np_pre_entry_outcome_row,
    collection_gate_for_business_days,
    compute_np_pre_entry_predictor_row,
    extract_board_snap,
    is_leaky_predictor_key,
    np_pre_entry_feature_logger_enabled,
    predictor_field_keys,
)


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {"np_pre_entry_feature_logger_enabled": True}
    base.update(overrides)
    return SimpleNamespace(**base)


def _ring_up_to(accepted_at: float, *, n: int = 40, step: float = 5.0) -> list:
    ring = []
    for i in range(n):
        ts = accepted_at - (n - i) * step
        px = 1000.0 + i * 0.5
        imb = 0.1 + i * 0.01
        bid = 1000.0 + i
        ask = 900.0 - i * 0.5
        tv = 1e8 + i * 1e5
        ring.append((ts, px, imb, bid, ask, tv))
    return ring


class TestNpPreEntryFeatureLogger(unittest.TestCase):
    def test_enabled_flag(self) -> None:
        self.assertTrue(np_pre_entry_feature_logger_enabled(_cfg()))
        self.assertFalse(np_pre_entry_feature_logger_enabled(_cfg(np_pre_entry_feature_logger_enabled=False)))

    def test_extract_and_append_board_snap(self) -> None:
        payload = {
            "CurrentPrice": 1234.0,
            "BidQty": 100,
            "AskQty": 80,
            "TradingValue": 2e8,
        }
        snap = extract_board_snap(payload, ts=1000.0)
        self.assertIsNotNone(snap)
        ring: list = []
        append_board_snap(ring, snap)  # type: ignore[arg-type]
        self.assertEqual(len(ring), 1)
        self.assertEqual(ring[0][1], 1234.0)

    def test_predictor_row_no_post_accept_ticks(self) -> None:
        accepted_at = 10_000.0
        ring = _ring_up_to(accepted_at)
        # Inject future leak tick
        ring.append((accepted_at + 30.0, 1100.0, 0.5, 1.0, 1.0, 1e8))
        row = compute_np_pre_entry_predictor_row(
            trade={"symbol": "6976.T", "entry_time": "t0", "day": "2026-07-11", "session": "AM"},
            board_ring=ring,
            accepted_at_ts=accepted_at,
            accepted_at_iso="2026-07-11T09:15:00+09:00",
        )
        self.assertTrue(row["np_logger_ok"])
        self.assertFalse(row["np_future_leakage"])
        self.assertLessEqual(float(row["np_max_source_ts"]), accepted_at)
        self.assertIn("np_ret_10s", row)
        self.assertIn("np_vol_price_sync_60s", row)
        self.assertIn("np_imb_persist_120s", row)
        for k in row:
            self.assertFalse(is_leaky_predictor_key(k), msg=f"leaky key: {k}")

    def test_outcome_separated_from_predictors(self) -> None:
        pred = compute_np_pre_entry_predictor_row(
            trade={"symbol": "6976.T", "entry_time": "t0"},
            board_ring=_ring_up_to(1000.0),
            accepted_at_ts=1000.0,
            accepted_at_iso="iso",
        )
        outcome = build_np_pre_entry_outcome_row(
            predictor_row=pred,
            exit_row={"exit_reason": "no_progress_exit", "pnl_yen_100": -1500, "hold_sec": 900},
        )
        self.assertTrue(outcome["is_no_progress_exit"])
        self.assertTrue(outcome["is_loser"])
        self.assertEqual(outcome["source"], "outcome_label")
        for k in pred:
            if k in ("np_logger_row_id", "symbol", "day", "session", "entry_time", "accepted_at"):
                continue
            self.assertNotIn(k, OUTCOME_KEYS)

    def test_sidecar_writer_one_row(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            writer = LiveSessionWriter(out, incremental=True, event_fields=["symbol"])
            row = {"np_logger_row_id": "x", "np_ret_10s": 0.1}
            writer.append_np_pre_entry_features(row)
            writer.append_np_pre_entry_outcomes({"np_logger_row_id": "x", "pnl_yen_100": -100})
            feat_lines = (out / "np_pre_entry_features.jsonl").read_text(encoding="utf-8").strip().splitlines()
            out_lines = (out / "np_pre_entry_outcomes.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(feat_lines), 1)
            self.assertEqual(len(out_lines), 1)
            self.assertEqual(json.loads(feat_lines[0])["np_logger_row_id"], "x")

    def test_collection_gates(self) -> None:
        self.assertEqual(collection_gate_for_business_days(0), "DATA_COLLECTION_ONLY")
        self.assertEqual(collection_gate_for_business_days(4), "DATA_COLLECTION_ONLY")
        self.assertEqual(collection_gate_for_business_days(5), "FEATURE_STABILITY_REVIEW_ALLOWED")
        self.assertEqual(collection_gate_for_business_days(10), "RULE_DISCOVERY_ALLOWED")

    def test_predictor_keys_cover_all_windows(self) -> None:
        keys = predictor_field_keys()
        for w in WINDOWS_SEC:
            self.assertIn(f"np_ret_{w}s", keys)
            self.assertIn(f"np_vol_price_sync_{w}s", keys)

    def test_leaky_key_detector(self) -> None:
        self.assertTrue(is_leaky_predictor_key("pnl_yen_100"))
        self.assertTrue(is_leaky_predictor_key("exit_reason"))
        self.assertTrue(is_leaky_predictor_key("is_loser"))
        self.assertFalse(is_leaky_predictor_key("np_ret_30s"))
        self.assertFalse(is_leaky_predictor_key("np_logger_ok"))


class TestPhase687ConfigWiring(unittest.TestCase):
    def test_yaml_flag_loads(self) -> None:
        from small_paper.config import load_pilot_config

        cfg_path = (
            NATIVE
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        cfg = load_pilot_config(cfg_path)
        self.assertTrue(cfg.np_pre_entry_feature_logger_enabled)

    def test_event_fields_include_np_meta(self) -> None:
        from small_paper.pilot_runner import EVENT_FIELDS

        self.assertIn("np_logger_ok", EVENT_FIELDS)
        self.assertIn("np_feature_complete", EVENT_FIELDS)
        self.assertIn("np_logger_row_id", EVENT_FIELDS)


if __name__ == "__main__":
    unittest.main()
