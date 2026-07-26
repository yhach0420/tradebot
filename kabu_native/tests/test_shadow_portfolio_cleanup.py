"""Shadow Portfolio Cleanup — registry / Discord / cost-aware retirement."""

from __future__ import annotations

from small_paper.discord_current_system_summary import build_shadow_summary_structured
from small_paper.forward_observer_defaults import (
    resolve_cost_aware_entry_shadow,
    resolve_cost_aware_entry_v2_shadow,
)
from small_paper.shadow_registry import (
    SHADOW_REGISTRY,
    discord_inventory_from_registry,
    format_shadow_portfolio_startup_lines,
    is_shadow_runtime_enabled,
    shadow_portfolio_status,
)


def test_registry_classified_exactly_once():
    classes = {r["management_class"] for r in SHADOW_REGISTRY}
    assert "UNKNOWN_BLOCKED" not in classes or all(
        r.get("management_class") != "UNKNOWN_BLOCKED" for r in SHADOW_REGISTRY
    )
    assert len(SHADOW_REGISTRY) == 35


def test_active_portfolio():
    st = shadow_portfolio_status()
    assert st["shadow_portfolio_status"]["ACTIVE_FORWARD"] == ["e1_x5_forward_shadow"]
    assert set(st["shadow_portfolio_status"]["TEMP_FORWARD"]) == {
        "flat_weak_range_shadow",
        "board_imbalance_reversal_shadow",
    }
    assert st["shadow_portfolio_status"]["MAINLINE_MONITOR"] == [
        "board_dynamic_trailing_shadow"
    ]
    assert st["active_pnl_shadow_count"] == 4
    assert st["active_shadow_count"] == 6  # +2 logger_only


def test_discord_inventory_max_3():
    inv = discord_inventory_from_registry()
    assert len(inv) == 3
    ids = {x["canonical_shadow_id"] for x in inv}
    assert ids == {
        "e1_x5_forward_shadow",
        "flat_weak_range_shadow",
        "board_dynamic_trailing_shadow",
    }


def test_cost_aware_retired(monkeypatch):
    monkeypatch.setenv("KABU_PAPER_RUNTIME", "1")
    monkeypatch.delenv("COST_AWARE_ENTRY_SHADOW", raising=False)
    monkeypatch.delenv("COST_AWARE_ENTRY_V2_SHADOW", raising=False)
    assert resolve_cost_aware_entry_shadow()[0] is False
    assert resolve_cost_aware_entry_v2_shadow()[0] is False
    assert is_shadow_runtime_enabled("cost_aware_entry_shadow") is False
    assert is_shadow_runtime_enabled("cost_aware_entry_v2_shadow") is False


def test_remove_targets_off():
    for cid in (
        "pbv2_rise5_shadow",
        "exit_shadow_monitor_t2_t3",
        "vwap_shadow_reject",
        "low_liquidity_shadow",
    ):
        assert is_shadow_runtime_enabled(cid) is False


def test_structured_summary_omits_retired():
    text = build_shadow_summary_structured({}, am_pm="AM")["discord_text"]
    assert "E1_X5" in text
    assert "Board Dynamic" in text
    assert "Cost-Aware" not in text
    assert "PullbackMisread" not in text
    assert "Rise5" not in text


def test_startup_portfolio_lines():
    lines = format_shadow_portfolio_startup_lines()
    assert lines[0] == "Shadow Portfolio:"
    assert any("ACTIVE_FORWARD" in x for x in lines)
    assert any("RETIRED: count=" in x for x in lines)
