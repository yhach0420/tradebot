import unittest
from pathlib import Path


class TestPhase176IntradayRefreshDegradedBehavior(unittest.TestCase):
    def test_open_symbols_exceed_cap_does_not_request_stop_regression(self) -> None:
        """
        We cannot easily unit-test the inner closure `_maybe_intraday_refresh()` without
        running the live loop. This regression test enforces the critical invariant:
        open_symbols_exceed_cap MUST NOT call _request_stop().
        """
        path = Path("kabu_native/src/small_paper/pilot_runner.py")
        src = path.read_text(encoding="utf-8")
        self.assertNotIn('_request_stop("open_symbols_exceed_cap")', src)
        self.assertIn("action\": \"continue_keep_previous_subscription", src)
        self.assertIn("will_stop\": False", src)
        self.assertIn("state.intraday_refresh_done = True", src)
        self.assertIn("if not specs:", src)

