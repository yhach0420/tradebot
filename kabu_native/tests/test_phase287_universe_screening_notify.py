"""Phase287: Universe screening Discord overview."""

import unittest

from small_paper.discord_message_builder import (
    build_universe_screening_overview,
    split_watch_symbols_discord_fields,
)


class TestPhase287UniverseScreeningNotify(unittest.TestCase):
    def test_screening_overview_initial_labels(self) -> None:
        text = build_universe_screening_overview(
            session_label="AM Screening",
            watch_symbol_count=50,
        )
        self.assertIn("AM Screening", text)
        self.assertIn("50銘柄", text)
        self.assertIn("初期監視銘柄", text)
        self.assertIn("削除銘柄", text)
        self.assertIn("（なし）", text)
        self.assertNotIn("追加銘柄", text)

    def test_watch_list_one_symbol_per_line(self) -> None:
        syms = [f"{i:04d}.T" for i in range(50)]
        fields = split_watch_symbols_discord_fields(syms)
        lines = []
        for f in fields:
            for line in f["value"].splitlines():
                s = line.strip()
                if s and s[0].isdigit() and "." in s.split()[0]:
                    lines.append(s)
        self.assertEqual(len(lines), 50)
        self.assertTrue(lines[0].startswith("01."))


if __name__ == "__main__":
    unittest.main()
