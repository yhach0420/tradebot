"""Phase687W4S — forward soak evaluator unit checks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from small_paper.live_order_account_status import AccountReadStatus
from small_paper.live_order_runtime_bridge import (
    ENTRY_SOURCE_ACTUAL,
    EXIT_SOURCE_ACTUAL,
    build_runtime_bridge,
    write_soak_session_snapshot,
)
from small_paper.live_order_safety_sm import KabuBrokerAdapter
from types import SimpleNamespace


def test_client_token_not_weekend_misclassified():
    assert KabuBrokerAdapter().refresh_readonly() == AccountReadStatus.CLIENT_NOT_CONFIGURED.value
    assert (
        KabuBrokerAdapter(client=object(), token="").refresh_readonly()
        == AccountReadStatus.TOKEN_REQUEST_FAILED.value
    )


def test_soak_snapshot_writer():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        cfg = SimpleNamespace(
            live_trading_enabled=False,
            order_enabled=False,
            dry_run=True,
            live_order_safety_sm_enabled=True,
            max_concurrent_positions=3,
            safety_sm_allow_mock_capital=True,
        )
        b = build_runtime_bridge(
            output_dir=td, session_id="soak/t1", config=cfg, allow_mock_capital=True
        )
        b.startup()
        b.on_actual_entry(symbol="A.T", price=1000.0, position_id="p1", source_kind=ENTRY_SOURCE_ACTUAL)
        b.on_actual_exit(
            symbol="A.T", position_id="p1", exit_reason="stop_hit", source_kind=EXIT_SOURCE_ACTUAL
        )
        path = write_soak_session_snapshot(
            b, output_dir=td, canonical_entry_count=1, canonical_exit_count=1
        )
        snap = json.loads(path.read_text(encoding="utf-8"))
        assert snap["mapping"]["missing_intent_count"] == 0
        assert snap["safety"]["actual_broker_submit_count"] == 0
        assert snap["flags"]["live_trading_enabled"] is False
