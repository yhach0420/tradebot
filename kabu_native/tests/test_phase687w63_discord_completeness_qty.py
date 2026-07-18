"""Phase687W63 — Discord completeness denominator & ENTRY qty consistency."""

from __future__ import annotations

import logging

from small_paper.discord_current_system_summary import (
    build_daily_research_highlights,
    build_shadow_summary_structured,
    collect_data_warnings,
    render_entry_aborted_lines,
    render_entry_quantity_line,
    render_official_entry_lines,
    resolve_entry_quantity,
    resolve_pullback_volume_counts,
)
from small_paper.entry_execution_integrity import is_official_entry_ready
from small_paper.pullback_volume_forward_logger import (
    VOL_PERSISTENCE_HIGH_THR,
    VOL_PERSISTENCE_LOW_THR,
    PullbackVolumeForwardState,
)


def test_misread_and_volume_denominators_separated():
    out = build_shadow_summary_structured(
        {
            "pullback_misread_guard_shadow_enabled": True,
            "pullback_misread_guard_shadow_blocked_count": 2,
            "pullback_volume_forward": {
                "enabled": True,
                "pullback_volume_eligible_count": 5,
                "pullback_volume_recorded_count": 5,
                "hits": 5,
            },
            "official_entry_count": 3,
        },
        am_pm="am",
    )
    text = out["discord_text"]
    assert "PullbackMisread hits:" in text
    assert "\n2\n" in text or "PullbackMisread hits:\n2" in text
    assert "Pullback Volume eligible:" in text
    assert "Pullback Volume recorded:" in text
    assert "5 / 5" in text
    assert "5 / 2" not in text
    assert "status: COMPLETE" in text


def test_incomplete_and_counter_mismatch():
    incomplete = build_shadow_summary_structured(
        {
            "pullback_volume_forward": {
                "enabled": True,
                "pullback_volume_eligible_count": 5,
                "pullback_volume_recorded_count": 4,
            },
            "official_entry_count": 3,
        },
        am_pm="am",
    )["discord_text"]
    assert "4 / 5" in incomplete
    assert "status: INCOMPLETE" in incomplete
    assert "status: COMPLETE" not in incomplete

    mismatch = resolve_pullback_volume_counts(
        {
            "pullback_volume_forward": {
                "enabled": True,
                "pullback_volume_eligible_count": 5,
                "pullback_volume_recorded_count": 6,
            }
        }
    )
    assert mismatch["status"] == "counter_mismatch"
    assert mismatch["ratio"] == "6 / 5"
    warns = collect_data_warnings(
        {
            "pullback_volume_forward": {
                "enabled": True,
                "pullback_volume_eligible_count": 5,
                "pullback_volume_recorded_count": 6,
            }
        }
    )
    assert any("records 6 / eligible 5" in w for w in warns)


def test_eligible_missing_legacy_na_no_warning():
    counts = resolve_pullback_volume_counts(
        {"pullback_volume_forward": {"enabled": True, "hits": 5}}
    )
    assert counts["eligible"] is None
    assert counts["recorded"] == 5
    assert counts["ratio"] == "n/a"
    text = build_shadow_summary_structured(
        {
            "pullback_misread_guard_shadow_enabled": True,
            "pullback_misread_guard_shadow_blocked_count": 2,
            "pullback_volume_forward": {"enabled": True, "hits": 5},
            "official_entry_count": 3,
        },
        am_pm="pm",
    )["discord_text"]
    assert "Pullback Volume completeness:" in text
    assert "n/a" in text
    assert "5 / 2" not in text
    warns = collect_data_warnings(
        {"pullback_volume_forward": {"enabled": True, "hits": 5}}
    )
    assert not any("records" in w and "eligible" in w for w in warns)


def test_data_warning_incomplete_daily_highlight():
    warns = collect_data_warnings(
        {
            "pullback_volume_forward": {
                "enabled": True,
                "pullback_volume_eligible_count": 5,
                "pullback_volume_recorded_count": 4,
            }
        }
    )
    assert "Pullback Volume records 4 / eligible 5" in warns
    hl = "\n".join(
        build_daily_research_highlights(
            {
                "observer_errors": 0,
                "pullback_volume_forward": {
                    "enabled": True,
                    "pullback_volume_eligible_count": 5,
                    "pullback_volume_recorded_count": 4,
                    "hits": 4,
                    "volume_low_n": 2,
                    "volume_low": {"n": 2, "collapse_rate": 1.0},
                },
                "cost_aware_entry_shadow": {
                    "enabled": True,
                    "selection_cycles": 1,
                    "candidates": 1,
                    "n_closed": 1,
                    "delta_yen": 1000,
                    "stop_risk_reject": 1,
                },
            }
        )
    )
    assert "DATA WARNING:" in hl
    assert "Pullback Volume records 4 / eligible 5" in hl
    assert "--- Data Completeness ---" not in hl


def test_entry_qty_consistent_and_zero_valid(caplog):
    for sym, px in (("7203.T", 2800), ("6758.T", 3000), ("8035.T", 25000)):
        text = "\n".join(
            render_official_entry_lines(
                {
                    "symbol": sym,
                    "entry_time": "2026-07-20T09:12:00+09:00",
                    "entry_price": px,
                    "quantity": 100,
                    "accept_stage": "official_entry",
                }
            )
        )
        assert "qty: 100" in text
        assert "[ENTRY]" in text
    assert resolve_entry_quantity({"qty": 0}) == 0
    assert render_entry_quantity_line(0) == "qty: 0"
    assert render_entry_quantity_line(None) == "qty: n/a"
    with caplog.at_level(logging.WARNING):
        na = "\n".join(render_official_entry_lines({"symbol": "9999.T"}, audit_missing=True))
    assert "qty: n/a" in na
    assert any("entry_quantity_missing" in r.message for r in caplog.records)


def test_ghost_accept_unchanged():
    ghost = {
        "symbol": "9984.T",
        "official_entry": False,
        "accept_aborted": True,
        "accept_stage": "accept_aborted",
    }
    assert is_official_entry_ready(ghost) is False
    text = "\n".join(
        render_entry_aborted_lines(ghost, reason="registration_failed", stage="accept_aborted")
    )
    assert "[ENTRY ABORTED]" in text
    assert "official entry: NOT CREATED" in text


def test_summary_block_exposes_eligible_recorded():
    st = PullbackVolumeForwardState(enabled=True)
    st.hit_count = 5
    st.rows = {f"k{i}": {} for i in range(5)}
    block = st.summary_block()
    assert block["pullback_volume_eligible_count"] == 5
    assert block["pullback_volume_recorded_count"] == 5


def test_forward_thresholds_unchanged():
    assert VOL_PERSISTENCE_HIGH_THR == 0.2782069767789509
    assert VOL_PERSISTENCE_LOW_THR == 0.12710349962769918
