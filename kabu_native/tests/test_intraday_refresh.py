"""Phase 157: intraday refresh universe merge tests."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
for p in (NATIVE / "src", NATIVE.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from universe.intraday_refresh import (  # noqa: E402
    FOCUS_EXCLUDE_SYMBOL,
    merge_register_specs,
    merge_universe_with_open_symbols,
)


def _sample_rows() -> list[dict]:
    rows = []
    for i in range(10):
        sym = f"100{i}.T"
        rows.append(
            {
                "symbol": sym,
                "symbol_key": f"100{i}@1",
                "exchange": "1",
                "universe_slot": "core" if i < 2 else "dynamic",
                "source_bucket": "core10_discord" if i < 2 else "vol_liq_dynamic40",
                "am_pm_session": "am",
            }
        )
    return rows


class TestIntradayRefresh(unittest.TestCase):
    def test_merge_open_symbol_priority(self) -> None:
        base = _sample_rows()
        open_sym = "9999.T"
        merged, meta = merge_universe_with_open_symbols(
            base,
            open_symbols=[open_sym],
            feature_rows=[],
            symbol_meta={},
            session="am",
            refresh_time="10:00",
        )
        syms = [r["symbol"] for r in merged]
        self.assertEqual(syms[0], open_sym)
        self.assertTrue(meta["carried_count"] >= 1)
        self.assertEqual(meta.get("duplicate_count"), 0)

    def test_register_specs_dedupe(self) -> None:
        base = _sample_rows()
        merged, _ = merge_universe_with_open_symbols(
            base,
            open_symbols=[],
            feature_rows=[],
            symbol_meta={},
            session="am",
            refresh_time="10:00",
        )
        specs, reg_meta = merge_register_specs(merged, symbol_meta={})
        self.assertEqual(len(specs), len(merged))
        self.assertEqual(reg_meta.get("duplicate_count"), 0)
        self.assertTrue(reg_meta.get("register_count_ok"))

    def test_open_symbols_exceed_cap_blocks(self) -> None:
        """Merge returns empty only when open symbols exceed TOTAL_SLOTS (register universe=50)."""
        from universe.intraday_refresh import TOTAL_SLOTS

        base = _sample_rows()
        too_many = [f"O{i}.T" for i in range(TOTAL_SLOTS + 1)]
        merged, meta = merge_universe_with_open_symbols(
            base,
            open_symbols=too_many,
            feature_rows=[],
            symbol_meta={},
            session="am",
            refresh_time="10:00",
        )
        self.assertEqual(merged, [])
        self.assertEqual(meta.get("error"), "open_symbols_exceed_cap")

    def test_5856_constant(self) -> None:
        self.assertEqual(FOCUS_EXCLUDE_SYMBOL, "5856.T")


if __name__ == "__main__":
    unittest.main()
