"""Phase723: Cost-Aware AM pipeline — runtime-compatible join + Discord labels."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from small_paper.cost_aware_entry_shadow import (
    CostAwareShadowState,
    ShadowPosition,
    attach_runtime_compatible_to_closed_trades,
    finalize_open_positions,
    summarize_state,
)
from small_paper.cost_aware_entry_v2_shadow import (
    CostAwareV2ShadowState,
    finalize_pending_exits,
    format_discord_lines,
    note_accepted_candidate,
    summarize_state as summarize_v2,
)
from small_paper.discord_message_builder import collect_active_shadow_observations

JST = ZoneInfo("Asia/Tokyo")


def _pos(sym: str, t0: datetime, px: float) -> ShadowPosition:
    return ShadowPosition(
        symbol=sym,
        entry_time=t0,
        entry_price=px,
        selection_cycle_id="c1",
        rank=1,
        integrated_score=1.0,
        winner_enrichment=0.0,
        stop_risk=0.0,
        stop_margin_z=0.0,
        pbv2_score=1.0,
    )


def test_force_close_runtime_compatible_filled():
    st = CostAwareShadowState()
    t0 = datetime(2026, 7, 23, 10, 0, tzinfo=JST)
    force = datetime(2026, 7, 23, 11, 25, tzinfo=JST)
    st.open_shadow["AAA.T"] = _pos("AAA.T", t0, 1000.0)
    path = [(t0, 1000.0), (force - timedelta(minutes=1), 1010.0)]
    finalize_open_positions(
        st,
        force_close_time=force,
        trading_date="20260723",
        price_paths={"AAA.T": path},
        is_freeze_recovery=False,
    )
    exits = [(force, "ZZZ.T", 2000.0, "morning_session_close")]
    enriched, stats = attach_runtime_compatible_to_closed_trades(
        st.closed_trades,
        official_exits=exits,
        price_paths={"AAA.T": path},
        force_close_time=force,
    )
    st.closed_trades = enriched
    s = summarize_state(st)
    assert s["n_open"] == 0
    assert s["join_success_count"] == 1
    assert s["runtime_compatible_pnl"] is not None
    assert s["delta_total_5bps"] is not None
    assert s["pf_delta_5bps"] is not None or s["runtime_pf_5bps"] is not None
    assert stats["join_failed_count"] == 0


def test_freeze_recovery_finalize_fills_runtime():
    st = CostAwareShadowState()
    t0 = datetime(2026, 7, 23, 10, 0, tzinfo=JST)
    force = datetime(2026, 7, 23, 11, 25, tzinfo=JST)
    st.open_shadow["BBB.T"] = _pos("BBB.T", t0, 2000.0)
    path = [(t0, 2000.0), (force, 1990.0)]
    finalize_open_positions(
        st,
        force_close_time=force,
        trading_date="20260723",
        price_paths={"BBB.T": path},
        is_freeze_recovery=True,
    )
    enriched, _ = attach_runtime_compatible_to_closed_trades(
        st.closed_trades,
        official_exits=[],
        price_paths={"BBB.T": path},
        force_close_time=force,
    )
    assert enriched[0]["runtime_compatible_na"] is False
    assert enriched[0]["join_status"] == "CLOSED_READY"


def test_join_failure_explicit_no_price():
    row = {
        "symbol": "CCC.T",
        "shadow_entry_time": "2026-07-23T10:00:00+09:00",
        "shadow_entry_price": 1000.0,
        "gross_pnl_yen_100": 0.0,
        "net_pnl_yen_100": -50.0,
        "shadow_exit_price": 1000.0,
        "shadow_exit_price_source": "N/A_NO_PRICE_PATH",
    }
    force = datetime(2026, 7, 23, 11, 25, tzinfo=JST)
    enriched, stats = attach_runtime_compatible_to_closed_trades(
        [row],
        official_exits=[],
        price_paths={},
        force_close_time=force,
    )
    assert stats["join_failed_count"] == 1
    assert enriched[0]["join_failure_reason"] == "NO_PRICE_PATH"
    assert enriched[0]["runtime_compatible_gross_yen"] is None


def test_pending_not_zero_yen_v2():
    st = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": 9.0})
    note_accepted_candidate(
        st,
        symbol="P.T",
        trade={},
        np_row={"np_imb_chg_60s": 0.0},
        position_id="pend1",
        entry_time="2026-07-23T10:00:00+09:00",
        entry_price=200,
    )
    block = summarize_v2(st)
    assert block["pending_count"] == 1
    assert block["H_board_ts"]["delta_5bps"] is None
    assert block["H_board_ts"]["runtime_pnl_5bps"] is None


def test_v2_soft_join_by_symbol():
    st = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": 9.0})
    note_accepted_candidate(
        st,
        symbol="8058.T",
        trade={},
        np_row={"np_imb_chg_60s": 0.1},
        position_id="",  # force symbol|entry_time key
        entry_time="2026-07-23T09:03:00+09:00",
        entry_price=4895.0,
    )
    stats = finalize_pending_exits(
        st,
        [
            {
                "position_id": "8058.T_20260723T090304000000",
                "symbol": "8058.T",
                "entry_time": "2026-07-23T09:03:04+09:00",
                "exit_price": 4906.0,
                "actual_pnl_yen_100": 1100.0,
                "exit_reason": "no_progress_exit",
            }
        ],
        session_force_close=True,
    )
    assert stats["join_success_count"] == 1
    assert stats["pending_count"] == 0
    block = summarize_v2(st)
    assert block["join_success_count"] == 1
    assert block["runtime_total_5bps"] is not None
    assert block["pf_delta_5bps"] is not None or block["runtime_pf_5bps"] is not None


def test_cost_aware_discord_not_reuse_block_label():
    summary = {
        "cost_aware_entry_shadow_enabled": True,
        "cost_aware_shadow_entries_proxy": 120,
        "cost_aware_virtual_entry_count": 120,
        "cost_aware_evaluable_count": 118,
        "cost_aware_real_block_count": 0,
        "cost_aware_delta_proxy": 100.0,
        "cost_aware_entry_shadow_pf_delta": 0.12,
        "cost_aware_entry_shadow": {
            "enabled": True,
            "shadow_entries": 120,
            "virtual_entry_count": 120,
            "evaluable_count": 118,
            "n_closed": 118,
            "status": "CLOSED_READY",
            "pf_delta_5bps": 0.12,
            "delta_total_5bps": 100.0,
        },
    }
    rows = collect_active_shadow_observations(summary)
    ca = next(r for r in rows if r["name"] == "Cost-Aware")
    assert ca["real_block_count"] == 0
    assert ca["block_count"] == 0
    assert ca["virtual_entry_count"] == 120
    assert ca["delta"] != "N/A"


def test_v2_discord_observe_only_and_am_title():
    lines = format_discord_lines(
        {
            "cost_aware_entry_v2_shadow": {
                "enabled": True,
                "evaluated_candidates": 47,
                "evaluable_count": 47,
                "join_success_count": 47,
                "join_failed_count": 0,
                "pending_count": 0,
                "fail_open_count": 2,
                "runtime_total_5bps": -1000.0,
                "cost_aware_total_5bps": -800.0,
                "delta_total_5bps": 200.0,
                "runtime_pf_5bps": 0.5,
                "cost_aware_pf_5bps": 0.6,
                "pf_delta_5bps": 0.1,
                "H_board_ts": {"keep": 38, "reject": 9},
                "I_price_board": {"keep": 35, "reject": 12},
                "submit": 0,
                "cancel": 0,
                "live_order": 0,
            }
        },
        am_pm="AM",
    )
    text = "\n".join(lines)
    assert "[Cost-Aware V2 Shadow - AM]" in text
    assert "observation only" in text
    assert "join成功: 47" in text
    assert "実block" not in text


def test_canonical_fields_not_in_v2_block():
    st = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": 9.0})
    block = summarize_v2(st)
    assert block["mainline_pnl_included"] is False
    assert block["canonical_pnl_mixed"] is False
    assert block["submit"] == 0
    assert block["cancel"] == 0
    assert block["live_order"] == 0


def test_delta_eligible_matches_join_success():
    st = CostAwareShadowState()
    t0 = datetime(2026, 7, 23, 9, 0, tzinfo=JST)
    force = datetime(2026, 7, 23, 11, 25, tzinfo=JST)
    st.open_shadow["D.T"] = _pos("D.T", t0, 1000.0)
    path = [(t0, 1000.0), (t0 + timedelta(minutes=10), 1005.0)]
    finalize_open_positions(
        st, force_close_time=force, trading_date="20260723", price_paths={"D.T": path}
    )
    enriched, _ = attach_runtime_compatible_to_closed_trades(
        st.closed_trades,
        official_exits=[(force, "D.T", 1005.0, "morning_session_close")],
        price_paths={"D.T": path},
        force_close_time=force,
    )
    st.closed_trades = enriched
    st.shadow_entries = 1
    s = summarize_state(st)
    assert s["delta_eligible_count"] == s["join_success_count"]
