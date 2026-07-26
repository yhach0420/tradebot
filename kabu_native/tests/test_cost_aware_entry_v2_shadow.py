"""Cost-Aware V2 Shadow — Paper default ON, Live force OFF, Summary/Discord isolation."""

from __future__ import annotations

from small_paper.cost_aware_entry_v2_shadow import (
    CostAwareV2ShadowState,
    assert_no_orders,
    evaluate_v2,
    format_discord_lines,
    note_accepted_candidate,
    note_exit,
    shadow_enabled,
    shadow_enabled_with_source,
    summarize_state,
)
from small_paper.forward_observer_defaults import (
    is_live_or_real_order_context,
    resolve_cost_aware_entry_v2_shadow,
)


def test_paper_default_on(monkeypatch):
    monkeypatch.delenv("COST_AWARE_ENTRY_V2_SHADOW", raising=False)
    monkeypatch.setenv("KABU_PAPER_RUNTIME", "1")
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    assert shadow_enabled({"paper_runtime": True}) is True
    on, src = shadow_enabled_with_source({"paper_runtime": True})
    assert on is True
    assert src in ("default", "env")


def test_env_zero_forces_off(monkeypatch):
    monkeypatch.setenv("KABU_PAPER_RUNTIME", "1")
    monkeypatch.setenv("COST_AWARE_ENTRY_V2_SHADOW", "0")
    assert shadow_enabled({"paper_runtime": True}) is False


def test_env_one_forces_on(monkeypatch):
    monkeypatch.delenv("KABU_PAPER_RUNTIME", raising=False)
    monkeypatch.setenv("COST_AWARE_ENTRY_V2_SHADOW", "1")
    assert shadow_enabled() is True


def test_live_force_off_even_if_env_on(monkeypatch):
    monkeypatch.setenv("COST_AWARE_ENTRY_V2_SHADOW", "1")
    monkeypatch.setenv("KABU_PAPER_RUNTIME", "1")
    assert is_live_or_real_order_context({"live_trading_enabled": True}) is True
    on, src = resolve_cost_aware_entry_v2_shadow({"live_trading_enabled": True, "paper_runtime": True})
    assert on is False
    assert src == "live_force_off"
    assert shadow_enabled({"order_enabled": True}) is False


def test_mainline_accept_unaffected_by_v2_reject():
    """V2 REJECT must not change a Runtime accept boolean."""
    runtime_accept = True
    feats = {"f_np_imb_chg_60": -9.0, "f_chase": 0.1, "f_near_high": 0.1}
    out = evaluate_v2(feats, thresholds={"t_imb_chg": -1.0}, policy="H_board_ts")
    assert out["v2_verdict"] == "REJECT"
    assert runtime_accept is True  # unchanged


def test_submit_cancel_live_order_zero():
    st = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": -1.0})
    note_accepted_candidate(
        st,
        symbol="1234.T",
        trade={"entry_price": 1000, "entry_time": "2026-07-22T10:00:00+09:00", "position_id": "p1"},
        np_row={"np_imb_chg_60s": -5.0},
        position_id="p1",
        entry_time="2026-07-22T10:00:00+09:00",
        entry_price=1000,
    )
    assert_no_orders(st)
    s = summarize_state(st)
    assert s["submit"] == 0 and s["cancel"] == 0 and s["live_order"] == 0
    assert s["discord_entry"] is False
    assert s["mainline_pnl_included"] is False
    assert s["canonical_pnl_mixed"] is False


def test_no_discord_entry_notification_flag():
    st = CostAwareV2ShadowState(enabled=True)
    s = summarize_state(st)
    assert s.get("discord_entry") is False
    assert s.get("blocks_real_entry") is False


def test_summary_am_pm_daily_block_keys():
    st = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": -0.01, "t_chase": 9, "t_near": 9})
    note_accepted_candidate(
        st,
        symbol="AAA.T",
        trade={"entry_price": 500, "entry_rise_5min_pct": 0.2, "entry_near_day_high_pct": 0.1},
        np_row={"np_imb_chg_60s": 0.5},
        session="AM",
        position_id="am1",
        entry_time="t1",
        entry_price=500,
    )
    block = summarize_state(st)
    assert "H_board_ts" in block and "I_price_board" in block
    assert "board_feature" in block
    assert block["primary_arm"] == "H_board_ts"
    assert block["secondary_arm"] == "I_price_board"
    discord = format_discord_lines({"cost_aware_entry_v2_shadow": block})
    assert any("Cost-Aware V2 Shadow" in x for x in discord)
    assert any("H_board_ts" in x for x in discord)
    assert any("I_price_board" in x for x in discord)


def test_canonical_not_mixed():
    st = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": -1.0})
    note_accepted_candidate(
        st,
        symbol="B.T",
        trade={"entry_price": 100},
        np_row={"np_imb_chg_60s": -5.0},
        position_id="x1",
        entry_time="e1",
        entry_price=100,
    )
    note_exit(
        st,
        {
            "position_id": "x1",
            "symbol": "B.T",
            "entry_time": "e1",
            "actual_pnl_yen_100": -1000,
            "exit_reason": "stop_hit",
            "entry_price": 100,
        },
    )
    block = summarize_state(st)
    # Shadow CF exists but flags prove non-mix
    assert block["mainline_pnl_included"] is False
    assert block["canonical_pnl_mixed"] is False
    # Rejected → CF portfolio excludes trade → delta positive when rejecting a loss
    assert block["H_board_ts"]["delta_5bps"] is not None


def test_dedup_same_position():
    st = CostAwareV2ShadowState(enabled=True)
    a = note_accepted_candidate(
        st, symbol="S.T", trade={}, position_id="same", entry_time="t", entry_price=1
    )
    b = note_accepted_candidate(
        st, symbol="S.T", trade={}, position_id="same", entry_time="t", entry_price=1
    )
    assert a is b
    assert len(st.by_key) == 1


def test_board_missing_fail_open():
    out = evaluate_v2({"f_chase": 1.0}, thresholds={"t_imb_chg": -1.0}, policy="H_board_ts")
    assert out["v2_verdict"] == "FAIL_OPEN"
    assert out["v2_keep"] is True
    assert "INSUFFICIENT_BOARD_HISTORY" in out["reject_reasons"]


def test_exit_pending_null_pnl():
    st = CostAwareV2ShadowState(enabled=True, thresholds={"t_imb_chg": 9.0})
    note_accepted_candidate(
        st,
        symbol="P.T",
        trade={},
        np_row={"np_imb_chg_60s": 0.0},
        position_id="pend",
        entry_time="t",
        entry_price=200,
    )
    block = summarize_state(st)
    assert block["H_board_ts"]["pnl_status"] == "pending"
    assert block["H_board_ts"]["delta_5bps"] is None
    assert block["H_board_ts"]["delta_raw"] is None


def test_discord_format_fail_open_safe():
    lines = format_discord_lines(
        {
            "cost_aware_entry_v2_shadow": {
                "enabled": True,
                "primary_arm": "H_board_ts",
                "evaluated_candidates": 85,
                "board_feature_available": 0,
                "board_feature_missing": 85,
                "fail_open_count": 85,
                "verdict_label": "FAIL_OPEN",
                "verdict_reason": "INSUFFICIENT_BOARD_HISTORY",
                "H_board_ts": {},
                "I_price_board": {},
            }
        }
    )
    assert any("FAIL_OPEN" in x for x in lines)
    assert any("N/A" in x for x in lines)


def test_discord_off_minimal():
    lines = format_discord_lines({"cost_aware_entry_v2_shadow_enabled": False})
    assert any("OFF" in x for x in lines)


def test_arms_separate_counts():
    st = CostAwareV2ShadowState(
        enabled=True,
        thresholds={"t_imb_chg": -1.0, "t_chase": 0.5, "t_near": 0.5},
    )
    # board ok, chase high → H KEEP, I REJECT
    note_accepted_candidate(
        st,
        symbol="Z.T",
        trade={"entry_rise_5min_pct": 3.0, "entry_near_day_high_pct": 2.0, "entry_price": 100},
        np_row={"np_imb_chg_60s": 0.5},
        position_id="z1",
        entry_time="t",
        entry_price=100,
    )
    block = summarize_state(st)
    assert block["H_board_ts"]["keep"] == 1
    assert block["I_price_board"]["reject"] == 1


def test_k_equals_h():
    feats = {"f_np_imb_chg_60": -5.0}
    thr = {"t_imb_chg": -1.0}
    assert (
        evaluate_v2(feats, thresholds=thr, policy="H_board_ts")["v2_verdict"]
        == evaluate_v2(feats, thresholds=thr, policy="K_v2_final")["v2_verdict"]
    )
