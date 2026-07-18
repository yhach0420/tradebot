"""Phase687W58 — Paper Forward observers default ON."""

from __future__ import annotations

from pathlib import Path

import pytest

from small_paper.cost_aware_entry_shadow import shadow_enabled
from small_paper.forward_observer_defaults import (
    COST_AWARE_ENV,
    PAPER_RUNTIME_ENV,
    PULLBACK_VOLUME_ENV,
    ensure_paper_forward_observer_env,
    format_forward_observers_startup_lines,
    forward_observer_status_block,
    parse_env_bool,
    resolve_cost_aware_entry_shadow,
    resolve_pullback_volume_forward,
)
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_dynamic40_shadow
from small_paper.pullback_volume_forward_logger import (
    PullbackVolumeForwardState,
    build_entry_row,
    logger_enabled,
)


@pytest.fixture(autouse=True)
def _clear_observer_env(monkeypatch):
    for k in (COST_AWARE_ENV, PULLBACK_VOLUME_ENV, PAPER_RUNTIME_ENV):
        monkeypatch.delenv(k, raising=False)


def test_unset_non_paper_defaults_off():
    assert shadow_enabled() is False
    assert logger_enabled() is False
    assert resolve_cost_aware_entry_shadow()[1] == "default"
    assert resolve_pullback_volume_forward()[1] == "default"


def test_paper_unset_defaults_on(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    assert shadow_enabled() is True
    assert logger_enabled() is True
    assert resolve_cost_aware_entry_shadow()[1] == "default"
    assert resolve_pullback_volume_forward()[1] == "default"


def test_explicit_zero_off_even_in_paper(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    monkeypatch.setenv(COST_AWARE_ENV, "0")
    monkeypatch.setenv(PULLBACK_VOLUME_ENV, "0")
    assert shadow_enabled() is False
    assert logger_enabled() is False
    ca_on, ca_src = resolve_cost_aware_entry_shadow()
    assert ca_on is False and ca_src == "env"
    status = forward_observer_status_block()
    assert status["warning"] is None  # explicit off → no warning
    lines = status["discord_lines"]
    assert "OFF (explicit)" in "\n".join(lines)


def test_explicit_one_on(monkeypatch):
    monkeypatch.setenv(COST_AWARE_ENV, "1")
    monkeypatch.setenv(PULLBACK_VOLUME_ENV, "1")
    assert shadow_enabled() is True
    assert logger_enabled() is True
    assert resolve_cost_aware_entry_shadow()[1] == "env"


def test_ensure_paper_does_not_overwrite_zero(monkeypatch):
    monkeypatch.setenv(COST_AWARE_ENV, "0")
    monkeypatch.setenv(PULLBACK_VOLUME_ENV, "0")
    ensure_paper_forward_observer_env()
    assert parse_env_bool(COST_AWARE_ENV) is False
    assert parse_env_bool(PULLBACK_VOLUME_ENV) is False
    assert parse_env_bool(PAPER_RUNTIME_ENV) is True


def test_run_paper_trade_bat_sets_defaults_when_undefined():
    bat = Path(__file__).resolve().parents[2].parent / "run_paper_trade.bat"
    if not bat.is_file():
        bat = Path(r"C:\Users\yhach\Documents\tradebotfile\run_paper_trade.bat")
    text = bat.read_text(encoding="utf-8", errors="ignore")
    assert "if not defined COST_AWARE_ENTRY_SHADOW set COST_AWARE_ENTRY_SHADOW=1" in text
    assert "if not defined PULLBACK_VOLUME_FORWARD set PULLBACK_VOLUME_FORWARD=1" in text


def test_gate_decision_identical_on_off(monkeypatch):
    trade = {
        "entry_rise_5min_pct": -0.2,
        "entry_vwap_dev_pct": -0.1,
        "universe_slot": "dynamic",
    }
    monkeypatch.delenv(PAPER_RUNTIME_ENV, raising=False)
    hit_off = would_block_pullback_dynamic40_shadow(trade)
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    hit_on = would_block_pullback_dynamic40_shadow(trade)
    assert hit_off is True and hit_on is True
    # logger must not mutate trade / decisions
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718")
    t2 = {
        **trade,
        "symbol": "7203.T",
        "entry_time": "2026-07-18T10:00:00+09:00",
        "entry_price": 1000,
        "gate_accept": True,
    }
    build_entry_row(st, t2, official_entry=True, official_reject=False)
    assert t2["gate_accept"] is True


def test_observer_exception_fail_open():
    """Mirror pilot fail-open: observer boom must not propagate."""

    class Boom:
        active = True

        def notify_forward_observers_startup(self, *, lines):
            raise RuntimeError("boom")

    notified = {"done": False}

    def _notify_once(discord) -> None:
        if notified["done"]:
            return
        try:
            block = forward_observer_status_block()
            notified["done"] = True
            if discord is None or not getattr(discord, "active", False):
                return
            discord.notify_forward_observers_startup(lines=list(block.get("discord_lines") or []))
        except Exception:
            notified["done"] = True

    _notify_once(Boom())
    assert notified["done"] is True
    _notify_once(Boom())  # second call no-op


def test_discord_startup_once_flag():
    lines = format_forward_observers_startup_lines(
        cost_aware_enabled=True,
        cost_aware_source="default",
        pullback_enabled=True,
        pullback_source="default",
    )
    assert lines[0] == "[FORWARD OBSERVERS]"
    assert "Cost-Aware Entry: ON" in lines[1]
    assert "runtime impact: none" in lines[-1]


def test_restart_no_duplicate_candidate(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718")
    trade = {
        "symbol": "7203.T",
        "entry_time": "2026-07-18T10:00:00+09:00",
        "entry_rise_5min_pct": -0.2,
        "entry_vwap_dev_pct": -0.1,
        "universe_slot": "dynamic",
        "entry_price": 1000,
    }
    assert build_entry_row(st, trade, official_entry=True, official_reject=False) is not None
    assert build_entry_row(st, trade, official_entry=True, official_reject=False) is None
    assert st.duplicate_skipped == 1


def test_config_can_enable_without_paper():
    assert shadow_enabled({"cost_aware_entry_shadow": {"enabled": True}}) is True
    assert logger_enabled({"pullback_volume_forward": {"enabled": True}}) is True
