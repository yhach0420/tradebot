"""Phase422 intraday refresh safety guard CAP5 tests."""

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

from universe.intraday_refresh import check_intraday_refresh_policy  # noqa: E402


def _policy(
    *,
    max_concurrent_positions: int = 5,
    position_cap_mode: bool = True,
    same_symbol_open_policy: str = "no_overlap_replace",
    paper_only: bool = True,
    order_enabled: bool = False,
) -> dict:
    return check_intraday_refresh_policy(
        refresh_enabled=True,
        max_concurrent_positions=max_concurrent_positions,
        register_count=50,
        open_symbols_count=0,
        price_risk_mode=True,
        entry_guard_enabled=True,
        position_cap_mode=position_cap_mode,
        same_symbol_open_policy=same_symbol_open_policy,
        paper_only=paper_only,
        order_enabled=order_enabled,
    )


class TestPhase422IntradayRefreshCap5Guard(unittest.TestCase):
    def test_cap5_full_guard_passes(self) -> None:
        pol = _policy()
        self.assertTrue(pol["ok"])
        self.assertEqual(pol["issues"], [])
        guard = pol["runtime_guard"]
        self.assertEqual(guard["max_concurrent_positions"], 5)
        self.assertTrue(guard["position_cap_mode"])
        self.assertEqual(guard["same_symbol_open_policy"], "no_overlap_replace")

    def test_cap6_fails(self) -> None:
        pol = _policy(max_concurrent_positions=6)
        self.assertFalse(pol["ok"])
        self.assertIn("refresh_requires_max_concurrent_lte_5", pol["issues"])

    def test_cap5_order_enabled_fails(self) -> None:
        pol = _policy(order_enabled=True)
        self.assertFalse(pol["ok"])
        self.assertIn("refresh_requires_order_disabled", pol["issues"])

    def test_cap5_replace_policy_fails(self) -> None:
        pol = _policy(same_symbol_open_policy="replace")
        self.assertFalse(pol["ok"])
        self.assertIn("refresh_requires_no_overlap_replace_policy", pol["issues"])

    def test_cap5_no_position_cap_mode_fails(self) -> None:
        pol = _policy(position_cap_mode=False)
        self.assertFalse(pol["ok"])
        self.assertIn("refresh_requires_position_cap_mode", pol["issues"])

    def test_cap3_legacy_passes(self) -> None:
        pol = _policy(max_concurrent_positions=3)
        self.assertTrue(pol["ok"])


if __name__ == "__main__":
    unittest.main()
