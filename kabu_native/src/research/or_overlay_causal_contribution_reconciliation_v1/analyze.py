"""Hard gates. No economics unless stream+identity+parity all PASS."""
from __future__ import annotations

from typing import Any

from research.or_overlay_causal_contribution_reconciliation_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E1,
    CASE_E2,
    NEXT_IF_E2,
    OR_CLOSED_STATUS,
    REQUIRED_STREAM_FIELDS,
    W27_OR_ERA_SESSIONS,
)
from research.or_overlay_causal_contribution_reconciliation_v1.audit import completeness_for_session


def _i(v: Any) -> int:
    try:
        if v is None or v == "":
            return 0
        return int(v)
    except (TypeError, ValueError):
        return 0


def summarize(audit: dict[str, Any]) -> dict[str, Any]:
    sessions = list(audit.get("sessions") or [])
    flags = [completeness_for_session(s) for s in sessions]
    present_n = sum(1 for f in flags if f["session_present"])
    jsonl_n = sum(1 for f in flags if f["jsonl_present"])
    missing = [
        f"{s['day']}/{s['session']}"
        for s, f in zip(sessions, flags)
        if not f["session_present"]
    ]
    field_all: dict[str, bool] = {}
    for name in REQUIRED_STREAM_FIELDS:
        field_all[name] = all(f.get(name) for f in flags) and present_n == len(sessions)

    total_eval = 0
    pbv2_accept = 0
    pbv2_reject = 0
    pbv2_cap = 0
    pbv2_cap_replayable = 0
    or_eval = 0
    or_accept = 0
    or_reject = 0
    or_reject_replayable = 0
    pbv2_reject_full_feature = 0
    config_shas: list[str] = []
    identities: list[dict[str, Any]] = []

    for s, f in zip(sessions, flags):
        ev = dict(s.get("events") or {})
        # CSV often emits candidate+rejected pairs; decision stream is accepted+rejected.
        total_eval += _i(ev.get("accepted_n")) + _i(ev.get("rejected_n"))
        pbv2_accept += _i(ev.get("entry_type_pbv2_n"))
        pbv2_reject += _i(ev.get("rejected_n"))
        cap_n = _i(ev.get("pbv2_cap_reject_n"))
        pbv2_cap += cap_n
        if (
            f["pbv2_internal_reason_column"]
            and f["event_timestamp"]
            and f["symbol"]
            and f["pbv2_lane_occupancy"]
            and f["exit_event"]
            and f["slot_release_event"]
        ):
            pbv2_cap_replayable += cap_n
        or_eval += _i(ev.get("or_overlay_not_candidate_n")) + _i(ev.get("or_cap_full_n"))
        or_accept += _i(ev.get("entry_type_or_n"))
        or_rej = _i(ev.get("or_cap_full_n")) + _i(ev.get("or_overlay_not_candidate_n"))
        or_reject += or_rej
        if f["or_predicate_inputs"] and f["event_timestamp"] and f["symbol"]:
            or_reject_replayable += or_rej
        pbv2_reject_full_feature += _i(ev.get("reject_with_near_high_and_update_n"))
        ident = dict(s.get("identity") or {})
        identities.append(ident)
        sha = str(ident.get("config_sha256") or "")
        if sha:
            config_shas.append(sha)

    unique_sha = sorted(set(config_shas))
    stream_complete = (
        present_n == len(sessions)
        and jsonl_n == len(sessions)
        and all(field_all.values())
    )
    cap5_provable = (
        stream_complete
        and pbv2_cap_replayable > 0
        and all(f["pbv2_internal_reason_column"] for f in flags)
        and all(f["pbv2_lane_occupancy"] for f in flags)
        and all(f["exit_event"] for f in flags)
        and all(f["slot_release_event"] for f in flags)
    )
    date_scoped = all(
        f["session_present"] and f["or_predicate_constants_in_snapshot"]
        for f in flags
    ) and present_n == len(sessions)
    # Historical contract changed if multiple config hashes among present sessions.
    runtime_changed = len(unique_sha) > 1

    recovery = dict(audit.get("recovery") or {})
    jsonl_recovered = _i(recovery.get("jsonl_n")) == len(W27_OR_ERA_SESSIONS)
    missing_0714 = any(m.startswith("20260714/") for m in missing)
    recoverable = (not stream_complete) and (
        jsonl_recovered or (not missing_0714 and present_n == len(sessions) and jsonl_n > 0)
    )
    # After one recovery search: jsonl_n==0 and 20260714 missing → not recoverable.
    if _i(recovery.get("jsonl_n")) == 0 and missing_0714:
        recoverable = False

    control_a_valid = stream_complete and date_scoped
    control_b_valid = control_a_valid and cap5_provable

    return {
        "present_session_n": present_n,
        "expected_session_n": len(sessions),
        "jsonl_session_n": jsonl_n,
        "missing_sessions": missing,
        "field_all": field_all,
        "COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE": stream_complete,
        "CONTROL_A_VALID": control_a_valid,
        "CONTROL_B_VALID": control_b_valid,
        "TOTAL_PBV2_EVALUATION_N": total_eval,
        "PBV2_ACCEPT_N": pbv2_accept,
        "PBV2_REJECT_N": pbv2_reject,
        "PBV2_CAP_REJECT_N": pbv2_cap,
        "PBV2_REJECT_WITH_FULL_FEATURE_STATE_N": pbv2_reject_full_feature,
        "PBV2_CAP_REJECT_REPLAYABLE_N": pbv2_cap_replayable,
        "PBV2_CAP5_COUNTERFACTUAL_PROVABLE": cap5_provable,
        "OR_EVALUATION_N": or_eval,
        "OR_ACCEPT_N": or_accept,
        "OR_REJECT_N": or_reject,
        "OR_REJECT_REPLAYABLE_N": or_reject_replayable,
        "DATE_SCOPED_RUNTIME_IDENTITY_PROVEN": date_scoped,
        "RUNTIME_CONFIG_CHANGED_WITHIN_OR_ERA": runtime_changed,
        "unique_config_sha256": unique_sha,
        "identities": identities,
        "session_flags": flags,
        "recovery_jsonl_n": _i(recovery.get("jsonl_n")),
        "integrity_gap_recoverable": recoverable,
        "OR_ACCEPT_IDENTITY_PARITY": False,
        "PBV2_ACCEPT_IDENTITY_PARITY": False,
        "LANE_IDENTITY_PARITY": False,
        "ENTRY_TIMESTAMP_PARITY": False,
        "FILL_IDENTITY_PARITY": False,
        "EXIT_IDENTITY_PARITY": False,
        "DAILY_PNL_PARITY": False,
        "FULL_CAUSAL_PARITY": False,
    }


def decide(audit: dict[str, Any]) -> dict[str, Any]:
    s = summarize(audit)
    stream = bool(s["COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE"])
    ident = bool(s["DATE_SCOPED_RUNTIME_IDENTITY_PROVEN"])
    parity = bool(s["FULL_CAUSAL_PARITY"])
    econ_opened = False
    if stream and ident and s["CONTROL_A_VALID"] and s["CONTROL_B_VALID"] and parity:
        # Economics still forbidden until those gates pass. They do not pass in this forensic.
        case = "A"
        case_name = CASE_A
        verdict = CASE_A
        nxt = "HOLD_FOR_G1_G7"
    elif s["integrity_gap_recoverable"] and not stream:
        case = "E1"
        case_name = CASE_E1
        verdict = CASE_E1
        nxt = "OR_OVERLAY_CANDIDATE_STREAM_RECOVERY_ONLY"
    else:
        case = "E2"
        case_name = CASE_E2
        verdict = CASE_E2
        nxt = NEXT_IF_E2
    if case == "A" and not (stream and ident and parity):
        raise RuntimeError("CASE_A_WITHOUT_GATES")
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CASE": case,
        "CASE_NAME": case_name,
        "VERDICT": verdict,
        "NEXT": nxt,
        "OR_ARCHITECTURE_STATUS": OR_CLOSED_STATUS if case == "E2" else "PREEXISTING_PARTIALLY_TESTED",
        "ECONOMICS_OPENED": econ_opened,
        "CONTROL_A_VALID": s["CONTROL_A_VALID"],
        "CONTROL_B_VALID": s["CONTROL_B_VALID"],
        "OR_SLEEVE_INCREMENTAL_SUPPORTED": False,
        "PRODUCTION_4_PLUS_1_POLICY_SUPPORTED": False,
        "OR_SLEEVE_INCREMENTAL_STATUS": "NOT_PROVABLE",
        "PRODUCTION_4_PLUS_1_POLICY_STATUS": "NOT_PROVABLE",
        "CAUSAL_EX_TOP_DELTA_SYMBOL": "NOT_PROVABLE",
        "OR_SLEEVE_CAUSAL_DELTA": "NOT_PROVABLE",
        "OR_VS_PBV2_SLOT_DELTA": "NOT_PROVABLE",
        "G1_G7": "NOT_EVALUATED",
        "coverage_pass": False,
        "CASE_A_ALLOWED": False,
        "summary": s,
        "CASE_B": CASE_B,
        "CASE_C": CASE_C,
        "CASE_D": CASE_D,
    }


def build_answers(pack: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    s = dict(pack.get("summary") or {})
    rec = dict(audit.get("recovery") or {})
    return {
        "1_why_this_run": (
            "Reconcile Production OR overlay causal contribution vs PBv2-only under exact "
            "PBv2-reject fallback and cap split 4+1. Not standalone Open Strength."
        ),
        "2_production_or_role": "PBv2 reject fallback overlay; not a standalone strategy",
        "3_cap_semantics": "TREATMENT cap_pbv2=4 cap_or=1; CONTROL_A PBv2=4 OR off unused 5th; CONTROL_B PBv2=5 OR off",
        "4_entry_architecture": (
            "O_R003 CurrentPrice near HighPrice DAY_HIGH_NEAR_PCT + update_count<=8; "
            "OS9 or day_leader; overlay only after PBv2 reject"
        ),
        "5_classic_opening_range_breakout": False,
        "6_standalone_open_strength_preexisting": False,
        "7_w27_combined_book": (
            "Phase687W27 all_paper A_CURRENT PnL=-252470 PF=0.8796 OR=73; "
            "OR-era PnL=-96270 PF=0.9023 OR=73. Combined book, not isolated sleeve Full Causal."
        ),
        "8_or_sleeve_isolated_full_causal_already_run": False,
        "9_three_portfolios": ["TREATMENT", "CONTROL_A", "CONTROL_B"],
        "10_treatment": "Production PBv2 cap4 + OR cap1",
        "11_control_a": "PBv2 cap4 + OR disabled; 5th slot unused",
        "12_control_b": "PBv2 cap5 + OR disabled",
        "13_q1": "OR_SLEEVE_CAUSAL_DELTA = TREATMENT - CONTROL_A",
        "14_q2": "OR_VS_PBV2_SLOT_DELTA = TREATMENT - CONTROL_B",
        "15_or_era_days": list(audit.get("or_era_days") or []),
        "16_expected_session_n": len(W27_OR_ERA_SESSIONS),
        "17_present_session_n": s.get("present_session_n"),
        "18_jsonl_session_n": s.get("jsonl_session_n"),
        "19_missing_sessions": s.get("missing_sessions"),
        "20_lane_occupancy_logged": bool((s.get("field_all") or {}).get("pbv2_lane_occupancy"))
        and bool((s.get("field_all") or {}).get("or_lane_occupancy")),
        "21_fill_event_present": bool((s.get("field_all") or {}).get("fill_event")),
        "22_exit_and_slot_release": {
            "exit": bool((s.get("field_all") or {}).get("exit_event")),
            "slot_release": bool((s.get("field_all") or {}).get("slot_release_event")),
        },
        "23_same_symbol_state_complete": bool((s.get("field_all") or {}).get("same_symbol_state")),
        "24_or_predicate_inputs_complete": bool((s.get("field_all") or {}).get("or_predicate_inputs")),
        "25_pbv2_internal_reason_all_sessions": all(
            f.get("pbv2_internal_reason_column") for f in (s.get("session_flags") or [])
        ),
        "26_control_a_valid": pack.get("CONTROL_A_VALID"),
        "27_control_b_valid": pack.get("CONTROL_B_VALID"),
        "28_economics_opened": pack.get("ECONOMICS_OPENED"),
        "29_or_sleeve_causal_delta": pack.get("OR_SLEEVE_CAUSAL_DELTA"),
        "30_or_vs_pbv2_slot_delta": pack.get("OR_VS_PBV2_SLOT_DELTA"),
        "31_causal_ex_top_delta_symbol": pack.get("CAUSAL_EX_TOP_DELTA_SYMBOL"),
        "32_full_causal_parity": s.get("FULL_CAUSAL_PARITY"),
        "33_coverage_pass": pack.get("coverage_pass"),
        "34_g1_g7": pack.get("G1_G7"),
        "35_case_a_allowed": pack.get("CASE_A_ALLOWED"),
        "36_runtime_changed_this_run": False,
        "37_submit_cancel_live": "0/0/0",
        "38_case": pack.get("CASE_NAME"),
        "39_verdict": pack.get("VERDICT"),
        "40_next": pack.get("NEXT"),
        "41_stress_raw_read": False,
        "42_future_used": False,
        "43_max_new_data_date": "NONE",
        "44_new_architecture_created": False,
        "45_standalone_open_strength_constructed": False,
        "46_current_config_carried_back": False,
        "47_called_economic_fail": False,
        "48_or_architecture_status": pack.get("OR_ARCHITECTURE_STATUS"),
        "49_COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE": s.get("COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE"),
        "50_total_pbv2_evaluation_n": s.get("TOTAL_PBV2_EVALUATION_N"),
        "51_pbv2_reject_n": s.get("PBV2_REJECT_N"),
        "52_pbv2_cap_reject_n": s.get("PBV2_CAP_REJECT_N"),
        "53_replayable_pbv2_cap_reject_n": s.get("PBV2_CAP_REJECT_REPLAYABLE_N"),
        "54_PBV2_CAP5_COUNTERFACTUAL_PROVABLE": s.get("PBV2_CAP5_COUNTERFACTUAL_PROVABLE"),
        "55_or_evaluation_n": s.get("OR_EVALUATION_N"),
        "56_or_reject_n": s.get("OR_REJECT_N"),
        "57_or_reject_replayable_n": s.get("OR_REJECT_REPLAYABLE_N"),
        "58_DATE_SCOPED_RUNTIME_IDENTITY_PROVEN": s.get("DATE_SCOPED_RUNTIME_IDENTITY_PROVEN"),
        "59_runtime_config_changed_within_or_era": s.get("RUNTIME_CONFIG_CHANGED_WITHIN_OR_ERA"),
        "60_if_changed_date_scoped_identities": s.get("identities"),
        "61_OR_ACCEPT_IDENTITY_PARITY": s.get("OR_ACCEPT_IDENTITY_PARITY"),
        "62_PBV2_ACCEPT_IDENTITY_PARITY": s.get("PBV2_ACCEPT_IDENTITY_PARITY"),
        "63_FILL_IDENTITY_PARITY": s.get("FILL_IDENTITY_PARITY"),
        "64_EXIT_IDENTITY_PARITY": s.get("EXIT_IDENTITY_PARITY"),
        "65_FULL_CAUSAL_PARITY": s.get("FULL_CAUSAL_PARITY"),
        "66_OR_SLEEVE_INCREMENTAL_SUPPORTED": pack.get("OR_SLEEVE_INCREMENTAL_SUPPORTED"),
        "67_PRODUCTION_4_PLUS_1_POLICY_SUPPORTED": pack.get("PRODUCTION_4_PLUS_1_POLICY_SUPPORTED"),
        "68_integrity_gap_recoverable": s.get("integrity_gap_recoverable"),
        "recovery_search": rec,
        "PBV2_ACCEPT_N": s.get("PBV2_ACCEPT_N"),
        "OR_ACCEPT_N": s.get("OR_ACCEPT_N"),
        "PBV2_REJECT_WITH_FULL_FEATURE_STATE_N": s.get("PBV2_REJECT_WITH_FULL_FEATURE_STATE_N"),
    }
