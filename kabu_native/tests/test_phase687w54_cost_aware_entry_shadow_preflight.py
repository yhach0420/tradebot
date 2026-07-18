"""W54 Shadow Preflight — no-fill + NP exclusion + score formula."""

from __future__ import annotations

import os

from small_paper.cost_aware_entry_shadow import (
    CostAwareShadowState,
    integrated_score,
    note_symbol_eval,
    run_selection_cycle,
    shadow_enabled,
    simulate_nofill_decision,
    summarize_state,
)


def test_shadow_disabled_by_default(monkeypatch):
    monkeypatch.delenv("COST_AWARE_ENTRY_SHADOW", raising=False)
    monkeypatch.delenv("KABU_PAPER_RUNTIME", raising=False)
    assert shadow_enabled() is False
    assert shadow_enabled({"cost_aware_entry_shadow": {"enabled": False}}) is False


def test_shadow_enabled_via_env(monkeypatch):
    monkeypatch.setenv("COST_AWARE_ENTRY_SHADOW", "1")
    assert shadow_enabled() is True


def test_integrated_score_has_no_np_term():
    # formula: z_pbv2 + 0.35*we - 0.45*z_stop
    s = integrated_score(z_pbv2=1.0, winner_enrichment=2.0, z_stop=1.0)
    assert abs(s - (1.0 + 0.35 * 2.0 - 0.45 * 1.0)) < 1e-9


def test_nofill_rejects_rank1_does_not_take_rank2_same_snapshot():
    ranked = [
        {"symbol": "AAA.T", "z_stop": 2.0, "integrated_score": 10.0},  # STOP exceed
        {"symbol": "BBB.T", "z_stop": 0.0, "integrated_score": 9.0},  # healthy
    ]
    out = simulate_nofill_decision(ranked, free_slots=1)
    assert out["rejected"] == ["AAA.T"]
    assert out["accepted"] == []
    assert out["unfilled_slots"] == 1


def test_nofill_accepts_rank1_when_healthy():
    ranked = [
        {"symbol": "BBB.T", "z_stop": 0.0, "integrated_score": 9.0},
        {"symbol": "CCC.T", "z_stop": 0.0, "integrated_score": 8.0},
    ]
    out = simulate_nofill_decision(ranked, free_slots=1)
    assert out["accepted"] == ["BBB.T"]
    assert out["unfilled_slots"] == 0


def test_selection_cycle_np_audit_not_used_for_reject():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from small_paper.cost_aware_entry_shadow import ShadowPosition

    JST = ZoneInfo("Asia/Tokyo")
    st2 = CostAwareShadowState()
    # Cap almost full → free_slots=1
    for i in range(4):
        st2.open_shadow[f"HOLD{i}.T"] = ShadowPosition(
            symbol=f"HOLD{i}.T",
            entry_time=datetime.now(JST),
            entry_price=100.0,
            selection_cycle_id="pre",
            rank=1,
            integrated_score=0.0,
            winner_enrichment=0.0,
            stop_risk=0.0,
            stop_margin_z=0.0,
            pbv2_score=0.0,
        )
    # Pad cycle so cross-sectional z(stop) for AAA exceeds 1.65
    for i in range(20):
        note_symbol_eval(
            st2,
            scan_id="s2",
            symbol=f"PAD{i}.T",
            trade={
                "entry_expectancy_score_v2": 1,
                "entry_rise_5min_pct": 0.1,
                "spread_bps": 5,
                "entry_near_day_high_pct": 0.1,
                "entry_momentum_continuation_score": 0.5,
                "np_risk_score": 999.0,
                "CurrentPrice": 1000,
            },
        )
    # AAA: top integrated (huge pbv2) but STOP z extreme → reject consumes the only free slot
    note_symbol_eval(
        st2,
        scan_id="s2",
        symbol="AAA.T",
        trade={
            "entry_expectancy_score_v2": 500,
            "entry_rise_5min_pct": 20.0,
            "spread_bps": 100,
            "entry_near_day_high_pct": 8.0,
            "entry_momentum_continuation_score": 0.0,
            "np_risk_score": 999.0,
            "CurrentPrice": 1000,
        },
    )
    note_symbol_eval(
        st2,
        scan_id="s2",
        symbol="BBB.T",
        trade={
            "entry_expectancy_score_v2": 50,
            "entry_rise_5min_pct": 0.05,
            "spread_bps": 3,
            "entry_near_day_high_pct": 0.0,
            "entry_momentum_continuation_score": 0.9,
            "np_risk_score": 999.0,
            "CurrentPrice": 1000,
        },
    )
    c2 = run_selection_cycle(st2, scan_id="s2", trading_date="20260718")
    assert c2["np_in_decision"] is False
    assert c2["candidate_symbols_ordered"][0] == "AAA.T"
    assert "AAA.T" not in c2["accepted"]
    assert "BBB.T" not in c2["accepted"]  # same-snapshot no-fill after STOP reject
    assert c2["unfilled_slots_after"] >= 1
    assert st2.same_snapshot_nofill >= 1


def test_summary_declares_np_not_in_decision():
    st = CostAwareShadowState()
    s = summarize_state(st)
    assert s["np_in_decision"] is False
    assert "np_risk" not in s["score_formula"]
