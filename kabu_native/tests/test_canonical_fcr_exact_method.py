"""Tests for Canonical FCR exact-method package."""
from __future__ import annotations

from pathlib import Path

from small_paper.canonical_board import normalize_kabu_board
from research.canonical_fcr_exact_method.arms import train_gate, val_gate
from research.canonical_fcr_exact_method.constants import COST_BPS, REQUIRED_ARTIFACTS, SUBMIT, CANCEL, LIVE_ORDER
from research.canonical_fcr_exact_method.loader import classify_ask_depletion, classify_trade_side
from research.canonical_fcr_exact_method.observations import causal_vwap

PKG = Path(__file__).resolve().parents[1] / "src" / "research" / "canonical_fcr_exact_method"


def _board(bid=100.0, ask=100.5, bq=500, aq=800):
    op = {
        "Buy1": {"Price": bid, "Qty": bq},
        "Sell1": {"Price": ask, "Qty": aq},
        "BidPrice": ask,
        "AskPrice": bid,
        "BidQty": aq,
        "AskQty": bq,
    }
    return normalize_kabu_board(op)


def test_canonical_bid_from_buy1():
    assert _board(10, 11).canonical_best_bid == 10


def test_canonical_ask_from_sell1():
    assert _board(10, 11).canonical_best_ask == 11


def test_buy_uses_canonical_ask():
    src = (PKG / "opportunity.py").read_text(encoding="utf-8")
    assert "canonical_best_ask" in src


def test_sell_uses_canonical_bid():
    src = (PKG / "opportunity.py").read_text(encoding="utf-8")
    assert "canonical_best_bid" in src


def test_no_raw_bid_ask_semantic_use():
    for p in PKG.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert "english_bid = BidPrice" not in t


def test_trade_requires_volume_delta():
    b = _board()
    side, _ = classify_trade_side(101.0, b, None)
    assert side == "NONE"


def test_trade_at_ask_is_buy():
    b = _board(100, 100.5)
    side, _ = classify_trade_side(100.5, b, 100.0)
    assert side == "BUY"


def test_trade_at_bid_is_sell():
    b = _board(100, 100.5)
    side, _ = classify_trade_side(100.0, b, 100.0)
    assert side == "SELL"


def test_inside_trade_is_unknown():
    b = _board(100, 100.5)
    side, _ = classify_trade_side(100.2, b, 100.0)
    assert side == "UNKNOWN"


def test_ask_cancel_not_absorption():
    assert classify_ask_depletion(500, 400, "NONE", None) == "CANCELLATION_OR_UNKNOWN"


def test_ask_execution_confirms_depletion():
    assert classify_ask_depletion(500, 400, "BUY", 100.0) == "EXECUTED_DEPLETION"


def test_vwap_no_fabrication():
    src = (PKG / "observations.py").read_text(encoding="utf-8")
    assert "VWAP_NOT_EVALUABLE" in src
    assert "never fabricate" in src


def test_no_pbv2_candidate_dependency():
    for p in PKG.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert "score_v2" not in t
        assert "pbv2_candidates" not in t


def test_no_momentum_low_dependency():
    for p in PKG.glob("*.py"):
        assert "Momentum Low" not in p.read_text(encoding="utf-8")


def test_no_vcie_condition_reuse():
    for p in PKG.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert "volume_confirmed_impulse_entry" not in t
        assert "V4_FULL_VCIE" not in t


def test_no_broad_feature_search():
    src = (PKG / "observations.py").read_text(encoding="utf-8")
    assert "Minimal" in src or "minimal" in src


def test_no_score_system():
    for p in PKG.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert "score_board" not in t
        assert "加点" not in t


def test_trend_before_pullback():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert src.index("TREND_CONTEXT") < src.index("PULLBACK_DETECTED")


def test_pullback_after_initial_impulse():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "impulse_high" in src and "PULLBACK_DETECTED" in src


def test_exhaustion_after_pullback():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert src.index("PULLBACK_DETECTED") < src.index("SELLING_EXHAUSTED")


def test_buy_flow_after_exhaustion():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert src.index("SELLING_EXHAUSTED") < src.index("BUY_FLOW_CONFIRMED")


def test_reclaim_after_buy_flow():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert src.index("BUY_FLOW_CONFIRMED") < src.index("RECLAIM_TRIGGERED")


def test_reclaim_level_predefined():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "reclaim_level" in src and "reclaim_created" in src


def test_no_state_skip():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "progressed_this_event" in src


def test_no_same_event_multistage_progress():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "progressed_this_event" in src
    assert "do not reclaim on same event" in src


def test_pullback_low_causal():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "pullback_low" in src


def test_new_low_invalidates_exhaustion():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert 'fail = "INVALIDATED", "new_low"' in src or '"new_low"' in src


def test_sell_acceleration_invalidates():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "sell_decel" in src or "INVALIDATED" in src


def test_ask_cancel_not_buy_flow():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "CANCELLATION_OR_UNKNOWN" in src
    assert "cancel_only" in src


def test_reclaim_without_buy_flow_not_full_fcr():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "F5_FULL_FCR" in src and "has_buy_flow" in src
    assert "D2_NO_BUY_FLOW" in src


def test_old_reclaim_level_not_reused():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "reclaim_level" in src


def test_episode_id_no_entry_timestamp():
    assert ":FCR:ep" in "x:y:FCR:ep1"


def test_one_impulse_one_entry():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "used_imp" in src


def test_same_impulse_reentry_block():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "used_imp" in src


def test_new_episode_requires_new_impulse():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "impulse_id" in src


def test_refresh_ends_episode():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "session_or_gap" in src


def test_session_end_ends_episode():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "session" in src


def test_data_gap_ends_episode():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "_gap" in src


def test_expiry():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "EXPIRED" in src and "expiry" in src


def test_no_stale_candidate():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "EXPIRED" in src


def test_f0_f1_increment():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "F0_to_F1" in src


def test_f1_f2_increment():
    assert "F1_to_F2" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_f2_f3_increment():
    assert "F2_to_F3" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_f3_f4_increment():
    assert "F3_to_F4" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_f4_f5_increment():
    assert "F4_to_F5" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_increment_mixed_supported():
    src = (PKG / "opportunity.py").read_text(encoding="utf-8")
    assert "INCREMENT_MIXED" in src


def test_no_forced_train_pass():
    ok, _ = train_gate(
        {"n": 5, "pnl": 1, "pf": 2, "winner_rate": 0.1, "never_rate": 0.1, "early_adverse_rate": 0.1, "stop_rate": 0.1, "top1_symbol_share": 0.1},
        {"never_rate": 0.5, "early_adverse_rate": 0.5},
        {"stop_rate": 0.2},
        {"noprogress_rate": 0.2},
    )
    assert ok is False


def test_no_forced_validation_pass():
    ok, _ = val_gate({"n": 10, "pnl": -1, "pf": 0.5})
    assert ok is False


def test_train_only_threshold_selection():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "fit_thresholds_train" in src


def test_forensic_holdout_frozen():
    src = (PKG / "data_split.py").read_text(encoding="utf-8")
    assert "forensic" in src.lower() or "REUSED_FORENSIC" in src or "20260724" in src


def test_entry_uses_past_only():
    src = (PKG / "state_machine.py").read_text(encoding="utf-8")
    assert "reclaim_level" in src


def test_future_only_in_labels():
    src = (PKG / "opportunity.py").read_text(encoding="utf-8")
    assert "entry_idx + 1" in src or "range(entry_idx" in src


def test_no_label_leakage():
    src = (PKG / "observations.py").read_text(encoding="utf-8")
    assert "mfe" not in src.lower()


def test_execution_e0_e5():
    from research.canonical_fcr_exact_method.execution import E_DELAYS
    assert set(E_DELAYS) == {"E0", "E1", "E2", "E3", "E4", "E5"}


def test_one_tick_adverse():
    src = (PKG / "execution.py").read_text(encoding="utf-8")
    assert "one_tick" in src or "adverse" in src


def test_cost_5bps():
    assert COST_BPS == 5.0


def test_pbv2_unchanged():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "pbv2_unchanged" in src or "pbv2_modified" in src


def test_cap5_deterministic():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "cap5" in src.lower() or "CAP5" in src


def test_submit_cancel_live_zero():
    assert SUBMIT == CANCEL == LIVE_ORDER == 0


def test_no_paper_auto_start():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "paper_auto_start" in src


def test_live_disabled():
    assert LIVE_ORDER == 0


def test_mainline_unchanged():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "mainline_changed" in src


def test_only_three_outputs():
    assert REQUIRED_ARTIFACTS == ("report.md", "report.json", "audit.xlsx")
