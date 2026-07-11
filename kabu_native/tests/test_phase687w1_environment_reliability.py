"""Phase687W1 — environment reliability tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.config import SmallPaperPilotConfig  # noqa: E402
from small_paper.daytrade_suitability_gate import (  # noqa: E402
    DaytradeSuitabilityConfig,
    DaytradeSuitabilityState,
)
from small_paper.post_entry_forward_shadow import PostEntryForwardShadowSession  # noqa: E402
from small_paper.vol_liq_session_key import (  # noqa: E402
    am_pm_cache_reuse_allowed,
    normalize_vol_liq_run_session_key,
    vol_liq_cache_key_aliases,
)
from small_paper.vol_liq_startup_cache import (  # noqa: E402
    build_vol_liq_threshold_with_startup_cache,
    config_fingerprint,
    get_vol_liq_cache_metrics,
    save_cache_payload,
    state_to_cache_payload,
    resolve_cache_dir,
)
from research.phase687w1_environment_reliability import (  # noqa: E402
    _is_protected,
    collect_disk_audit_rows,
    execute_disk_cleanup,
)


def _cfg(**kw) -> SmallPaperPilotConfig:
    base = SmallPaperPilotConfig(
        daytrade_suitability_enabled=True,
        vol_liq_startup_cache_enabled=True,
        vol_liq_startup_cache_dir="kabu_native/results/cache/vol_liq_startup",
    )
    return replace(base, **kw)


def _state(run_key: str) -> DaytradeSuitabilityState:
    return DaytradeSuitabilityState(
        config=DaytradeSuitabilityConfig(enabled=True),
        run_session_key=run_key,
        source_sessions=["20260624/live_session_080340"],
        vol_liq_threshold=54.695739,
        prior_quality_trade_count=3,
    )


class TestVolLiqKeyNormalize(unittest.TestCase):
    def test_bare_stamp_to_live_session(self) -> None:
        self.assertEqual(
            normalize_vol_liq_run_session_key("20260710/084821"),
            "20260710/live_session_084821",
        )

    def test_aliases_include_both(self) -> None:
        aliases = vol_liq_cache_key_aliases("20260710/084821")
        self.assertIn("20260710/live_session_084821", aliases)
        self.assertIn("20260710/084821", aliases)

    def test_am_pm_reuse_forbidden(self) -> None:
        self.assertFalse(
            am_pm_cache_reuse_allowed(
                am_key="20260711/live_session_084500",
                pm_key="20260711/live_session_122500",
            )
        )


class TestVolLiqCacheAliasHit(unittest.TestCase):
    def test_bare_stamp_cache_hits_canonical_key(self, tmp_path: Path | None = None) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            cfg = _cfg()
            # Point cache dir into tmp via absolute override
            cfg = replace(cfg, vol_liq_startup_cache_dir=str(repo / "cache"))
            bare = "20260710/084821"
            canon = "20260710/live_session_084821"
            cache_dir = resolve_cache_dir(cfg, repo_root=repo)
            scores = [10.0, 20.0, 54.695739]
            # Save under bare key (historical safety behavior)
            save_cache_payload(
                cache_dir,
                state_to_cache_payload(
                    _state(bare),
                    scores,
                    config_fp=config_fingerprint(cfg),
                    baseline_elapsed_sec=986.0,
                ),
            )
            with patch(
                "small_paper.vol_liq_startup_cache.build_vol_liq_threshold_full_scan_with_scores"
            ) as mocked:
                out = build_vol_liq_threshold_with_startup_cache(
                    cfg, repo_root=repo, run_session_key=canon
                )
                mocked.assert_not_called()
            self.assertIsNotNone(out)
            assert out is not None
            self.assertEqual(out.vol_liq_threshold, 54.695739)
            m = get_vol_liq_cache_metrics(canon)
            self.assertIsNotNone(m)
            assert m is not None
            self.assertTrue(m.vol_liq_cache_hit)
            self.assertLess(m.vol_liq_cache_elapsed_sec, 5.0)

    def test_stale_fingerprint_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            cfg = _cfg()
            cfg = replace(cfg, vol_liq_startup_cache_dir=str(repo / "cache"))
            run_key = "20260710/live_session_084821"
            cache_dir = resolve_cache_dir(cfg, repo_root=repo)
            bad_fp = config_fingerprint(cfg)
            bad_fp["daytrade_suitability_rule"] = "other_rule"
            save_cache_payload(
                cache_dir,
                state_to_cache_payload(
                    _state(run_key),
                    [1.0, 2.0],
                    config_fp=bad_fp,
                    baseline_elapsed_sec=900.0,
                ),
            )
            state = _state(run_key)
            with patch(
                "small_paper.vol_liq_startup_cache.build_vol_liq_threshold_full_scan_with_scores",
                return_value=(state, [1.0, 2.0, 3.0]),
            ):
                out = build_vol_liq_threshold_with_startup_cache(
                    cfg, repo_root=repo, run_session_key=run_key
                )
            self.assertIsNotNone(out)
            m = get_vol_liq_cache_metrics(run_key)
            assert m is not None
            self.assertTrue(m.vol_liq_cache_fallback)
            self.assertIn("config_fingerprint_mismatch", m.vol_liq_cache_fallback_reason)


class TestExtensionFinalize(unittest.TestCase):
    def test_finalize_exists_and_idempotent(self) -> None:
        pe = PostEntryForwardShadowSession()
        pe.finalize_session_end(ts=1.0, day="20260711")
        pe.finalize_session_end(ts=2.0, day="20260711")
        self.assertTrue(pe._session_end_finalized)
        self.assertEqual(pe.summary_fields()["post_entry_shadow_score_ge3_count"], 0)


class TestDiskProtection(unittest.TestCase):
    def test_protected_live_session(self) -> None:
        p = NATIVE / "results" / "small_paper" / "20260710" / "live_session_122525"
        self.assertTrue(_is_protected(p))

    def test_protected_push(self) -> None:
        p = NATIVE / "data" / "push_jsonl"
        self.assertTrue(_is_protected(p))

    def test_cleanup_refuses_protected(self) -> None:
        rows = [
            {
                "path": str(NATIVE / "results" / "small_paper" / "20260710" / "live_session_122525"),
                "category": "canonical",
                "size_bytes": 100,
                "size_gb": 0,
                "action": "delete",
                "delete_reason": "should_refuse",
                "protected": True,
            }
        ]
        out = execute_disk_cleanup(rows, dry_run=False)
        self.assertFalse(out[0]["deleted"])
        self.assertEqual(out[0]["error"], "protected_path_refused")

    def test_disk_audit_dry_run(self) -> None:
        rows = collect_disk_audit_rows()
        self.assertTrue(any(r["action"] == "keep" for r in rows))
        dry = execute_disk_cleanup(rows, dry_run=True)
        self.assertTrue(all(not r.get("deleted") for r in dry if r.get("action") == "delete"))


class TestPhase687LoggerUnchanged(unittest.TestCase):
    def test_logger_module_present(self) -> None:
        path = NATIVE / "src" / "small_paper" / "np_pre_entry_feature_logger.py"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("compute_np_pre_entry_predictor_row", text)


class TestLatencySemantics(unittest.TestCase):
    def test_emit_marks_stale(self) -> None:
        from small_paper.order_latency_dryrun_trace import OrderLatencyDryRunSession

        with tempfile.TemporaryDirectory() as td:
            sess = OrderLatencyDryRunSession(output_dir=Path(td))
            # Begin with stale CurrentPriceTime (~1h old vs push)
            sess.begin_push(
                symbol="6976.T",
                payload={"CurrentPriceTime": "2026-07-10T11:00:00+09:00"},
                message_index=1,
                t1_push_received_at="2026-07-10T12:00:00+09:00",
                t2_mono=100.0,
            )
            sess.mark_decision_end(accepted=True, entry_route="PBV2", gate_reason="pass")
            # Force emit path via dryrun marks
            tr = sess._active
            assert tr is not None
            tr.t5_decision_at = "2026-07-10T12:00:01+09:00"
            tr.t9_dryrun_start_at = "2026-07-10T12:00:02+09:00"
            tr.t9_dryrun_start_mono = 102.0
            tr.t10_dryrun_end_mono = 102.1
            tr.accepted = True
            sess._emit(tr)
            row = sess.samples[-1]
            self.assertTrue(row["stale_market_timestamp"])
            self.assertTrue(row["price_to_order_is_stale_inflated"])
            self.assertGreater(row["price_to_order_sec"], 3000)
            self.assertLess(row["push_to_order_sec"], 5)


if __name__ == "__main__":
    unittest.main()
