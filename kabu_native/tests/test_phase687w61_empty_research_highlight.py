"""Phase687W61 — suppress empty / title-only Daily research highlights."""

from __future__ import annotations

from small_paper.discord_current_system_summary import (
    HIGHLIGHT_MAX_ITEMS,
    HIGHLIGHT_MAX_LINES,
    _has_meaningful_text,
    build_daily_research_highlights,
    build_fwr_daily_highlight,
    rank_research_highlights,
)
from small_paper.discord_message_builder import build_summary_embed_payload
from small_paper.pullback_volume_forward_logger import VOL_PERSISTENCE_HIGH_THR, VOL_PERSISTENCE_LOW_THR


def _ca(**kw):
    base = {
        "enabled": True,
        "selection_cycles": 3,
        "candidates": 10,
        "n_closed": 2,
        "shadow_pnl_yen_100": 20000,
        "runtime_pnl_yen_100": 1600,
        "delta_yen": 18400,
        "stop_risk_reject": 2,
    }
    base.update(kw)
    return base


def test_has_meaningful_text():
    assert _has_meaningful_text(None) is False
    assert _has_meaningful_text("") is False
    assert _has_meaningful_text(" ") is False
    assert _has_meaningful_text("\n") is False
    assert _has_meaningful_text([]) is False
    assert _has_meaningful_text({}) is False
    assert _has_meaningful_text("ok") is True
    assert _has_meaningful_text(["", "x"]) is True


def test_fwr_title_only_never_emitted():
    # empty body path must not produce title-only
    assert build_fwr_daily_highlight({"flat_weak_range_shadow_enabled": True}) is None
    assert build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 0,
            "flat_weak_range_shadow_block_count": 0,
            "flat_weak_range_shadow_completed": 0,
        }
    ) is None
    hl = build_daily_research_highlights(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 0,
            "cost_aware_entry_shadow": _ca(),
        }
    )
    text = "\n".join(hl)
    assert "Flat Weak + Range:" not in text


def test_empty_body_excluded_from_rank():
    items = [
        {"title": "A:", "body": "", "score": 100},
        {"title": "B:", "body": None, "score": 90},
        {"title": "C:", "body": "   ", "score": 80},
        {"title": "D:", "body": "ok", "score": 10},
    ]
    ranked = rank_research_highlights(items, max_items=3)
    assert len(ranked) == 1
    assert ranked[0]["title"] == "D:"


def test_fwr_fallbacks():
    pending = build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 8,
            "flat_weak_range_shadow_block_count": 4,
            "flat_weak_range_shadow_completed": 0,
        }
    )
    assert pending is not None
    assert pending["body"] == "would block 4件 / outcome pending"

    pos = build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 8,
            "flat_weak_range_shadow_completed": 3,
            "flat_weak_range_shadow_delta_yen": 7200,
            "flat_weak_range_shadow_blocked_losers": 3,
        }
    )
    assert pos is not None
    assert "+7,200円" in pos["body"] or "+7200" in pos["body"].replace(",", "")
    assert "loser回避" in pos["body"]

    neg = build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 8,
            "flat_weak_range_shadow_completed": 2,
            "flat_weak_range_shadow_delta_yen": -4500,
            "flat_weak_range_shadow_blocked_winners": 2,
        }
    )
    assert neg is not None
    assert "winner除外" in neg["body"]

    join = build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 4,
            "flat_weak_range_shadow_block_count": 4,
            "flat_weak_range_shadow_completed": 0,
            "observer_exit_count": 5,
        }
    )
    assert join is not None
    assert join["body"] == "JOIN INCOMPLETE / 要確認"

    collecting = build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 3,
            "flat_weak_range_shadow_block_count": 0,
            "flat_weak_range_shadow_completed": 0,
        }
    )
    assert collecting is not None
    assert collecting["body"] == "collecting"


def test_empty_fwr_promotes_pullback_misread():
    hl = build_daily_research_highlights(
        {
            "cost_aware_entry_shadow": _ca(delta_yen=100, shadow_pnl_yen_100=100, runtime_pnl_yen_100=0, stop_risk_reject=0),
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 0,
            "pullback_volume_forward": {
                "enabled": True,
                "hits": 2,
                "volume_high_n": 1,
                "volume_low_n": 1,
                "volume_high": {"n": 1, "healthy_rate": 1.0},
                "volume_low": {"n": 1, "collapse_rate": 0.0},
            },
            "pullback_misread_guard_shadow_enabled": True,
            "pullback_misread_guard_shadow_blocked_count": 2,
            "pullback_misread_guard_shadow_delta_yen": 3200,
            "pullback_misread_blocked_losers": 1,
        }
    )
    text = "\n".join(hl)
    assert "Flat Weak + Range:" not in text
    assert "PullbackMisread:" in text
    assert "loser回避" in text


def test_two_valid_items_not_padded():
    hl = build_daily_research_highlights(
        {
            "cost_aware_entry_shadow": _ca(),
            "pullback_volume_forward": {
                "enabled": True,
                "hits": 3,
                "volume_high_n": 1,
                "volume_low_n": 2,
                "volume_low": {"n": 2, "collapse_rate": 0.8},
            },
        }
    )
    headers = [
        ln
        for ln in hl
        if ln.endswith(":") and ln not in ("=== TODAY'S RESEARCH ===", "DATA WARNING:")
    ]
    assert len(headers) == 2
    assert len(headers) <= HIGHLIGHT_MAX_ITEMS
    assert len(hl) <= HIGHLIGHT_MAX_LINES


def test_data_warning_empty_suppressed():
    hl = build_daily_research_highlights({"cost_aware_entry_shadow": _ca()})
    assert "DATA WARNING:" not in hl


def test_final_render_skips_title_only_and_limits():
    hl = build_daily_research_highlights(
        {
            "cost_aware_entry_shadow": _ca(),
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 5,
            "flat_weak_range_shadow_completed": 2,
            "flat_weak_range_shadow_delta_yen": 7200,
            "flat_weak_range_shadow_blocked_losers": 2,
            "pullback_misread_guard_shadow_enabled": True,
            "pullback_misread_guard_shadow_delta_yen": 100,
            "pullback_misread_blocked_losers": 1,
            "pullback_misread_guard_shadow_blocked_count": 9,
            "pullback_volume_forward": {
                "enabled": True,
                "hits": 7,
                "volume_high_n": 2,
                "volume_low_n": 5,
                "volume_low": {"n": 5, "collapse_rate": 0.8},
            },
        }
    )
    # no orphan titles
    for i, ln in enumerate(hl):
        if ln.endswith(":") and ln not in ("=== TODAY'S RESEARCH ===", "DATA WARNING:"):
            assert i + 1 < len(hl)
            assert hl[i + 1].strip()
    headers = [
        ln
        for ln in hl
        if ln.endswith(":") and ln not in ("=== TODAY'S RESEARCH ===", "DATA WARNING:")
    ]
    assert len(headers) <= 3
    assert len(hl) <= 12


def test_am_embed_unaffected():
    hl = build_daily_research_highlights({"cost_aware_entry_shadow": _ca()})
    am = build_summary_embed_payload({"trade_count": 1}, am_pm="AM", research_highlights=hl)
    assert "TODAY'S RESEARCH" not in am["description"]


def test_thresholds_unchanged():
    assert abs(VOL_PERSISTENCE_HIGH_THR - 0.2782069767789509) < 1e-12
    assert abs(VOL_PERSISTENCE_LOW_THR - 0.12710349962769918) < 1e-12
