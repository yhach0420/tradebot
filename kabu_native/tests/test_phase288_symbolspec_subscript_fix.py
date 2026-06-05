"""Phase288: SymbolSpec must not be subscripted in runner notify paths."""

import unittest

from storage.symbol_sources import SymbolSpec, symbol_key_name, symbol_name, symbols_list


class TestPhase288SymbolSpecSubscriptFix(unittest.TestCase):
    def test_symbol_name_from_spec(self) -> None:
        spec = SymbolSpec(symbol="7203.T", symbol_key="7203@1", exchange=1, code="7203")
        self.assertEqual(symbol_name(spec), "7203.T")
        self.assertEqual(symbol_key_name(spec), "7203@1")

    def test_symbol_name_from_dict(self) -> None:
        row = {"symbol": "3905.T", "symbol_key": "3905@1", "exchange": 1}
        self.assertEqual(symbol_name(row), "3905.T")
        self.assertEqual(symbol_key_name(row), "3905@1")

    def test_symbol_name_from_legacy_tuple(self) -> None:
        self.assertEqual(symbol_name(("6758.T", "6758@1")), "6758.T")
        self.assertEqual(symbol_key_name(("6758.T", "6758@1")), "6758@1")

    def test_symbols_list_mixed(self) -> None:
        spec = SymbolSpec(symbol="7203.T", symbol_key="7203@1", exchange=1, code="7203")
        out = symbols_list([spec, {"symbol": "3905.T"}, ("6758.T", "6758@1")])
        self.assertEqual(out, ["7203.T", "3905.T", "6758.T"])


if __name__ == "__main__":
    unittest.main()
