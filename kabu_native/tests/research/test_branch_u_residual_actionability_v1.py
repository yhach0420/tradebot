"""Branch U residual actionability V1 tests. No 20260903/04 capture input."""
from __future__ import annotations

from research.simple_tech_redesign.branch_u_residual_actionability_v1_analyze import (
    ANALYSIS_EXHAUSTED,
    decide_phase_a,
    decide_phase_b,
)
from research.simple_tech_redesign.branch_u_residual_actionability_v1_harvest import (
    REQUIRED_TEXTS,
    recover_remaining_predicates,
    select_qualifier,
    semantic_duplicate_audit,
)
from research.simple_tech_redesign.branch_u_residual_actionability_v1_spec import (
    AUTO_HOP_NEXT_PRIMITIVE,
    COMBINATION_SEARCH,
    EXCLUDED_NOT_CANDIDATE,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    PNL_SELECTION,
    PROSPECTIVE_HARVEST_SUSPENDED,
    REMAINING_PRIMITIVES,
    TRUE_OOS,
    U_EARLY_MIN_HIT_FRAC,
    U_EMA_PRIOR_VERDICT,
    candidate_id_for,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day


def test_frozen_bounds_and_inventory():
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert PROSPECTIVE_HARVEST_SUSPENDED is True
    assert TRUE_OOS is False
    assert AUTO_HOP_NEXT_PRIMITIVE is False
    assert COMBINATION_SEARCH is False
    assert PNL_SELECTION is False
    assert abs(U_EARLY_MIN_HIT_FRAC - 0.20) < 1e-12
    assert REMAINING_PRIMITIVES == (
        "U_TREND_LOST",
        "U_PULLBACK_LOW_BID_BREAK",
        "U_RCI_RE_OVERSOLD",
        "U_VWAP_CLOSE_LOSS",
        "U_HH_HL_LOST",
    )
    assert "U_NEVER_BREAK_EVEN" in EXCLUDED_NOT_CANDIDATE
    assert "U_BB_LOWER_BREAK" in EXCLUDED_NOT_CANDIDATE
    assert "U_EMA_STRUCTURE_LOSS" in EXCLUDED_NOT_CANDIDATE
    assert U_EMA_PRIOR_VERDICT == "SIMPLE_TECH_BRANCH_U_EMA_PORTFOLIO_FAILED"
    assert candidate_id_for("U_TREND_LOST") == "UNPROVEN_U_TREND_LOST_V1"


def test_forbidden_days_fail_closed():
    assert assert_research_day("20260903", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260904", today="20260904") == "FAIL_CLOSED_FORBIDDEN_INPUT"
    assert assert_research_day("20260907", today="20260904") == "FAIL_CLOSED_FUTURE_DATA"


def test_exact_remaining_predicates_recovered():
    pack = recover_remaining_predicates()
    assert pack["ok"] is True, pack
    prims = dict(pack.get("primitives") or {})
    for pid in REMAINING_PRIMITIVES:
        assert pid in prims
        assert prims[pid].get("SOURCE_FILE")
        assert prims[pid].get("SOURCE_FUNCTION")
        assert prims[pid].get("SOURCE_LINE")
        assert prims[pid].get("SOURCE_SHA")
        assert prims[pid].get("EXACT_PREDICATE_TEXT")
        assert prims[pid].get("TIMEFRAME")
        assert prims[pid].get("EVENT_TIMESTAMP")
    for text in REQUIRED_TEXTS.values():
        assert text


def test_semantic_duplicate_pullback_and_rci_not_same_event():
    pack = recover_remaining_predicates()
    dup = semantic_duplicate_audit(pack)
    assert dup["ok"] is True, dup
    by = {r["primitive"]: r for r in dup["rows"]}
    assert by["U_PULLBACK_LOW_BID_BREAK"]["SEMANTIC_DUPLICATE"] is False
    assert by["U_RCI_RE_OVERSOLD"]["SEMANTIC_DUPLICATE"] is False
    assert dup["pullback_vs_floor_duplicate"] is False
    assert dup["rci_vs_p3_duplicate"] is False


def test_selection_rule_parity_no_pnl():
    rows = [
        {
            "primitive": "U_TREND_LOST",
            "qualified": True,
            "U_EARLY_HIT_N": 8,
            "hit_day_n": 3,
            "top_U_hit_symbol": {"abs_share": 0.2},
        },
        {
            "primitive": "U_HH_HL_LOST",
            "qualified": True,
            "U_EARLY_HIT_N": 8,
            "hit_day_n": 4,
            "top_U_hit_symbol": {"abs_share": 0.4},
        },
        {
            "primitive": "U_VWAP_CLOSE_LOSS",
            "qualified": True,
            "U_EARLY_HIT_N": 7,
            "hit_day_n": 7,
            "top_U_hit_symbol": {"abs_share": 0.1},
        },
    ]
    sel = select_qualifier(rows)
    assert sel["PNL_USED_FOR_SELECTION"] is False
    assert sel["n"] == 3
    assert sel["selected"] == "U_HH_HL_LOST"
    rows2 = [
        {
            "primitive": "A",
            "qualified": True,
            "U_EARLY_HIT_N": 6,
            "hit_day_n": 3,
            "top_U_hit_symbol": {"abs_share": 0.4},
        },
        {
            "primitive": "B",
            "qualified": True,
            "U_EARLY_HIT_N": 6,
            "hit_day_n": 3,
            "top_U_hit_symbol": {"abs_share": 0.2},
        },
    ]
    sel2 = select_qualifier(rows2)
    assert sel2["selected"] == "B"


def test_zero_qualifier_is_exhausted_no_phase_b():
    d = decide_phase_a(identity_ok=True, pred_ok=True, dup_ok=True, leak_ok=True, selection={"n": 0, "selected": None})
    assert d["VERDICT"] == ANALYSIS_EXHAUSTED
    assert d["run_phase_b"] is False
    assert d["FAMILY_CLOSED"] is True
    assert d["CANDIDATE_FROZEN"] is False


def _base(**over):
    body = {
        "identity": {
            "ok": True,
            "control_sot_ok": True,
            "treatment_sot_ok": True,
            "leftover_ok": True,
            "be_flag_mismatch_n": 0,
            "tf1_fail_n": 0,
            "predicate_drift_n": 0,
        },
        "decomp_ok": True,
        "U_EARLY_NEVER_BE_DIRECT_DELTA": 2000.0,
        "P_EARLY_DIRECT_DELTA": 0.0,
        "GOOD_DIRECT_DELTA": 0.0,
        "DIP_DIRECT_DELTA": 0.0,
        "PROTECTED_KEEP_DIRECT_DELTA": 0.0,
        "TOTAL_CAUSAL_DELTA": 3000.0,
        "control": {"PF": 1.2, "max_drawdown": -20000.0, "total_pnl": 1000.0, "fill_n": 84},
        "treatment": {"PF": 1.5, "max_drawdown": -15000.0, "total_pnl": 4000.0, "fill_n": 90},
        "concentration": {"single_day_contribution_gt_50pct": False, "single_symbol_contribution_gt_50pct": False},
    }
    body.update(over)
    return body


def test_p_early_direct_harm_is_case_b_not_relaxed():
    d = decide_phase_b(
        _base(P_EARLY_DIRECT_DELTA=-1.0),
        _base(),
        leak_ok=True,
        harvested_ok=True,
        pred_ok=True,
        primitive="U_TREND_LOST",
    )
    assert d["CASE"] == "B"
    assert d["VERDICT"] == "SIMPLE_TECH_BRANCH_U_RESIDUAL_WINNER_OR_RECOVERY_HARM"
    assert d["FAMILY_CLOSED"] is True
    assert d["CANDIDATE_FROZEN"] is False
    assert d["gates"]["3_P_EARLY_direct_ge_0"] is False


def test_good_or_dip_harm_is_case_b():
    d = decide_phase_b(_base(GOOD_DIRECT_DELTA=-1.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True, primitive="U_TREND_LOST")
    assert d["CASE"] == "B"
    d2 = decide_phase_b(_base(DIP_DIRECT_DELTA=-1.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True, primitive="U_TREND_LOST")
    assert d2["CASE"] == "B"


def test_u_early_not_improved_is_case_d():
    d = decide_phase_b(_base(U_EARLY_NEVER_BE_DIRECT_DELTA=0.0), _base(), leak_ok=True, harvested_ok=True, pred_ok=True, primitive="U_TREND_LOST")
    assert d["CASE"] == "D"
    assert d["FAMILY_CLOSED"] is True


def test_case_a_and_burned_p_early_harm_is_c():
    d = decide_phase_b(_base(), _base(), leak_ok=True, harvested_ok=True, pred_ok=True, primitive="U_TREND_LOST")
    assert d["CASE"] == "A"
    assert d["CANDIDATE_FROZEN"] is True
    d2 = decide_phase_b(
        _base(),
        _base(P_EARLY_DIRECT_DELTA=-10.0),
        leak_ok=True,
        harvested_ok=True,
        pred_ok=True,
        primitive="U_TREND_LOST",
    )
    assert d2["CASE"] == "C"
    assert d2["CANDIDATE_FROZEN"] is False
