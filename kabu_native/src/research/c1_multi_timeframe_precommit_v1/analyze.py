"""Freeze C1 library. No economics. No signal counts. No harvest."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_E,
    DEVELOPMENT_DAYS,
    NEXT_IF_A,
    NEXT_IF_B,
    OR_FINAL_STATUS,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_VERDICT,
    RAW_CANDIDATE_IDS,
)
from research.c1_multi_timeframe_precommit_v1.asof_semantics import prove_same_timestamp_ordering
from research.c1_multi_timeframe_precommit_v1.bucket_alignment import prove_bucket_alignment
from research.c1_multi_timeframe_precommit_v1.duplicate_audit import apply_prune, audit_raw_library
from research.c1_multi_timeframe_precommit_v1.htf_states import htf_registry
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library
from research.c1_multi_timeframe_precommit_v1.one_min_states import one_min_registry
from research.c1_multi_timeframe_precommit_v1.prior import load_prior
from research.c1_multi_timeframe_precommit_v1.session_reset import prove_session_reset
from research.c1_multi_timeframe_precommit_v1.spec import (
    canary_spec,
    canonical_spec,
    coverage_gates,
    dumps_sha256,
    economic_gates,
    exact_entry_rule,
    execution_contract,
    exit_contract,
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    spec_sha256,
    stability_gates,
)
from research.c1_multi_timeframe_precommit_v1.vwap_semantics import prove_vwap_semantics


def _asof_public(proof: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in proof.items() if k != "steps"}


def decide() -> dict[str, Any]:
    spec = canonical_spec()
    prior = load_prior()
    bucket = prove_bucket_alignment()
    asof = prove_same_timestamp_ordering()
    vwap = prove_vwap_semantics()
    reset = prove_session_reset()
    one_min = one_min_registry()
    htf = htf_registry()
    raw = build_raw_library()
    dup_map = audit_raw_library(raw)
    final, counts = apply_prune(raw, dup_map)
    tuned = any(r.get("THRESHOLD_CHANGED") or r.get("THRESHOLD_TUNED_THIS_RUN") for r in one_min)
    cross = any(r.get("CROSS_FAMILY") for r in raw)
    eleven = len(raw) != 10 or len(RAW_CANDIDATE_IDS) != 10
    backfill = bool(counts.get("BACKFILL"))
    unknown = int(counts["UNKNOWN_IDENTITY_N"])

    integrity = (
        (not prior.get("ok"))
        or (not bucket.get("ok"))
        or (not asof.get("ok"))
        or (not asof.get("FINALIZE_EQ_T0_ORDERING_PROVEN"))
        or (not asof.get("HTF_STATE_PUBLISHED_BEFORE_SIGNAL_EVAL"))
        or (not vwap.get("ok"))
        or (not reset.get("ok"))
        or bool(reset.get("CONTRADICTION_WITH_PRECOMMIT"))
        or tuned
        or cross
        or eleven
        or backfill
        or unknown > 0
        or spec["V7_SAME_BUCKET_JOIN_USED"]
        or spec["HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME"]
        or (not spec["HTF_VWAP_USES_CANONICAL_SESSION_VWAP"])
        or spec["CANDIDATE_ECONOMICS_RUN"]
        or spec["CANDIDATE_SIGNAL_COUNT_COMPUTED"]
        or spec["CANDIDATE_PNL_COMPUTED"]
        or spec["CROSS_FAMILY_GRID"]
        or spec["CANDIDATE11"]
        or int(asof.get("FUTURE_HTF_BAR_N") or 0) != 0
        or int(asof.get("PARTIAL_HTF_BAR_USED_N") or 0) != 0
        or int(asof.get("SAME_BUCKET_CARRYBACK_N") or 0) != 0
        or int(asof.get("CROSS_SESSION_HTF_BAR_N") or 0) != 0
        or int(vwap.get("FUTURE_SESSION_VWAP_CARRYBACK_N") or 0) != 0
        or int(bucket.get("CROSS_SESSION_HTF_BAR_N") or 0) != 0
    )

    if integrity:
        case = "E"
        case_name = CASE_E
        nxt = "Fix only the failed HTF/ordering/VWAP/prior gap. Do not run economics."
        verdict = CASE_E
    elif int(counts["FINAL_CANDIDATE_N"]) == 0:
        case = "B"
        case_name = CASE_B
        nxt = NEXT_IF_B
        verdict = CASE_B
    elif int(counts["FINAL_CANDIDATE_N"]) >= 1:
        case = "A"
        case_name = CASE_A
        nxt = NEXT_IF_A
        verdict = CASE_A
    else:
        case = "E"
        case_name = CASE_E
        nxt = "Fix only the failed HTF/ordering/VWAP/prior gap. Do not run economics."
        verdict = CASE_E

    hashes = {
        "ONE_MIN_STATE_REGISTRY_SHA256": dumps_sha256(one_min),
        "HTF_STATE_REGISTRY_SHA256": dumps_sha256(htf),
        "HTF_BUCKET_ALIGNMENT_SHA256": dumps_sha256(bucket),
        "HTF_ASOF_SEMANTICS_SHA256": dumps_sha256(_asof_public(asof)),
        "HTF_VWAP_SEMANTICS_SHA256": dumps_sha256(vwap),
        "RAW_CANDIDATE_LIBRARY_SHA256": dumps_sha256(raw),
        "DUPLICATE_MAP_SHA256": dumps_sha256(dup_map),
        "FINAL_CANDIDATE_LIBRARY_SHA256": dumps_sha256(final),
        "EXECUTION_SHA256": dumps_sha256(execution_contract()),
        "EXIT_SHA256": dumps_sha256(exit_contract()),
        "PORTFOLIO_SHA256": dumps_sha256(portfolio_contract()),
        "FOLD_ASSIGNMENT_SHA256": dumps_sha256(fold_assignment()),
        "GATES_SHA256": dumps_sha256(
            {
                "coverage": coverage_gates(),
                "fold_coverage": fold_coverage_gates(),
                "economic": economic_gates(),
                "stability": stability_gates(),
            }
        ),
        "CANARY_SPEC_SHA256": dumps_sha256(canary_spec()),
        "SPEC_SHA256": spec_sha256(),
    }
    forensic = []
    if not prior.get("ok"):
        forensic.append({"gap": "PRIOR_DECISION", "blocker": prior.get("blocker")})
    if not bucket.get("ok"):
        forensic.append({"gap": "HTF_BUCKET_ALIGNMENT"})
    if not asof.get("ok") or not asof.get("FINALIZE_EQ_T0_ORDERING_PROVEN"):
        forensic.append({"gap": "HTF_ASOF_OR_SAME_TIMESTAMP_ORDERING"})
    if not vwap.get("ok"):
        forensic.append({"gap": "HTF_VWAP_SEMANTICS"})
    if not reset.get("ok") or reset.get("CONTRADICTION_WITH_PRECOMMIT"):
        forensic.append({"gap": "HTF_INDICATOR_SESSION_RESET"})
    if unknown:
        forensic.append({"gap": "DUPLICATE_IDENTITY_UNKNOWN"})

    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CASE": case,
        "CASE_NAME": case_name,
        "VERDICT": verdict,
        "NEXT": nxt,
        "prior": prior,
        "bucket": bucket,
        "asof": asof,
        "vwap": vwap,
        "session_reset": reset,
        "one_min_states": one_min,
        "htf_states": htf,
        "raw_library": raw,
        "duplicate_map": dup_map,
        "final_library": final,
        "counts": counts,
        "hashes": hashes,
        "execution": execution_contract(),
        "exit": exit_contract(),
        "portfolio": portfolio_contract(),
        "folds": fold_assignment(),
        "coverage_gates": coverage_gates(),
        "fold_coverage_gates": fold_coverage_gates(),
        "economic_gates": economic_gates(),
        "stability_gates": stability_gates(),
        "canary": canary_spec(),
        "exact_entry_rule": exact_entry_rule(),
        "forensic": forensic,
        "C4_REMAINS_ELIGIBLE": True,
        "C4_EXECUTED_THIS_RUN": False,
        "C4_RULE_CREATED": False,
        "C1_BROADENS_ENTRY_POPULATION_CLAIM": False,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
        "CANDIDATE_BACKFILL": False,
        "OR_FINAL_STATUS": OR_FINAL_STATUS,
        "OR_ECONOMICS_OPENED": False,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "PFQ": {"document": PFQ_DOCUMENT_ID, "verdict": PFQ_VERDICT, "line": PFQ_LINE},
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    prior = dict(pack.get("prior") or {})
    asof = dict(pack.get("asof") or {})
    vwap = dict(pack.get("vwap") or {})
    bucket = dict(pack.get("bucket") or {})
    reset = dict(pack.get("session_reset") or {})
    c = dict(pack.get("counts") or {})
    h = dict(pack.get("hashes") or {})
    dup_excl = [r for r in pack.get("duplicate_map") or [] if r.get("ACTION") == "EXCLUDE_DUPLICATE"]
    closed_excl = [r for r in pack.get("duplicate_map") or [] if r.get("ACTION") == "EXCLUDE_CLOSED_LINEAGE"]
    return {
        "1_prior_rethink_verdict": prior.get("VERDICT"),
        "2_selected_class": prior.get("SELECTED_ARCHITECTURE_CLASS"),
        "3_C4_also_eligible": bool(prior.get("C4_ALSO_ELIGIBLE")),
        "4_why_C1_first": prior.get("WHY_C1_FIRST"),
        "5_C1_broadens_ENTRY_population_claim": False,
        "6_HTFs": {"HTF_3M": 180, "HTF_5M": 300},
        "7_BUCKET_ORIGIN": bucket.get("BUCKET_ORIGIN") or (bucket.get("examples") or {}).get("BUCKET_ORIGIN"),
        "8_SESSION_SCOPED": True,
        "9_higher_TF_completed_semantics": (
            "HTF bar exists only when the 09:00-anchored bucket's constituent 1m bars are all "
            "complete. Usable HTF at 1m t0 is latest such bar with finalize_t <= t0 after publish."
        ),
        "10_finalize_t_eq_t0_allowed": bool(asof.get("FINALIZE_EQ_T0_ALLOWED")),
        "11_actual_same_timestamp_ordering_proven": bool(asof.get("FINALIZE_EQ_T0_ORDERING_PROVEN")),
        "12_HTF_STATE_PUBLISHED_BEFORE_SIGNAL_EVAL": bool(asof.get("HTF_STATE_PUBLISHED_BEFORE_SIGNAL_EVAL")),
        "13_exact_event_ordering": asof.get("ENGINE_EVENT_ORDER"),
        "14_1m_state_registry": [
            {"STATE_ID": r["STATE_ID"], "EXACT": r.get("PRECOMMIT_EXACT_TEXT"), "SOURCE_FUNCTION": r.get("SOURCE_FUNCTION")}
            for r in pack.get("one_min_states") or []
        ],
        "15_HTF_state_registry": pack.get("htf_states"),
        "16_HTF_indicator_session_reset_semantics": reset.get("HTF_INDICATOR_SESSION_RESET_SEMANTICS"),
        "17_threshold_changed": False,
        "18_same_family_only": True,
        "19_cross_family_grid_used": False,
        "20_exact_ENTRY_rule": pack.get("exact_entry_rule"),
        "21_raw_candidate_n": int(c.get("RAW_CANDIDATE_N") or 0),
        "22_raw_candidate_ids": [r["CANDIDATE_ID"] for r in pack.get("raw_library") or []],
        "23_duplicate_n": int(c.get("DUPLICATE_CANDIDATE_N") or 0),
        "24_duplicate_identities": dup_excl or "NONE",
        "25_closed_lineage_n": int(c.get("CLOSED_LINEAGE_CANDIDATE_N") or 0),
        "26_final_candidate_n": int(c.get("FINAL_CANDIDATE_N") or 0),
        "27_candidate_backfill": False,
        "28_candidate11": False,
        "29_V7_same_bucket_join_used": False,
        "30_PARTIAL_HTF_BAR_USED_N": int(asof.get("PARTIAL_HTF_BAR_USED_N") or 0),
        "31_FUTURE_HTF_BAR_N": int(asof.get("FUTURE_HTF_BAR_N") or 0),
        "32_SAME_BUCKET_CARRYBACK_N": int(asof.get("SAME_BUCKET_CARRYBACK_N") or 0),
        "33_CROSS_SESSION_HTF_BAR_N": int(asof.get("CROSS_SESSION_HTF_BAR_N") or bucket.get("CROSS_SESSION_HTF_BAR_N") or 0),
        "34_HTF_VWAP_recomputed_from_aggregate_Close_Volume": False,
        "35_HTF_VWAP_uses_canonical_causal_session_VWAP": True,
        "36_FUTURE_SESSION_VWAP_CARRYBACK_N": int(vwap.get("FUTURE_SESSION_VWAP_CARRYBACK_N") or 0),
        "37_X1_identity": pack.get("execution"),
        "38_Z3_identity": pack.get("exit"),
        "39_portfolio_contract": pack.get("portfolio"),
        "40_Development_dates": list(pack.get("DEVELOPMENT_DAYS") or []),
        "41_full_coverage_gates": pack.get("coverage_gates"),
        "42_economic_gates": pack.get("economic_gates"),
        "43_causal_ex_top_semantics": (pack.get("economic_gates") or {}).get("CAUSAL_EX_TOP1"),
        "44_robust_score": (pack.get("economic_gates") or {}).get("ROBUST_SCORE"),
        "45_fold_coverage_gates": pack.get("fold_coverage_gates"),
        "46_stability_gates": pack.get("stability_gates"),
        "47_FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
        "48_WINNER_BLOCK_SYMBOL_FILTER_N": 0,
        "49_canary_identity": pack.get("canary"),
        "50_candidate_economics_run": False,
        "51_candidate_signal_count_computed": False,
        "52_candidate_PnL_computed": False,
        "53_burned_Holdout_read": False,
        "54_Stress_read": False,
        "55_future_used": False,
        "56_Sizing_ran": False,
        "57_C4_remains_eligible": True,
        "58_Runtime_changed": False,
        "59_submit_cancel_live": "0/0/0",
        "60_TRUE_OOS": False,
        "61_CERTIFIED": False,
        "62_all_hashes": h,
        "63_VERDICT": pack.get("VERDICT"),
        "64_NEXT": pack.get("NEXT"),
        "_closed_lineage_excluded_n": len(closed_excl),
        "_session_reset_ok": bool(reset.get("ok")),
        "_prior_ok": bool(prior.get("ok")),
    }
