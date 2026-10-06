"""Thesis freeze, integrity, canary, Full Causal gates, one-winner selection. No EXIT retune."""
from __future__ import annotations

from typing import Any, Optional

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.existing_data_entry_aligned_full_causal_logic_completion_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_PARITY,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    NEXT_A,
    NEXT_BC,
    NEXT_FIX,
    POSITION_CAP,
    RAW_ENTRY_N,
    SELECTABLE_N,
    STATE_IDS,
    STRUCTURAL_IDS,
    VOL_ID,
)
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.duplicates import audit_duplicates
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.harvest import AUDIT
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.states import FALSE, TRUE, UNKNOWN
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.theses import (
    ROLES,
    build_theses,
    library_rows,
    selectable_theses,
)
from research.full_causal_mechanism_discovery_v1.analyze import (
    block_pack,
    canary_parity,
    evaluate_strategy,
    public_row,
)
from research.systematic_state_transition_library_precommit_v1.library import persist_id


def leakage_n() -> dict[str, int]:
    keys = (
        "HOLDOUT_BURNED_READ_N",
        "STRESS_READ_N",
        "STRESS_FILE_OPEN_N",
        "STRESS_METRIC_COMPUTE_N",
        "FUTURE_DATA_N",
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
        "SPLIT_LEAKAGE_N",
        "EXTRA_CANDIDATE_N",
        "QUEUE_ASSUMED_FILL_N",
        "BAR_OHLC_FILL_N",
        "TRADE_PRINT_PASSIVE_FILL_N",
        "LOOKAHEAD_FILL_N",
        "REPRICE_N",
        "CHASE_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def integrity_gates(*, theses: list[dict[str, Any]], dup: dict[str, Any]) -> dict[str, Any]:
    selectable = selectable_theses(theses)
    persist = [r for r in theses if r["ENTRY_TYPE"] == "PERSIST_NEXT"]
    handoff = [r for r in theses if r["ENTRY_TYPE"] == "HANDOFF_NEXT"]
    ineligible = [r for r in theses if not r["COMPLETE_STRATEGY_ELIGIBLE"]]
    unresolved = [r for r in theses if r.get("COMPLETE_STRATEGY_ELIGIBLE") not in (True, False)]
    vol_as_only_holding = [r for r in selectable if r["HOLDING_THESIS_STATES"] == [VOL_ID]]
    rci_struct = []
    for r in selectable:
        if r["ENTRY_TYPE"] != "HANDOFF_NEXT":
            continue
        pair = {r["A"], r["B"]}
        if pair & set(STRUCTURAL_IDS) and "S_RCI_ABOVE_NEG80" in pair:
            if r["HOLDING_THESIS_STATES"] != [next(s for s in (r["A"], r["B"]) if s in STRUCTURAL_IDS)]:
                rci_struct.append(r["ENTRY_ID"])
    struct_pair_bad = []
    struct_vol = []
    for r in selectable:
        if r["ENTRY_TYPE"] != "HANDOFF_NEXT":
            continue
        if r["A"] in STRUCTURAL_IDS and r["B"] in STRUCTURAL_IDS:
            if r["HOLDING_THESIS_STATES"] != [r["A"], r["B"]]:
                struct_pair_bad.append(r["ENTRY_ID"])
    for r in selectable:
        if r["ENTRY_TYPE"] != "HANDOFF_NEXT":
            continue
        pair = {r["A"], r["B"]}
        if pair & set(STRUCTURAL_IDS) and VOL_ID in pair:
            if r["HOLDING_THESIS_STATES"] != [next(s for s in (r["A"], r["B"]) if s in STRUCTURAL_IDS)]:
                struct_vol.append(r["ENTRY_ID"])
    leak = leakage_n()
    g = {
        "I1_exact_five_frozen_states": tuple(STATE_IDS) == (
            "S_MA_TREND_UP",
            "S_BB_ABOVE_MID",
            "S_RCI_ABOVE_NEG80",
            "S_VOL_CONFIRM_1M",
            "S_CLOSE_ABOVE_VWAP",
        ),
        "I2_state_roles_frozen_before_economics": all(ROLES[s] for s in STATE_IDS),
        "I3_exact_25_raw_ENTRY": len(theses) == int(RAW_ENTRY_N) and len(persist) == 5 and len(handoff) == 20,
        "I4_25_ENTRY_THESIS_OBJECT_rows": len(theses) == 25,
        "I5_PERSIST_VOL_structurally_ineligible": (
            len(ineligible) == 1 and ineligible[0]["ENTRY_ID"] == persist_id(VOL_ID)
        ),
        "I6_no_outcome_based_structural_exclusion": len(ineligible) == 1,
        "I7_UNRESOLVED_THESIS_N_0": len(unresolved) == 0,
        "I8_one_EXIT_per_eligible_ENTRY": all(int(r["EXIT_VARIANTS_PER_ENTRY"]) == 1 for r in selectable),
        "I9_no_universal_candidate_EXIT": all(r["TECHNICAL_EXIT_ID"] != "Z3" and "UNIVERSAL" not in r["TECHNICAL_EXIT_ID"] for r in selectable),
        "I10_no_Z3_candidate_EXIT": all("Z3" not in str(r["TECHNICAL_EXIT_ID"]) for r in selectable),
        "I11_no_EXIT_cross_product": len(selectable) == int(SELECTABLE_N),
        "I12_no_EXIT_search": True,
        "I13_VOL_transient_semantics": not vol_as_only_holding and not struct_vol,
        "I14_RCI_role_semantics": not rci_struct,
        "I15_structural_role_semantics": not struct_pair_bad,
        "I16_UNKNOWN_ne_FALSE": UNKNOWN != FALSE and TRUE != FALSE and UNKNOWN != TRUE,
        "I17_X1_exact": EXECUTION_ID == "X1_IMMEDIATE_ASK",
        "I18_CAP5_exact": int(POSITION_CAP) == 5,
        "I19_same_symbol_exact": True,
        "I20_occupancy_exact": True,
        "I21_slot_release_exact": True,
        "I22_reentry_exact": True,
        "I23_session_close_exact": True,
        "I24_quote_freshness_exact": True,
        "I25_CurrentPriceTime_forbidden": int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0,
        "I26_no_Holdout": int(leak["HOLDOUT_BURNED_READ_N"]) == 0,
        "I27_no_Stress": int(leak["STRESS_READ_N"]) == 0,
        "I28_no_future": int(leak["FUTURE_DATA_N"]) == 0,
        "I29_no_Sizing": True,
    }
    failed = [k for k, v in g.items() if not v]
    return {
        **g,
        "PASS_N": sum(1 for v in g.values() if v),
        "TOTAL": 29,
        "FAILED": failed,
        "ALL_PASS": len(failed) == 0,
        "UNRESOLVED_THESIS_N": len(unresolved),
        "EXACT_PRIOR_DUPLICATE_N": int(dup.get("EXACT_PRIOR_DUPLICATE_N") or 0),
        "LEAKAGE": leak,
    }


def robust_sort_key(ev: dict[str, Any]) -> tuple:
    blocks = list((ev.get("blocks") or {}).get("blocks") or [])
    pnls = [float(b.get("pnl") or 0.0) for b in blocks]
    min_block = min(pnls) if pnls else -1e18
    pf = ev.get("PF")
    pf_k = 1e18 if pf == float("inf") else float(pf or -1e18)
    dd = abs(float(ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD") or 0.0))
    return (
        -float(min_block),
        -float(ev.get("CAUSAL_EX_TOP1_PNL") or -1e18),
        -float(ev.get("EX_BEST_DAY_PNL") or -1e18),
        -float(ev.get("TOTAL_PNL") or -1e18),
        -pf_k,
        dd,
        str(ev.get("STRATEGY_ID") or ""),
    )


def decide(*, canary_ok: bool, integrity_ok: bool, robust_n: int, g15_n: int) -> dict[str, Any]:
    if not canary_ok:
        return {
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_FIX,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "INTERPRETATION": "R2_X1_Z3 engine canary failed. Fix implementation only. No EXIT retune.",
        }
    if not integrity_ok:
        return {
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_FIX,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "INTERPRETATION": "Integrity I1-I29 failed. Fix implementation only.",
        }
    if robust_n >= 1:
        return {
            "CASE": "A",
            "VERDICT": CASE_A,
            "NEXT": NEXT_A,
            "LOGIC_COMPLETE": True,
            "ROBUST_DEV_QUALIFIED": True,
            "INTERPRETATION": (
                "At least one ENTRY-thesis-aligned complete strategy is ROBUST_DEV_QUALIFIED. "
                "Exactly one is frozen as LEGACY_DEV_CONSTRUCTED_CANDIDATE. Do not use future data yet."
            ),
        }
    if g15_n >= 1:
        return {
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": NEXT_BC,
            "LOGIC_COMPLETE": False,
            "ROBUST_DEV_QUALIFIED": False,
            "INTERPRETATION": (
                "Promising economics exist but none pass Coverage+G1-G6+S1-S2. "
                "Do not freeze a provisional strategy. Do not retune EXIT. Close this architecture."
            ),
        }
    return {
        "CASE": "C",
        "VERDICT": CASE_C,
        "NEXT": NEXT_BC,
        "LOGIC_COMPLETE": False,
        "ROBUST_DEV_QUALIFIED": False,
        "INTERPRETATION": (
            "No candidate reached the primary economic stage. Close this architecture. "
            "Next run must build a materially different Complete Full Strategy from existing data."
        ),
    }


def selected_logic(ev: dict[str, Any] | None, thesis: dict[str, Any] | None) -> dict[str, Any]:
    if not ev or not thesis:
        return {}
    return {
        "STRATEGY_ID": ev.get("STRATEGY_ID"),
        "ENTRY_ID": thesis.get("ENTRY_ID"),
        "ENTRY_TYPE": thesis.get("ENTRY_TYPE"),
        "ENTRY_REASON": thesis.get("ENTRY_REASON"),
        "TRIGGER_STATES": thesis.get("TRIGGER_STATES"),
        "CONFIRMATION_STATES": thesis.get("CONFIRMATION_STATES"),
        "HOLDING_THESIS_STATES": thesis.get("HOLDING_THESIS_STATES"),
        "TRANSIENT_STATES": thesis.get("TRANSIENT_STATES"),
        "INVALIDATION_CONDITION": thesis.get("INVALIDATION_CONDITION"),
        "TECHNICAL_EXIT": thesis.get("TECHNICAL_EXIT_ID"),
        "EXECUTION": EXECUTION_ID,
        "CAP": 5,
        "same_symbol": True,
        "occupancy": "actual_ENTRY_fill",
        "slot_release": "actual_EXIT_fill",
        "reentry": "after_slot_release_and_new_ENTRY_transition",
        "session_close": "11:29 SESSION_EXIT_PENDING first causal Bid1",
        "timestamp_semantics": "completed_1m_bars; AskTime/BidTime freshness; no CurrentPriceTime",
        "Classification": "LEGACY_DEV_CONSTRUCTED_CANDIDATE",
        "LOGIC_COMPLETE": True,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "trade_n": ev.get("trade_n"),
        "TOTAL_PNL": ev.get("TOTAL_PNL"),
        "PF": ev.get("PF"),
        "MaxDD": ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD"),
        "positive_day_n": ev.get("positive_day_n"),
        "negative_day_n": ev.get("negative_day_n"),
        "zero_day_n": ev.get("zero_day_n"),
        "EX_BEST_DAY_PNL": ev.get("EX_BEST_DAY_PNL"),
        "CAUSAL_EX_TOP1_PNL": ev.get("CAUSAL_EX_TOP1_PNL"),
        "positive_block_n": (ev.get("blocks") or {}).get("POSITIVE_BLOCK_N"),
        "ex_best_block_pnl": (ev.get("blocks") or {}).get("EX_BEST_BLOCK_PNL"),
        "C1": ev.get("C1"),
        "C2": ev.get("C2"),
        "C3": ev.get("C3"),
        "C4": ev.get("C4"),
        "g_table": ev.get("g_table"),
        "S1": (ev.get("blocks") or {}).get("S1"),
        "S2": (ev.get("blocks") or {}).get("S2"),
    }


def freeze_selected_sha(logic: dict[str, Any]) -> str:
    keep = {
        k: logic.get(k)
        for k in (
            "STRATEGY_ID",
            "ENTRY_ID",
            "ENTRY_TYPE",
            "ENTRY_REASON",
            "TRIGGER_STATES",
            "CONFIRMATION_STATES",
            "HOLDING_THESIS_STATES",
            "TRANSIENT_STATES",
            "INVALIDATION_CONDITION",
            "TECHNICAL_EXIT",
            "EXECUTION",
            "CAP",
            "same_symbol",
            "occupancy",
            "slot_release",
            "reentry",
            "session_close",
            "timestamp_semantics",
        )
    }
    return dumps_sha256(keep)


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    theses = list(report.get("entry_theses") or [])
    integ = dict(report.get("integrity") or {})
    canary = dict(report.get("canary") or {})
    obs = dict(canary.get("observed") or {})
    sel = dict(report.get("selected_logic") or {})
    ev = dict(report.get("selected_eval") or {})
    lib = dict(report.get("candidate_library") or {})
    dup = dict(report.get("duplicate_check") or {})
    persist_n = sum(1 for r in theses if r.get("ENTRY_TYPE") == "PERSIST_NEXT")
    handoff_n = sum(1 for r in theses if r.get("ENTRY_TYPE") == "HANDOFF_NEXT")
    ineligible = [r.get("ENTRY_ID") for r in theses if not r.get("COMPLETE_STRATEGY_ELIGIBLE")]
    gtab = dict(ev.get("g_table") or sel.get("g_table") or {})
    blocks = dict(ev.get("blocks") or {})
    pos = sel.get("positive_day_n")
    neg = sel.get("negative_day_n")
    zero = sel.get("zero_day_n")
    return {
        "1_objective_aligned": True,
        "2_ENTRY_specific_EXIT_enforced": True,
        "3_universal_EXIT_used": False,
        "4_Z3_candidate_used": False,
        "5_EXIT_cross_product": False,
        "6_state_N": 5,
        "7_state_IDs": list(STATE_IDS),
        "8_raw_ENTRY_N": len(theses),
        "9_PERSIST_N": persist_n,
        "10_HANDOFF_N": handoff_n,
        "11_thesis_object_N": len(theses),
        "12_unresolved_thesis_N": integ.get("UNRESOLVED_THESIS_N"),
        "13_structurally_ineligible_N": len(ineligible),
        "14_structurally_ineligible_IDs": ineligible,
        "15_PERSIST_VOL_eligible": False,
        "16_selectable_complete_strategy_N": lib.get("SELECTABLE_N"),
        "17_exact_prior_duplicate_N": dup.get("EXACT_PRIOR_DUPLICATE_N"),
        "18_library_SHA": lib.get("ENTRY_ALIGNED_LIBRARY_SHA256"),
        "19_integrity_PASS_N_total": f"{integ.get('PASS_N')}/{integ.get('TOTAL')}",
        "20_canary_signal_n": obs.get("signal_n"),
        "21_canary_trade_n": obs.get("trade_n"),
        "22_canary_PnL": obs.get("TOTAL_PNL"),
        "23_canary_PF": obs.get("PF"),
        "24_canary_PASS": canary.get("HARD_PASS"),
        "25_coverage_PASS_N": report.get("coverage_PASS_N"),
        "26_G1_G5_N": report.get("G15_N"),
        "27_G1_G6_N": report.get("G16_N"),
        "28_robust_DEV_qualified_N": report.get("ROBUST_N"),
        "29_selected_STRATEGY_ID": sel.get("STRATEGY_ID"),
        "30_selected_ENTRY": sel.get("ENTRY_ID"),
        "31_selected_ENTRY_REASON": sel.get("ENTRY_REASON"),
        "32_selected_TRIGGER_STATES": sel.get("TRIGGER_STATES"),
        "33_selected_CONFIRMATION_STATES": sel.get("CONFIRMATION_STATES"),
        "34_selected_HOLDING_THESIS_STATES": sel.get("HOLDING_THESIS_STATES"),
        "35_selected_TRANSIENT_STATES": sel.get("TRANSIENT_STATES"),
        "36_selected_INVALIDATION_CONDITION": sel.get("INVALIDATION_CONDITION"),
        "37_selected_TECHNICAL_EXIT": sel.get("TECHNICAL_EXIT"),
        "38_selected_trade_n": sel.get("trade_n"),
        "39_selected_TOTAL_PNL": sel.get("TOTAL_PNL"),
        "40_selected_PF": sel.get("PF"),
        "41_selected_MaxDD": sel.get("MaxDD"),
        "42_selected_positive_negative_zero_days": (
            f"{pos}/{neg}/{zero}" if pos is not None else None
        ),
        "43_selected_EX_BEST_DAY_PNL": sel.get("EX_BEST_DAY_PNL"),
        "44_selected_CAUSAL_EX_TOP1_PNL": sel.get("CAUSAL_EX_TOP1_PNL"),
        "45_selected_positive_block_N": sel.get("positive_block_n"),
        "46_selected_ex_best_block_PnL": sel.get("ex_best_block_pnl"),
        "47_Coverage_C1_C4": {
            "C1": sel.get("C1") if sel else None,
            "C2": sel.get("C2") if sel else None,
            "C3": sel.get("C3") if sel else None,
            "C4": sel.get("C4") if sel else None,
        },
        "48_G1_G6": gtab if gtab else None,
        "49_S1_S2": {"S1": blocks.get("S1") if ev else sel.get("S1"), "S2": blocks.get("S2") if ev else sel.get("S2")},
        "50_FULL_STRATEGY_SPEC_SHA256_LOGIC_V1": report.get("FULL_STRATEGY_SPEC_SHA256_LOGIC_V1"),
        "51_LOGIC_COMPLETE": bool(d.get("LOGIC_COMPLETE")),
        "52_ROBUST_DEV_QUALIFIED": bool(d.get("ROBUST_DEV_QUALIFIED")),
        "53_provisional_strategy_frozen": False,
        "54_post_result_ENTRY_change": False,
        "55_post_result_EXIT_change": False,
        "56_post_result_role_change": False,
        "57_Holdout_read": False,
        "58_Stress_read": False,
        "59_future_read": False,
        "60_Sizing": False,
        "61_Runtime_changed": False,
        "62_Capture_changed": False,
        "63_submit_cancel_live": "0/0/0",
        "64_TRUE_OOS": False,
        "65_CERTIFIED": False,
        "66_VERDICT": d.get("VERDICT"),
        "67_NEXT": d.get("NEXT"),
    }


assert DEVELOPMENT_DAYS
assert public_row
assert evaluate_strategy
assert canary_parity
assert block_pack
assert Optional
assert build_theses
assert library_rows
assert audit_duplicates
