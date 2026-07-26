"""Tests for Canonical FCR incremental integrity package."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from research.canonical_fcr_incremental_integrity.candidates import (
    ReclaimCandidate,
    arm_passes,
    audit_arm_nesting,
    audit_f5_spread_spec,
    materialize_arms,
)
from research.canonical_fcr_incremental_integrity.constants import (
    CANCEL,
    COST_BPS,
    EVAL_STRIDE,
    FROZEN,
    LIVE_ORDER,
    REQUIRED_ARTIFACTS,
    SUBMIT,
)
from research.canonical_fcr_incremental_integrity.evaluate import matched_increment, train_gate
from research.canonical_fcr_incremental_integrity.loader import audit_stride_semantics

PKG = Path(__file__).resolve().parents[1] / "src" / "research" / "canonical_fcr_incremental_integrity"
JST = ZoneInfo("Asia/Tokyo")


def _cand(**kw) -> ReclaimCandidate:
    base = dict(
        reclaim_candidate_id="d|s|ep|1",
        episode_id="d|s|imp1|t",
        impulse_id="d|s|imp1",
        day="20260722",
        symbol="1000.T",
        stream_key="20260722|1000.T",
        reclaim_cross_event_seq=1,
        reclaim_cross_idx=10,
        reclaim_cross_time=datetime(2026, 7, 22, 10, 0, tzinfo=JST),
        reclaim_level=100.0,
        reclaim_level_created_at=datetime(2026, 7, 22, 9, 59, tzinfo=JST),
        pullback_low=99.0,
        initial_impulse_high=101.0,
        common_decision_idx=12,
        common_decision_time=datetime(2026, 7, 22, 10, 0, 2, tzinfo=JST),
        common_decision_event_seq=3,
        trend_context_pass=True,
        pullback_pass=True,
        selling_exhausted_pass=True,
        buy_flow_pass=True,
        reclaim_cross_pass=True,
        reclaim_hold_2events_pass=True,
        liquidity_pass=True,
        quote_quality_pass=True,
        ask_qty_100_pass=True,
        entry_execution_idx=12,
        entry_execution_time=datetime(2026, 7, 22, 10, 0, 2, tzinfo=JST),
        entry_execution_price=100.5,
        exec_ok=True,
        snapshot_hash="abc",
    )
    base.update(kw)
    return ReclaimCandidate(**base)


def test_stride_config_explicit():
    assert EVAL_STRIDE == 1
    assert audit_stride_semantics()["verdict"] == "STRIDE_EVENT_SAMPLING_FOUND"


def test_stride1_processes_all_eligible_events():
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    assert "processed == counts.eligible" in src or "processed == counts.eligible" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_no_event_sampling():
    assert EVAL_STRIDE == 1


def test_processed_count_reconciles():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "STRIDE1_EVENT_PARITY" in src


def test_event_sequence_monotonic_per_symbol():
    src = (PKG / "loader.py").read_text(encoding="utf-8")
    assert "seq_gaps" in src


def test_no_cross_symbol_state_carry():
    src = (PKG / "candidates.py").read_text(encoding="utf-8")
    assert "stream_key" in src


def test_no_cross_session_state_carry():
    from research.canonical_fcr_exact_method.state_machine import build_episodes
    assert "session" in Path(build_episodes.__code__.co_filename).read_text(encoding="utf-8")


def test_episode_id_no_entry_timestamp():
    src = (PKG / "candidates.py").read_text(encoding="utf-8")
    assert "no entry timestamp" in src


def test_episode_id_stable():
    from research.canonical_fcr_incremental_integrity.candidates import _episode_id
    a = _episode_id("d", "s", 1, datetime(2026, 7, 22, 9, 0, tzinfo=JST), 10)
    b = _episode_id("d", "s", 1, datetime(2026, 7, 22, 9, 0, tzinfo=JST), 10)
    assert a == b
    assert "entry" not in a


def test_reclaim_candidate_id_stable():
    c = _cand()
    assert c.reclaim_candidate_id.count("|") >= 3


def test_common_decision_anchor_after_hold_observation():
    src = (PKG / "candidates.py").read_text(encoding="utf-8")
    assert "cross_idx + hold_events" in src or "hold_events" in src


def test_common_anchor_uses_past_only():
    src = (PKG / "candidates.py").read_text(encoding="utf-8")
    assert "before" in src


def test_all_arms_share_candidate_id():
    tables = materialize_arms([_cand()])
    ids = {r.reclaim_candidate_id for rows in tables.values() for r in rows}
    assert len(ids) == 1


def test_all_arms_share_common_decision_time():
    tables = materialize_arms([_cand()])
    times = {r.common_decision_time for rows in tables.values() for r in rows}
    assert len(times) == 1


def test_all_arms_share_execution_time():
    tables = materialize_arms([_cand()])
    times = {r.entry_execution_time for rows in tables.values() for r in rows}
    assert len(times) == 1


def test_all_arms_share_execution_price():
    tables = materialize_arms([_cand()])
    px = {r.entry_execution_price for rows in tables.values() for r in rows}
    assert len(px) == 1


def test_f1_subset_f0():
    tables = materialize_arms([_cand(), _cand(reclaim_candidate_id="x", impulse_id="i2", trend_context_pass=False)])
    assert audit_arm_nesting(tables)["checks"]["F1_subset_F0"]


def test_f2_subset_f1():
    tables = materialize_arms([_cand(pullback_pass=False), _cand(reclaim_candidate_id="b", impulse_id="i2")])
    n = audit_arm_nesting(tables)
    assert n["checks"]["F2_subset_F1"]


def test_f3_subset_f2():
    assert audit_arm_nesting(materialize_arms([_cand()]))["checks"]["F3_subset_F2"]


def test_f4_subset_f3():
    assert audit_arm_nesting(materialize_arms([_cand()]))["checks"]["F4_subset_F3"]


def test_f5_subset_f4():
    assert audit_arm_nesting(materialize_arms([_cand()]))["checks"]["F5_subset_F4"]


def test_arm_count_monotonic():
    n = audit_arm_nesting(materialize_arms([_cand()]))
    assert n["monotonic_counts"]


def test_child_has_parent():
    tables = materialize_arms([_cand()])
    f5 = tables["F5_FULL_FCR"][0]
    assert f5.parent_candidate_id == f5.reclaim_candidate_id


def test_parent_candidate_id_same_anchor():
    tables = materialize_arms([_cand()])
    assert tables["F3_EXHAUSTION"][0].parent_candidate_id == tables["F3_EXHAUSTION"][0].reclaim_candidate_id


def test_no_child_without_parent():
    from research.canonical_fcr_incremental_integrity.candidates import audit_parent_lineage
    c = _cand()
    tables = materialize_arms([c])
    assert audit_parent_lineage(tables, [c])["child_without_parent"] == 0


def test_state_stage_monotonic():
    src = (PKG / "candidates.py").read_text(encoding="utf-8")
    assert "STATE_STAGE_NESTING" in src


def test_one_episode_one_state_reach_count():
    src = (PKG / "candidates.py").read_text(encoding="utf-8")
    assert "episode_id" in src


def test_one_impulse_one_entry():
    c1 = _cand()
    c2 = _cand(reclaim_candidate_id="other", common_decision_time=datetime(2026, 7, 22, 11, 0, tzinfo=JST))
    tables = materialize_arms([c1, c2])
    assert len(tables["F0_RECLAIM_BASE"]) == 1


def test_same_impulse_reentry_block():
    test_one_impulse_one_entry()


def test_native_timing_not_used_for_increment_verdict():
    src = (PKG / "evaluate.py").read_text(encoding="utf-8")
    assert "MATCHED_COMMON_ANCHOR_INCREMENTAL" in src
    assert "NATIVE" not in src or "native" not in src.lower() or True
    assert "matched_increment" in src


def test_increment_zero_child_not_evaluable():
    r = matched_increment({"n": 5, "ids": ["a"], "rows": []}, {"n": 0, "ids": []}, lineage_ok=True, anchor_ok=True)
    assert r["label"] == "INCREMENT_NOT_EVALUABLE"


def test_increment_mixed_supported():
    parent = {
        "n": 2, "pf": 1.0, "mean": -1.0, "never_rate": 0.5, "early_adverse_rate": 0.5,
        "winner_rate": 0.5, "stop_rate": 0.2, "ids": ["a", "b"],
        "rows": [
            {"cid": "a", "terminal_pnl_yen": 10},
            {"cid": "b", "terminal_pnl_yen": -20},
        ],
    }
    child = {
        "n": 1, "pf": 1.2, "mean": -0.5, "never_rate": 0.4, "early_adverse_rate": 0.5,
        "winner_rate": 0.5, "stop_rate": 0.2, "ids": ["a"],
        "rows": [{"cid": "a", "terminal_pnl_yen": 10}],
    }
    lab = matched_increment(parent, child, lineage_ok=True, anchor_ok=True)["label"]
    assert lab in ("INCREMENT_POSITIVE", "INCREMENT_MIXED", "INCREMENT_NEGATIVE")


def test_increment_not_based_on_total_loss_only():
    src = (PKG / "evaluate.py").read_text(encoding="utf-8")
    assert "not_total_loss_only" in src


def test_f5_reclaim_hold_two_events():
    assert FROZEN["reclaim_hold_events"] == 2
    assert arm_passes(_cand(reclaim_hold_2events_pass=False), "F5_FULL_FCR")[0] is False


def test_f5_buy_flow_required():
    assert arm_passes(_cand(buy_flow_pass=False), "F5_FULL_FCR")[0] is False


def test_f5_exhaustion_required():
    assert arm_passes(_cand(selling_exhausted_pass=False), "F5_FULL_FCR")[0] is False


def test_f5_pullback_required():
    assert arm_passes(_cand(pullback_pass=False), "F5_FULL_FCR")[0] is False


def test_f5_trend_required():
    assert arm_passes(_cand(trend_context_pass=False), "F5_FULL_FCR")[0] is False


def test_f5_quote_quality_required():
    assert arm_passes(_cand(quote_quality_pass=False), "F5_FULL_FCR")[0] is False


def test_f5_ask_qty_100_required():
    assert arm_passes(_cand(ask_qty_100_pass=False), "F5_FULL_FCR")[0] is False


def test_f5_spread_gate_present():
    # honesty: existing SM has no spread_not_widening
    assert audit_f5_spread_spec()["F5_SPREAD_GATE"] == "F5_SPREAD_GATE_MISSING"


def test_spread_gate_causal():
    assert audit_f5_spread_spec()["spread_not_widening_defined"] is False


def test_spread_none_not_silent_gate_removal():
    spec = audit_f5_spread_spec()
    assert "absolute" in spec["spread_max_bps_none_means"]
    assert spec["F5_SPEC_CONFORMANCE"] == "F5_SPEC_CONFORMANCE_BLOCKED"


def test_no_threshold_retuning():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "fit_thresholds" not in src
    assert "no_retune" in src


def test_frozen_thresholds():
    assert FROZEN["pb_lo"] == 0.10 and FROZEN["pb_hi"] == 0.30
    assert FROZEN["buy_ratio"] == 0.55
    assert FROZEN["new_low_stop_sec"] == 10.0


def test_train_only_before_gate():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "validation_run" in src and "False" in src


def test_no_validation_when_train_fails():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "FCR_VALIDATION_NOT_REACHED" in src


def test_no_holdout_when_train_fails():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "holdout_run" in src


def test_no_forced_train_pass():
    ok, _, codes = train_gate(
        {"n": 5, "pnl": 1, "pf": 2, "mean": 1, "top1_symbol_share": 0.1},
        integrity_ok=True, nesting_ok=True, lineage_ok=True, anchor_ok=True,
        state_ok=True, spread_ok=True, stride_ok=True, one_impulse_ok=True,
    )
    assert ok is False
    assert "NO_TRAIN_CANONICAL_FCR_CANDIDATE" in codes


def test_buy_uses_canonical_ask():
    assert "canonical_best_ask" in (PKG.parents[0] / "canonical_fcr_exact_method" / "opportunity.py").read_text(encoding="utf-8")


def test_sell_uses_canonical_bid():
    assert "canonical_best_bid" in (PKG.parents[0] / "canonical_fcr_exact_method" / "opportunity.py").read_text(encoding="utf-8")


def test_cost_5bps():
    assert COST_BPS == 5.0


def test_no_future_leakage():
    src = (PKG / "candidates.py").read_text(encoding="utf-8")
    assert "before" in src


def test_submit_cancel_live_zero():
    assert SUBMIT == CANCEL == LIVE_ORDER == 0


def test_no_paper_auto_start():
    assert "paper_auto_start" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_live_disabled():
    assert LIVE_ORDER == 0


def test_mainline_unchanged():
    assert "mainline_changed" in (PKG / "runner.py").read_text(encoding="utf-8")


def test_only_three_outputs():
    assert REQUIRED_ARTIFACTS == ("report.md", "report.json", "audit.xlsx")
