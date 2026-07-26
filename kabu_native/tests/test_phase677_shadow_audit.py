"""Phase677 shadow recompute / registry tests."""
from __future__ import annotations

from small_paper.shadow_registry import SHADOW_REGISTRY, discord_inventory_from_registry
from small_paper.shadow_session_recompute import (
    recompute_flat_weak,
    recompute_pullback_misread,
)
from small_paper.discord_message_builder import DISCORD_SHADOW_INVENTORY


def test_registry_unique_ids():
    ids = [r["canonical_shadow_id"] for r in SHADOW_REGISTRY]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 20


def test_logger_marked_not_pnl():
    loggers = [r for r in SHADOW_REGISTRY if r["category"] == "LOGGER_ONLY"]
    assert loggers
    assert all(r.get("pnl_applicable") is False for r in loggers)


def test_research_not_in_discord_pnl_inventory():
    inv_names = {x["name"] for x in discord_inventory_from_registry()}
    for r in SHADOW_REGISTRY:
        if r["category"] == "RESEARCH_ONLY":
            assert r.get("discord_section") not in inv_names or r.get("pnl_applicable") is False


def test_fwr_position_id_join_normal_and_recovery():
    events = [
        {
            "event_type": "accepted",
            "position_id": "p1",
            "symbol": "1000.T",
            "entry_price": 100,
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": True,
        },
        {
            "event_type": "observer_exit",
            "exit_reason": "stop_hit",
            "position_id": "p1",
            "entry_price": 100,
            "exit_price": 90,
            "actual_pnl_yen_100": -1000,
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": True,
        },
        {
            "event_type": "accepted",
            "position_id": "p2",
            "symbol": "2000.T",
            "entry_price": 200,
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": False,
        },
        {
            "event_type": "observer_exit",
            "exit_reason": "recovery_forced_close",
            "position_id": "p2",
            "entry_price": 200,
            "exit_price": 210,
            "actual_pnl_yen_100": 1000,
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": False,
        },
    ]
    r = recompute_flat_weak(events)
    assert r["target_count"] == 2
    assert r["completed"] == 2
    assert r["recovery_join_count"] == 1
    assert r["runtime_pnl"] == 0.0  # -1000 + 1000
    assert r["shadow_pnl"] == 1000.0  # blocked→0 + kept 1000
    assert r["delta_pnl"] == 1000.0


def test_pullback_block_pnl():
    events = [
        {
            "event_type": "accepted",
            "position_id": "p1",
            "entry_price": 100,
            "pullback_misread_guard_shadow_blocked": True,
        },
        {
            "event_type": "observer_exit",
            "position_id": "p1",
            "exit_reason": "trailing_mfe_exit",
            "actual_pnl_yen_100": -500,
            "pullback_misread_guard_shadow_blocked": True,
        },
    ]
    r = recompute_pullback_misread(events)
    assert r["block_count"] == 1
    assert r["shadow_pnl"] == 0.0
    assert r["runtime_pnl"] == -500.0
    assert r["delta_pnl"] == 500.0


def test_discord_inventory_includes_flat_weak():
    names = {x["name"] for x in DISCORD_SHADOW_INVENTORY}
    assert "Flat Weak + Range" in names or "PullbackMisread" in names
