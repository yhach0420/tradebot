"""P2-4A synthetic A–R. No Historical Capture."""
from __future__ import annotations

from research.trailing10_dynamic_anchor_p2_4a.binding import verify_entry_time_binding
from research.trailing10_dynamic_anchor_p2_4a.synthetic import run_suite
from small_paper.v1r_primary_runtime import WAIT_SEC


def test_suite_all_pass():
    suite = run_suite()
    failed = [r for r in suite["results"] if not r["ok"]]
    assert suite["failed"] == 0, failed
    assert suite["passed"] == 18


def test_entry_time_binding_pass():
    b = verify_entry_time_binding()
    assert b["CURRENT_ENTRY_TIME_BINDING"] == "PASS", b.get("missing")


def test_wait_sec_unchanged():
    assert WAIT_SEC == 1.0
