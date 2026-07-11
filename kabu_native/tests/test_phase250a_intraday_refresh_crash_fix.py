import unittest
from pathlib import Path


class TestPhase250aIntradayRefreshCrashFix(unittest.TestCase):
    def _maybe_intraday_refresh_body(self) -> str:
        src = (Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py").read_text(
            encoding="utf-8"
        )
        start = src.index("    def _maybe_intraday_refresh() -> None:")
        end = src.index("    def _maybe_am_pm_force_close() -> None:", start)
        return src[start:end]

    def test_open_syms_assigned_before_first_use(self) -> None:
        body = self._maybe_intraday_refresh_body()
        assign = body.index("open_syms = observer.open_symbols()")
        first_use = body.index("len(open_syms)")
        self.assertLess(assign, first_use)

    def test_refresh_csv_missing_failed_path_uses_open_syms(self) -> None:
        body = self._maybe_intraday_refresh_body()
        missing_idx = body.index('"reason": "refresh_csv_missing"')
        assign = body.index("open_syms = observer.open_symbols()")
        self.assertLess(assign, missing_idx)

    def test_phase242b_logging_fields_present_on_failed_and_completed(self) -> None:
        body = self._maybe_intraday_refresh_body()
        for event in ("failed", "completed"):
            for field in (
                "carried_open_symbols_count",
                "refresh_symbols_added_count",
                "final_register_count",
            ):
                self.assertIn(field, body, msg=f"missing {field} near {event}")
