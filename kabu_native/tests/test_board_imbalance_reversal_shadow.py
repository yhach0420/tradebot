"""Board Imbalance Reversal (H_board_ts) unit tests."""

from __future__ import annotations

from small_paper.board_imbalance_reversal_shadow import (
    SOT_THRESHOLD,
    BoardImbalanceReversalState,
    evaluate_h_board_ts,
    note_accepted,
    note_exit,
    resolve_board_imbalance_reversal_enabled,
    summary_fields,
)
from small_paper.shadow_registry import discord_inventory_from_registry, get_shadow_def


def test_threshold_sot():
    assert SOT_THRESHOLD == -0.038599


def test_paper_enable():
    assert resolve_board_imbalance_reversal_enabled(
        is_paper_runtime=True, env_value=None
    ).enabled
    assert not resolve_board_imbalance_reversal_enabled(
        is_paper_runtime=True, env_value="0"
    ).enabled
    assert not resolve_board_imbalance_reversal_enabled(
        is_paper_runtime=False, env_value="1"
    ).enabled


def test_boundary_and_fail_open():
    assert evaluate_h_board_ts({"f_np_imb_chg_60": -0.038599})["would_reject"]
    assert not evaluate_h_board_ts({"f_np_imb_chg_60": -0.038598})["would_reject"]
    miss = evaluate_h_board_ts({})
    assert miss["fail_open"] and not miss["would_reject"]
    assert miss["f_np_imb_chg_60"] is None


def test_counterfactual_delta():
    st = BoardImbalanceReversalState(enabled=True, threshold=SOT_THRESHOLD)
    note_accepted(
        st,
        symbol="7203.T",
        trade={"f_np_imb_chg_60": -0.05, "position_id": "p1", "entry_time": "t1"},
        position_id="p1",
        entry_time="t1",
        entry_price=1000,
    )
    assert st.would_reject == 1
    note_exit(st, {"position_id": "p1", "symbol": "7203.T", "entry_time": "t1", "pnl_yen_100": -500.0, "exit_reason": "stop"})
    assert st.closed == 1
    assert st.counterfactual_delta_yen_100 == 500.0  # 0 - (-500)
    assert st.stop_avoided == 1
    s = summary_fields(st)
    assert s["board_imbalance_reversal_shadow_enabled"] is True
    assert s["board_imbalance_reversal_would_reject"] == 1


def test_registry_and_discord():
    d = get_shadow_def("board_imbalance_reversal_shadow")
    assert d is not None
    assert d["management_class"] == "TEMP_FORWARD"
    assert d.get("discord_visible") is False
    inv = discord_inventory_from_registry()
    assert len(inv) == 3
    assert "board_imbalance_reversal_shadow" not in {x["canonical_shadow_id"] for x in inv}


def test_old_board_imbalance_still_retired():
    old = get_shadow_def("board_imbalance_shadow")
    assert old is not None
    assert old["management_class"] == "RETIRED"
