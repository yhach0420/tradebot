import unittest

from small_paper.vwap_shadow_reject import (
    VWAP_SHADOW_REJECT_MIN,
    VwapShadowRejectCounters,
    compute_vwap_shadow_reject_fields,
    enrich_exit_vwap_shadow_fields,
)


class TestPhase186VwapShadowReject(unittest.TestCase):
    def test_candidate_at_threshold(self) -> None:
        fields = compute_vwap_shadow_reject_fields(
            payload={"CurrentPrice": 102.5, "VWAP": 100.0},
            entry_px=102.5,
        )
        self.assertTrue(fields["vwap_shadow_reject_candidate"])
        self.assertEqual(fields["vwap_shadow_reject_reason"], "vwap_dev_ge_2p5")
        self.assertEqual(fields["entry_vwap_dev_pct"], 2.5)

    def test_not_candidate_below_threshold(self) -> None:
        fields = compute_vwap_shadow_reject_fields(
            payload={"CurrentPrice": 102.0, "VWAP": 100.0},
            entry_px=102.0,
        )
        self.assertFalse(fields["vwap_shadow_reject_candidate"])
        self.assertEqual(fields["vwap_shadow_reject_reason"], "")

    def test_reuses_precomputed_dev(self) -> None:
        fields = compute_vwap_shadow_reject_fields(
            payload={"VWAP": 100.0},
            entry_px=103.0,
            entry_vwap_dev_pct=3.1,
        )
        self.assertTrue(fields["vwap_shadow_reject_candidate"])
        self.assertEqual(fields["entry_vwap_dev_pct"], 3.1)

    def test_exit_enrich(self) -> None:
        out = enrich_exit_vwap_shadow_fields(
            {"vwap_shadow_reject_candidate": True, "vwap_shadow_reject_reason": "vwap_dev_ge_2p5", "entry_vwap_dev_pct": 2.8},
            pnl_pct=-1.5,
            exit_reason="stop_hit",
        )
        self.assertTrue(out["vwap_shadow_reject_candidate"])
        self.assertTrue(out["stop_hit"])
        self.assertFalse(out["trailing_mfe_exit"])
        self.assertEqual(out["pnl_pct"], -1.5)

    def test_counters(self) -> None:
        c = VwapShadowRejectCounters()
        c.record_accept({"vwap_shadow_reject_candidate": True})
        c.record_accept({"vwap_shadow_reject_candidate": False})
        c.record_exit(
            {
                "vwap_shadow_reject_candidate": True,
                "pnl_pct": 2.0,
                "exit_reason": "trailing_mfe_exit",
                "trailing_mfe_exit": True,
            }
        )
        c.record_exit(
            {
                "vwap_shadow_reject_candidate": True,
                "pnl_pct": -1.0,
                "exit_reason": "stop_hit",
                "stop_hit": True,
            }
        )
        s = c.summary_fields()
        self.assertEqual(s["vwap_shadow_reject_candidate_count"], 1)
        self.assertEqual(s["vwap_shadow_candidate_total_pnl"], 1.0)
        self.assertEqual(s["vwap_shadow_candidate_stop_hit_count"], 1)
        self.assertEqual(s["vwap_shadow_candidate_trailing_mfe_count"], 1)

    def test_fixed_threshold(self) -> None:
        self.assertEqual(VWAP_SHADOW_REJECT_MIN, 2.5)


if __name__ == "__main__":
    unittest.main()
