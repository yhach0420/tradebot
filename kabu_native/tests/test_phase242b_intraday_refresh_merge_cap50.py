import unittest

from universe.intraday_refresh import merge_universe_with_open_symbols


def _row(sym: str, slot: str = "dynamic") -> dict:
    return {
        "symbol": sym,
        "symbol_key": sym.replace(".T", "") + "@1",
        "exchange": "1",
        "universe_slot": slot,
        "rank": "0",
        "passed": "true",
    }


class TestPhase242bIntradayRefreshMergeCap50(unittest.TestCase):
    def _base_rows_50(self) -> list[dict]:
        # 10 core + 40 dynamic
        rows = [_row(f"C{i:04d}.T", "core") for i in range(10)]
        rows += [_row(f"D{i:04d}.T", "dynamic") for i in range(40)]
        return rows

    def test_open_symbols_count_0_success(self) -> None:
        base = self._base_rows_50()
        merged, meta = merge_universe_with_open_symbols(
            base,
            open_symbols=[],
            feature_rows=[],
            symbol_meta={},
            session="pm",
            refresh_time="14:30",
        )
        self.assertNotIn("error", meta)
        self.assertEqual(len(merged), 50)
        self.assertEqual(int(meta.get("carried_open_symbols_count") or 0), 0)
        self.assertEqual(int(meta.get("final_register_count") or 0), 50)

    def test_open_symbols_count_3_success(self) -> None:
        base = self._base_rows_50()
        open_syms = ["X0001.T", "X0002.T", "X0003.T"]
        merged, meta = merge_universe_with_open_symbols(
            base,
            open_symbols=open_syms,
            feature_rows=[],
            symbol_meta={},
            session="pm",
            refresh_time="14:30",
        )
        self.assertNotIn("error", meta)
        self.assertEqual(len(merged), 50)
        self.assertEqual([m["symbol"] for m in merged[:3]], sorted(open_syms))
        self.assertEqual(int(meta.get("carried_open_symbols_count") or 0), 3)
        self.assertEqual(int(meta.get("final_register_count") or 0), 50)

    def test_open_symbols_count_8_success(self) -> None:
        base = self._base_rows_50()
        open_syms = [f"X{i:04d}.T" for i in range(8)]
        merged, meta = merge_universe_with_open_symbols(
            base,
            open_symbols=open_syms,
            feature_rows=[],
            symbol_meta={},
            session="pm",
            refresh_time="14:30",
        )
        self.assertNotIn("error", meta)
        self.assertEqual(len(merged), 50)
        self.assertEqual([m["symbol"] for m in merged[:8]], sorted(open_syms))
        self.assertEqual(int(meta.get("carried_open_symbols_count") or 0), 8)
        self.assertEqual(int(meta.get("refresh_symbols_added_count") or 0), 42)
        self.assertEqual(int(meta.get("final_register_count") or 0), 50)

    def test_open_symbols_count_50_success(self) -> None:
        base = self._base_rows_50()
        open_syms = [f"X{i:04d}.T" for i in range(50)]
        merged, meta = merge_universe_with_open_symbols(
            base,
            open_symbols=open_syms,
            feature_rows=[],
            symbol_meta={},
            session="pm",
            refresh_time="14:30",
        )
        self.assertNotIn("error", meta)
        self.assertEqual(len(merged), 50)
        self.assertEqual([m["symbol"] for m in merged], sorted(open_syms))
        self.assertEqual(int(meta.get("carried_open_symbols_count") or 0), 50)
        self.assertEqual(int(meta.get("refresh_symbols_added_count") or 0), 0)
        self.assertEqual(int(meta.get("final_register_count") or 0), 50)

    def test_open_symbols_count_51_fails(self) -> None:
        base = self._base_rows_50()
        open_syms = [f"X{i:04d}.T" for i in range(51)]
        merged, meta = merge_universe_with_open_symbols(
            base,
            open_symbols=open_syms,
            feature_rows=[],
            symbol_meta={},
            session="pm",
            refresh_time="14:30",
        )
        self.assertEqual(meta.get("error"), "open_symbols_exceed_cap")
        self.assertEqual(merged, [])


if __name__ == "__main__":
    unittest.main()

