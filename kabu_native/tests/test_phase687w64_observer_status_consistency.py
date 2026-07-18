"""Phase687W64 — Observer Status / Data Consistency Guard."""

from __future__ import annotations

from types import SimpleNamespace

from small_paper.discord_current_system_summary import (
    build_daily_research_highlights,
    build_shadow_summary_structured,
    collect_data_warnings,
    resolve_all_observer_states,
    resolve_observer_enabled,
)
from small_paper.forward_observer_defaults import COST_AWARE_ENV, PAPER_RUNTIME_ENV, PULLBACK_VOLUME_ENV
from small_paper.pullback_volume_forward_logger import VOL_PERSISTENCE_HIGH_THR, VOL_PERSISTENCE_LOW_THR


def _on_summary(**extra):
    s = {
        "cost_aware_entry_shadow": {
            "enabled": True,
            "selection_cycles": 2,
            "candidates": 3,
            "official_entry_match": 1,
            "official_entry_mismatch": 2,
            "n_closed": 2,
            "delta_yen": 5800,
            "stop_risk_reject": 1,
        },
        "flat_weak_range_shadow_enabled": True,
        "flat_weak_range_shadow_target_count": 3,
        "flat_weak_range_shadow_block_count": 2,
        "flat_weak_range_shadow_kept_count": 1,
        "flat_weak_range_shadow_completed": 2,
        "flat_weak_range_shadow_blocked_losers": 2,
        "flat_weak_range_shadow_delta_yen": 4600,
        "pullback_misread_guard_shadow_enabled": True,
        "pullback_misread_guard_shadow_blocked_count": 2,
        "pullback_misread_guard_shadow_delta_yen": 1500,
        "pullback_misread_blocked_losers": 1,
        "pullback_volume_forward": {
            "enabled": True,
            "hits": 5,
            "pullback_volume_eligible_count": 5,
            "pullback_volume_recorded_count": 5,
            "volume_high_n": 2,
            "volume_mid_n": 1,
            "volume_low_n": 2,
            "volume_high": {"n": 2, "healthy_rate": 1.0},
            "volume_low": {"n": 2, "collapse_rate": 1.0},
        },
        "official_entry_count": 3,
        "observer_exit_count": 3,
    }
    s.update(extra)
    return s


def test_enabled_true_with_data_shows_on():
    text = build_shadow_summary_structured(_on_summary(), am_pm="am")["discord_text"]
    assert "Cost-Aware Entry: ON" in text
    assert "Flat Weak + Range: ON" in text
    assert "PullbackMisread: ON" in text
    assert "Pullback Volume: ON" in text
    assert "evaluations: 3" in text
    assert "candidates: 3" in text
    assert "hits: 2" in text or "PullbackMisread hits:\n2" in text
    assert "hits: 5" in text
    assert "5 / 5" in text


def test_off_with_zero_data_hides_normal_detail():
    summary = {
        "cost_aware_entry_shadow": {"enabled": False},
        "flat_weak_range_shadow_enabled": False,
        "pullback_misread_guard_shadow_enabled": False,
        "pullback_volume_forward": {"enabled": False},
        "official_entry_count": 0,
    }
    text = build_shadow_summary_structured(summary, am_pm="am")["discord_text"]
    assert "Cost-Aware Entry: OFF" in text
    assert "Flat Weak + Range: OFF" in text
    assert "PullbackMisread: OFF" in text
    assert "Pullback Volume: OFF" in text
    assert "evaluations: 0 / 0" not in text
    assert "candidates: 3" not in text
    assert "hits: 5" not in text
    assert "5 / 5" not in text
    assert "status: collecting" not in text
    assert "not applicable (observer OFF)" in text


def test_off_with_data_presents_warning(monkeypatch):
    monkeypatch.delenv(COST_AWARE_ENV, raising=False)
    monkeypatch.delenv(PULLBACK_VOLUME_ENV, raising=False)
    monkeypatch.delenv(PAPER_RUNTIME_ENV, raising=False)
    summary = {
        "pullback_volume_forward": {
            "enabled": False,
            "hits": 5,
            "pullback_volume_eligible_count": 5,
            "pullback_volume_recorded_count": 5,
        }
    }
    text = build_shadow_summary_structured(summary, am_pm="am")["discord_text"]
    assert "Pullback Volume: OFF / DATA PRESENT" in text
    assert "CONFIG/DATA MISMATCH" in text
    assert "unexpected records: 5" in text
    assert "status: collecting" not in text
    warns = collect_data_warnings(summary)
    assert any("Pullback Volume is OFF but 5 records exist" in w for w in warns)
    hl = "\n".join(build_daily_research_highlights(summary))
    assert "DATA WARNING:" in hl
    assert not any(ln.startswith("Pullback Volume:") for ln in hl.splitlines())


def test_unknown_not_silently_off(monkeypatch):
    monkeypatch.delenv(COST_AWARE_ENV, raising=False)
    monkeypatch.delenv(PULLBACK_VOLUME_ENV, raising=False)
    monkeypatch.delenv(PAPER_RUNTIME_ENV, raising=False)
    empty = build_shadow_summary_structured({}, am_pm="am")["discord_text"]
    assert "Pullback Volume: UNKNOWN" in empty
    assert "Cost-Aware Entry: UNKNOWN" in empty
    with_data = build_shadow_summary_structured(
        {"pullback_volume_forward": {"hits": 5}},
        am_pm="am",
    )["discord_text"]
    assert "Pullback Volume: UNKNOWN / DATA PRESENT" in with_data
    warns = collect_data_warnings({"pullback_volume_forward": {"hits": 5}})
    assert any("Observer status unresolved: Pullback Volume" in w for w in warns)


def test_hits_do_not_infer_enabled(monkeypatch):
    monkeypatch.delenv(COST_AWARE_ENV, raising=False)
    monkeypatch.delenv(PAPER_RUNTIME_ENV, raising=False)
    assert (
        resolve_observer_enabled(
            "cost_aware_entry",
            None,
            {"cost_aware_entry_shadow": {"candidates": 9, "selection_cycles": 3}},
        )
        is None
    )


def test_runtime_config_priority(monkeypatch):
    monkeypatch.setenv(COST_AWARE_ENV, "0")
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    enabled = resolve_observer_enabled(
        "cost_aware_entry",
        SimpleNamespace(paper_runtime=True),
        {"cost_aware_entry_shadow": {"enabled": True, "candidates": 3}},
    )
    assert enabled is False
    states = resolve_all_observer_states(
        {"cost_aware_entry_shadow": {"enabled": True, "candidates": 3}},
        SimpleNamespace(paper_runtime=True),
    )
    assert states["cost_aware_entry"]["config_summary_conflict"] is True


def test_daily_off_suppresses_highlight():
    hl = "\n".join(
        build_daily_research_highlights(
            {
                "cost_aware_entry_shadow": {
                    "enabled": False,
                    "candidates": 3,
                    "selection_cycles": 1,
                    "delta_yen": 5800,
                    "n_closed": 1,
                    "stop_risk_reject": 1,
                },
                "flat_weak_range_shadow_enabled": False,
                "flat_weak_range_shadow_target_count": 3,
                "flat_weak_range_shadow_delta_yen": 4600,
                "pullback_volume_forward": {"enabled": False, "hits": 5, "volume_low_n": 2},
            }
        )
    )
    assert "Cost-Aware:" not in hl
    assert "Flat Weak + Range:" not in hl
    assert "DATA WARNING:" in hl


def test_forward_thresholds_unchanged():
    assert VOL_PERSISTENCE_HIGH_THR == 0.2782069767789509
    assert VOL_PERSISTENCE_LOW_THR == 0.12710349962769918
