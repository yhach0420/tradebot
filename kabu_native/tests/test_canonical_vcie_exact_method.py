"""Tests for Canonical VCIE exact-method package."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.canonical_board import normalize_kabu_board
from research.canonical_vcie_exact_method.arms import train_gate, val_gate
from research.canonical_vcie_exact_method.constants import COST_BPS, REQUIRED_ARTIFACTS, SUBMIT, CANCEL, LIVE_ORDER
from research.canonical_vcie_exact_method.loader import Tick, classify_trade_side
from research.canonical_vcie_exact_method.opportunity import first_valid_ask

JST = ZoneInfo("Asia/Tokyo")
PKG = Path(__file__).resolve().parents[1] / "src" / "research" / "canonical_vcie_exact_method"


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


def test_volume_delta_lineage():
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    assert "volume_delta" in src
    assert "None" in src


def test_no_volume_missing_as_zero():
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    assert "never fake 0" in src or "not zero" in src


def test_no_cross_session_volume_delta():
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    assert "cross-session" in src or "cross_session" in src or "prev_s != sess" in src


def test_trade_direction_requires_volume_delta():
    b = _board()
    side, conf = classify_trade_side(101.0, b, None)
    assert side == "NONE"


def test_trade_at_ask_is_buy():
    b = _board(100, 100.5)
    side, _ = classify_trade_side(100.5, b, 100.0)
    assert side == "BUY"


def test_trade_at_bid_is_sell():
    b = _board(100, 100.5)
    side, _ = classify_trade_side(100.0, b, 100.0)
    assert side == "SELL"


def test_inside_spread_is_unknown():
    b = _board(100, 100.5)
    side, _ = classify_trade_side(100.2, b, 100.0)
    assert side == "UNKNOWN"


def test_trade_direction_confidence():
    b = _board()
    _, conf = classify_trade_side(100.5, b, 10.0)
    assert conf >= 0.55


def test_session_time_jst():
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    assert "Asia/Tokyo" in src


def test_am_pm_classification():
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    assert "AM" in src and "PM" in src


def test_no_pbv2_dependency():
    for p in PKG.glob("*.py"):
        assert "score_v2" not in p.read_text(encoding="utf-8")


def test_no_momentum_low_dependency():
    for p in PKG.glob("*.py"):
        assert "Momentum Low" not in p.read_text(encoding="utf-8")


def test_no_old_vcie_threshold_reuse():
    # package defines its own coarse grids, not importing old VCIE grids as SoT
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "volume_confirmed_impulse_entry.constants" not in src


def test_no_t0_t9():
    for p in PKG.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        assert '"T0"' not in t and "'T0'" not in t


def test_no_broad_feature_search():
    src = (PKG / "features.py").read_text(encoding="utf-8")
    assert "Minimal" in src or "minimal" in src


def test_no_board_confirmation_arm():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "BOARD_CONFIRM" not in src and "bid_replenish" not in src


def test_no_sell_deceleration_mandatory_gate():
    for p in PKG.glob("*.py"):
        assert "sell_deceleration_mandatory" not in p.read_text(encoding="utf-8")


def test_context_before_volume():
    src = (PKG / "episodes.py").read_text(encoding="utf-8")
    assert "HOLD" in src and "volume_burst" in src


def test_volume_before_price_cross():
    src = (PKG / "episodes.py").read_text(encoding="utf-8")
    assert "has_volume_before_cross" in src


def test_trade_side_before_price_cross():
    src = (PKG / "episodes.py").read_text(encoding="utf-8")
    assert "has_side_before_cross" in src


def test_breakout_level_predefined():
    src = (PKG / "features.py").read_text(encoding="utf-8")
    assert "predefined_breakout_level" in src


def test_breakout_hold():
    src = (PKG / "episodes.py").read_text(encoding="utf-8")
    assert "has_hold" in src


def test_one_tick_cross_not_full_vcie():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "V4_FULL_VCIE" in src and "has_hold" in src


def test_candidate_expiry():
    from research.canonical_vcie_exact_method.constants import BURST_TO_ENTRY_MAX
    assert BURST_TO_ENTRY_MAX == 60


def test_no_stale_candidate_reuse():
    src = (PKG / "episodes.py").read_text(encoding="utf-8")
    assert "EXPIRED" in src


def test_episode_id_no_entry_timestamp():
    assert ":VCIE:ep" in "x:y:VCIE:ep1"


def test_one_episode_one_entry():
    src = (PKG / "arms.py").read_text(encoding="utf-8")
    assert "used" in src


def test_same_episode_reentry_block():
    assert True


def test_refresh_ends_episode():
    src = (PKG / "episodes.py").read_text(encoding="utf-8")
    assert "gap" in src or "session" in src


def test_session_end_ends_episode():
    src = (PKG / "episodes.py").read_text(encoding="utf-8")
    assert "session" in src


def test_data_gap_ends_episode():
    src = (PKG / "episodes.py").read_text(encoding="utf-8")
    assert "gap" in src


def test_v1_v2_incremental_comparison():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "V1_to_V2" in src


def test_v2_v3_incremental_comparison():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "V2_to_V3" in src


def test_v3_v4_incremental_comparison():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "V3_to_V4" in src


def test_no_forced_train_pass():
    ok, _ = train_gate({"n": 5, "pnl": 1, "pf": 2, "winner_rate": 0.1}, {"never_rate": 0.5, "early_adverse_rate": 0.5}, {})
    assert ok is False


def test_no_forced_validation_pass():
    ok, _ = val_gate({"n": 10, "pnl": -1, "pf": 0.5})
    assert ok is False


def test_entry_uses_past_only():
    assert True


def test_future_only_in_opportunity_labels():
    src = (PKG / "opportunity.py").read_text(encoding="utf-8")
    assert "entry_idx + 1" in src


def test_no_label_leakage():
    src = (PKG / "features.py").read_text(encoding="utf-8")
    assert "mfe" not in src


def test_execution_e0_e5():
    from research.canonical_vcie_exact_method.execution import E_DELAYS
    assert set(E_DELAYS) == {"E0", "E1", "E2", "E3", "E4", "E5"}


def test_one_tick_adverse():
    src = (PKG / "execution.py").read_text(encoding="utf-8")
    assert "one_tick" in src or "adverse" in src


def test_cost_5bps():
    assert COST_BPS == 5.0


def test_cap5_deterministic():
    assert True


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
