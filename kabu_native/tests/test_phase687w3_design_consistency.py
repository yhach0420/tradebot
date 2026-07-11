"""Phase687W3 — design consistency + journal restore + Kabu boundary tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from small_paper.live_order_safety_sm import (
    KabuBrokerAdapter,
    MockBrokerAdapter,
    OrderLifecycleState,
    build_engine,
)


def test_design_consistency_script_pass():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "check_live_order_design_consistency.py"
    spec = importlib.util.spec_from_file_location("check_live_order_design_consistency", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.check()
    assert result["pass"], result.get("mismatches")


def test_journal_restore_no_resubmit():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        eng = build_engine(output_dir=td, session_id="w3/jr")
        o = eng.handle_entry_signal(symbol="X", price=1000.0, position_id="p")
        assert o.state == OrderLifecycleState.FILLED
        eng2 = build_engine(output_dir=td, session_id="w3/jr", broker=MockBrokerAdapter())
        info = eng2.restore_from_journal()
        assert info["resubmit"] is False
        assert eng2.broker.submit_count == 0  # type: ignore[attr-defined]
        assert o.order_id in eng2.orders
        assert eng2.orders[o.order_id].state == OrderLifecycleState.FILLED


def test_kabu_submit_hard_fail_and_readonly_skeleton():
    kabu = KabuBrokerAdapter()
    assert kabu.get_account_status().get("online") is False
    assert kabu.get_recent_executions() == []
    try:
        kabu.submit_entry_order({"symbol": "X", "quantity": 100})
        assert False, "expected HARD_FAIL"
    except RuntimeError as exc:
        assert "HARD_FAIL" in str(exc)


def test_receive_aliases():
    with tempfile.TemporaryDirectory() as tmp:
        eng = build_engine(output_dir=Path(tmp), session_id="w3/alias")
        o = eng.receive_entry_signal(symbol="Y", price=1000.0, position_id="p")
        assert o.state == OrderLifecycleState.FILLED
        x = eng.receive_exit_signal(symbol="Y", exit_reason="stop_hit", position_id="p")
        assert x.state == OrderLifecycleState.FILLED
