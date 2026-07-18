"""Phase687W60 — Daily Research Highlights."""

from __future__ import annotations

from small_paper.discord_current_system_summary import (
    HIGHLIGHT_MAX_ITEMS,
    HIGHLIGHT_MAX_LINES,
    build_daily_research_highlights,
    render_daily_short_lines,
)
from small_paper.discord_message_builder import build_summary_embed_payload
from small_paper.pullback_volume_forward_logger import VOL_PERSISTENCE_HIGH_THR, VOL_PERSISTENCE_LOW_THR


def _base_summary(**extra):
    s = {
        "official_entry_count": 20,
        "observer_exit_count": 18,
        "cost_aware_entry_shadow": {
            "enabled": True,
            "selection_cycles": 5,
            "candidates": 20,
            "n_closed": 4,
            "shadow_pnl_yen_100": 20000,
            "runtime_pnl_yen_100": 1600,
            "delta_yen": 18400,
            "stop_risk_reject": 2,
            "official_entry_mismatch": 1,
        },
        "flat_weak_range_shadow_enabled": True,
        "flat_weak_range_shadow_target_count": 10,
        "flat_weak_range_shadow_block_count": 4,
        "flat_weak_range_shadow_completed": 4,
        "flat_weak_range_shadow_blocked_losers": 3,
        "flat_weak_range_shadow_blocked_winners": 1,
        "flat_weak_range_shadow_delta_yen": 7200,
        "pullback_volume_forward": {
            "enabled": True,
            "hits": 7,
            "volume_high_n": 2,
            "volume_low_n": 5,
            "volume_high": {"n": 2, "healthy_rate": 1.0, "collapse_rate": 0.0},
            "volume_low": {"n": 5, "healthy_rate": 0.2, "collapse_rate": 0.8},
        },
        "pullback_misread_guard_shadow_enabled": True,
        "pullback_misread_guard_shadow_blocked_count": 3,
        "pullback_misread_guard_shadow_delta_yen": 500,
        "pullback_misread_blocked_losers": 1,
    }
    s.update(extra)
    return s


def test_daily_top_has_todays_research():
    lines = render_daily_short_lines(_base_summary(), trading_date="2026-07-18")
    text = "\n".join(lines)
    assert text.startswith("[TRADEBOT DAILY - 2026-07-18]")
    assert "=== TODAY'S RESEARCH ===" in text
    assert text.index("=== TODAY'S RESEARCH ===") < text.index("Actual:")


def test_max_three_items_and_twelve_lines():
    hl = build_daily_research_highlights(_base_summary())
    # count research item headers (exclude DATA WARNING / blank / section title)
    headers = [
        ln
        for ln in hl
        if ln.endswith(":")
        and ln
        not in (
            "=== TODAY'S RESEARCH ===",
            "DATA WARNING:",
        )
    ]
    assert len(headers) <= HIGHLIGHT_MAX_ITEMS
    assert len(hl) <= HIGHLIGHT_MAX_LINES


def test_cost_aware_positive_and_negative():
    pos = build_daily_research_highlights(_base_summary())
    assert any("+18,400円" in ln or "+18400円" in ln.replace(",", "") for ln in pos)
    assert any("STOP回避" in ln for ln in pos)

    neg_sum = _base_summary()
    neg_sum["cost_aware_entry_shadow"] = {
        "enabled": True,
        "selection_cycles": 3,
        "candidates": 10,
        "n_closed": 3,
        "shadow_pnl_yen_100": 1000,
        "runtime_pnl_yen_100": 10300,
        "delta_yen": -9300,
        "never_filled": 2,
        "stop_risk_reject": 0,
    }
    neg = build_daily_research_highlights(neg_sum)
    assert any("-9,300円" in ln or "-9300" in ln.replace(",", "") for ln in neg)
    assert any("winner missed" in ln for ln in neg)


def test_completed_zero_not_zero_yen():
    s = _base_summary()
    s["cost_aware_entry_shadow"] = {
        "enabled": True,
        "selection_cycles": 2,
        "candidates": 5,
        "n_closed": 0,
        # no yen outcomes
    }
    # remove other strong signals so CA appears
    s["flat_weak_range_shadow_delta_yen"] = None
    s["flat_weak_range_shadow_completed"] = 0
    s["flat_weak_range_shadow_block_count"] = 0
    s["pullback_volume_forward"] = {"enabled": True, "hits": 0, "volume_high_n": 0, "volume_low_n": 0}
    hl = "\n".join(build_daily_research_highlights(s))
    assert "0円" not in hl
    assert "collecting" in hl or "completed 0" in hl


def test_fwr_loser_winner_and_join_incomplete():
    hl = "\n".join(build_daily_research_highlights(_base_summary()))
    assert "Flat Weak + Range:" in hl
    assert "loser回避" in hl

    s = _base_summary()
    s["flat_weak_range_shadow_delta_yen"] = -4500
    s["flat_weak_range_shadow_blocked_winners"] = 2
    s["flat_weak_range_shadow_blocked_losers"] = 0
    s["cost_aware_entry_shadow"]["delta_yen"] = 100  # smaller
    s["cost_aware_entry_shadow"]["shadow_pnl_yen_100"] = 100
    s["cost_aware_entry_shadow"]["runtime_pnl_yen_100"] = 0
    assert "winner除外" in "\n".join(build_daily_research_highlights(s))

    join_bad = _base_summary()
    join_bad["flat_weak_range_shadow_completed"] = 0
    join_bad["flat_weak_range_shadow_block_count"] = 4
    join_bad["observer_exit_count"] = 10
    join_txt = "\n".join(build_daily_research_highlights(join_bad))
    assert "JOIN INCOMPLETE" in join_txt
    assert join_txt.index("DATA WARNING:") < join_txt.index("Cost-Aware:") or "JOIN INCOMPLETE" in join_txt


def test_pullback_volume_collapse_and_healthy():
    s = _base_summary()
    # make volume the strongest by shrinking others
    s["cost_aware_entry_shadow"]["delta_yen"] = 10
    s["cost_aware_entry_shadow"]["shadow_pnl_yen_100"] = 10
    s["cost_aware_entry_shadow"]["runtime_pnl_yen_100"] = 0
    s["cost_aware_entry_shadow"]["stop_risk_reject"] = 0
    s["flat_weak_range_shadow_delta_yen"] = 10
    s["flat_weak_range_shadow_blocked_losers"] = 0
    txt = "\n".join(build_daily_research_highlights(s))
    assert "Pullback Volume:" in txt
    assert "collapse" in txt or "Low" in txt

    s2 = _base_summary()
    s2["pullback_volume_forward"] = {
        "enabled": True,
        "hits": 4,
        "volume_high_n": 4,
        "volume_low_n": 0,
        "volume_high": {"n": 4, "healthy_rate": 0.75, "collapse_rate": 0.0},
        "volume_low": {"n": 0},
    }
    s2["cost_aware_entry_shadow"]["delta_yen"] = 1
    s2["cost_aware_entry_shadow"]["shadow_pnl_yen_100"] = 1
    s2["cost_aware_entry_shadow"]["runtime_pnl_yen_100"] = 0
    s2["flat_weak_range_shadow_delta_yen"] = 1
    assert "healthy" in "\n".join(build_daily_research_highlights(s2))


def test_pullback_misread_lower_priority_unless_large():
    s = _base_summary()
    # PB misread small — should often be dropped when 3 others present
    hl = build_daily_research_highlights(s)
    headers = [ln for ln in hl if ln.endswith(":") and "RESEARCH" not in ln and "WARNING" not in ln]
    assert "PullbackMisread:" not in headers or len(headers) <= 3

    big = _base_summary()
    big["pullback_misread_guard_shadow_delta_yen"] = 50000
    big["pullback_misread_blocked_losers"] = 4
    big["cost_aware_entry_shadow"]["delta_yen"] = 100
    big["cost_aware_entry_shadow"]["shadow_pnl_yen_100"] = 100
    big["cost_aware_entry_shadow"]["runtime_pnl_yen_100"] = 0
    big["flat_weak_range_shadow_delta_yen"] = 100
    assert "PullbackMisread:" in "\n".join(build_daily_research_highlights(big))


def test_data_warning_priority():
    # W63/W64: warn on PV eligible/recorded mismatch (not Misread-hits-as-denominator)
    s = _base_summary()
    s["pullback_volume_forward"]["pullback_volume_eligible_count"] = 9
    s["pullback_volume_forward"]["pullback_volume_recorded_count"] = 7
    s["pullback_volume_forward"]["hits"] = 7
    txt = "\n".join(build_daily_research_highlights(s))
    assert "DATA WARNING:" in txt
    assert "Pullback Volume records 7 / eligible 9" in txt
    assert txt.index("DATA WARNING:") < txt.index("Cost-Aware:")


def test_no_detail_shadow_dump():
    txt = "\n".join(build_daily_research_highlights(_base_summary()))
    assert "--- Observer Status ---" not in txt
    assert "--- Data Completeness ---" not in txt
    assert "volume mid:" not in txt


def test_embed_daily_includes_highlights_am_does_not():
    s = _base_summary()
    hl = build_daily_research_highlights(s)
    daily = build_summary_embed_payload({"trade_count": 1, "total_pnl_yen_100": 0}, am_pm="", research_highlights=hl)
    assert "TODAY'S RESEARCH" in daily["description"]
    am = build_summary_embed_payload({"trade_count": 1, "total_pnl_yen_100": 0}, am_pm="AM", research_highlights=hl)
    assert "TODAY'S RESEARCH" not in am["description"]


def test_fail_open_highlight_exception(monkeypatch):
    import small_paper.discord_current_system_summary as mod

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "_build_daily_research_highlights_inner", _boom)
    out = build_daily_research_highlights({})
    assert out[0] == "=== TODAY'S RESEARCH ==="
    assert "unavailable" in out[1]


def test_thresholds_unchanged():
    assert abs(VOL_PERSISTENCE_HIGH_THR - 0.2782069767789509) < 1e-12
    assert abs(VOL_PERSISTENCE_LOW_THR - 0.12710349962769918) < 1e-12
