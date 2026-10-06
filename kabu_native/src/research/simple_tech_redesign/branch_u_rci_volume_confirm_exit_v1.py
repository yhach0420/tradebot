"""Offline UNPROVEN_RCI_THEN_DOWN_VOLUME_EXPANSION_V1. Phase A then optional Full Causal. Research only."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import CANCEL_N, ELIGIBLE_DAYS, LIVE_ORDER_N, SESSION, SUBMIT_N
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_causal_harvest import _add_arm, _empty_arm
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.exit_composition_shift_rca_analyze import load_residual_rows
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import residual_index
from research.simple_tech_redesign.isolation import (
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT,
    BRANCH_U_OUT,
    BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT,
    BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT,
    BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT,
    E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT,
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT,
    ENTRY_THESIS_INVALIDATION_RCA_OUT,
    LIFECYCLE_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.branch_u_rci_volume_confirm_exit_v1_analyze import decide, decide_phase_a, evaluate_cohort
from research.simple_tech_redesign.branch_u_rci_volume_confirm_exit_v1_harvest import (
    duplicate_architecture_audit,
    harvest_day,
    harvest_unconstrained_day,
    load_unconstrained_identity,
    phase_a_gates,
    recover_sequence_predicates,
    unconstrained_class_counts,
)
from research.simple_tech_redesign.branch_u_rci_volume_confirm_exit_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.branch_u_rci_volume_confirm_exit_v1_spec import (
    ANALYSIS_ID,
    AND_OR_SEARCH,
    AUTO_HOP_NEXT_PAIR,
    BE_SEMANTICS,
    CANDIDATE_ID,
    COMBINATION_SEARCH,
    CONTROL_EXIT,
    EXECUTION,
    FIRST_PROSPECTIVE_DAY,
    FORBIDDEN_INPUT_DAYS,
    K_SEARCH,
    MAX_RESEARCH_DATE,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    PAIR_ENUMERATION,
    PERSISTENCE_SEARCH,
    PNL_SELECTION,
    PROSPECTIVE_HARVEST_SUSPENDED,
    RESIDUAL_PRIOR_ANALYSIS,
    RESIDUAL_PRIOR_VERDICT,
    REVERSE_SEQUENCE,
    SIGNAL,
    STATE_MACHINE,
    STATIC_AND,
    THIS_ORIGIN,
    THRESHOLD_SEARCH,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
    TRUE_OOS,
    V29_ORIGIN_FROZEN,
    ACTIONABILITY_STOP,
    VWAP_PRIOR_ANALYSIS,
    VWAP_PRIOR_CASE,
    VWAP_PRIOR_VERDICT,
    candidate_identity_hash,
    canonical_candidate_spec,
    source_sha256_candidate,
    spec_sha256_candidate,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_analyze import capture_reporting_state

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "RESEARCH_WRITE_PATH_OVERLAP_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "INPUT_20260903_N",
    "INPUT_20260904_N",
    "FUTURE_DATA_N",
    "PROSPECTIVE_HARVESTER_RUN_N",
    "PRIOR_ARTIFACT_MUTATED_N",
    "FUTURE_QUOTE_CARRYBACK_N",
    "FUTURE_TIMESTAMP_CARRYBACK_N",
    "PRE_FILL_EXIT_N",
    "POST_BE_U_TRIGGER_N",
    "PREDICATE_DRIFT_N",
    "TF1_MISSING_N",
    "VOLUME_ARRAY_MISSING_N",
    "FILL_PACK_INCOMPLETE_N",
    "STATIC_AND_SAME_BAR_EXIT_N",
    "THRESHOLD_SEARCH_N",
    "PERSISTENCE_SEARCH_N",
    "K_SEARCH_N",
    "AND_OR_SEARCH_N",
    "REVERSE_SEQUENCE_N",
    "PAIR_ENUMERATION_N",
    "PNL_SELECTION_N",
    "NEW_ENTRY_FILTER_N",
    "NEW_EXIT_RULE_N",
    "AUTO_HOP_N",
    "VWAP_CONFIRM_N",
    "EMA_CONFIRM_N",
)
PRIOR_OUTS = (
    LIFECYCLE_OUT,
    BRANCH_U_OUT,
    BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT,
    BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT,
    BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
    E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
    ENTRY_THESIS_INVALIDATION_RCA_OUT,
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT,
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _prior_hashes() -> dict[str, str]:
    out = {}
    for folder in PRIOR_OUTS:
        for name in ("report.json", "report.md", "audit.xlsx"):
            out[f"{folder.name}/{name}"] = _sha_file(folder / name)
    return out


def _merge(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    ctrl = _empty_arm()
    treat = _empty_arm()
    rows: list[dict[str, Any]] = []
    causes: list[dict[str, Any]] = []
    leftover_ok = True
    control_sot_ok = True
    treatment_sot_ok = True
    for body in bodies:
        _add_arm(ctrl, dict(body.get("control") or {}))
        _add_arm(treat, dict(body.get("treatment") or {}))
        rows.extend(list(body.get("rows") or []))
        causes.extend(list(body.get("incremental_causes") or []))
        leftover_ok = leftover_ok and bool(body.get("leftover_ok"))
        control_sot_ok = control_sot_ok and bool(body.get("control_sot_ok"))
        treatment_sot_ok = treatment_sot_ok and bool(body.get("treatment_sot_ok"))
    return {
        "control": ctrl,
        "treatment": treat,
        "rows": rows,
        "causes": causes,
        "leftover_ok": leftover_ok,
        "control_sot_ok": control_sot_ok,
        "treatment_sot_ok": treatment_sot_ok,
    }


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def _public_pred(pred: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pred.items() if k not in {"pred", "primitive_true_probe"}}


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    ident_d = dict(dev.get("identity") or {})
    ident_f = dict(fwd.get("identity") or {})
    pred = dict(report.get("predicate") or {})
    rci = dict(pred.get("RCI") or {})
    vol = dict(pred.get("VOLUME") or {})
    unc = dict(report.get("unconstrained_audit") or {})
    byc = dict(unc.get("by_class") or {})
    overall = dict(unc.get("overall") or {})
    ident232 = dict(unc.get("identity") or report.get("identity_232") or {})
    incr = dict(dev.get("incremental") or {})
    conc_d = dict(dev.get("concentration") or {})
    conc_f = dict(fwd.get("concentration") or {})
    gates = dict(dec.get("gates") or {})
    phase_a = dict(report.get("phase_a") or {})
    dup = dict(report.get("duplicate_architecture") or {})
    phase_b = bool(dec.get("PHASE_B_RAN"))
    na = "PHASE_B_NOT_RUN"
    return {
        "objective_alignment": (
            "keep fill coverage; tolerate some ENTRY quality dilution; process failure with Technical EXIT; "
            "improve Full Causal Portfolio PnL/PF/MaxDD; then Sizing. This run is not an ENTRY filter. "
            "ENTRY/Execution/CAP/Sizing frozen. New technical axis is participation/volume only."
        ),
        "already_executed_check": (
            f"DUPLICATE_ARCHITECTURE={dup.get('DUPLICATE_ARCHITECTURE')} compared={dup.get('compared')} "
            "state-machine/predicate identity, not name match."
        ),
        "why_current_inventory_exhausted": (
            f"RCI-then-VWAP prior={VWAP_PRIOR_ANALYSIS} CASE {VWAP_PRIOR_CASE} verdict={VWAP_PRIOR_VERDICT}. "
            "Slot release alone did not solve Burned; Burned direct itself was negative. "
            "VWAP reverse/threshold/TF change forbidden. Single-primitive U line already CLOSED. "
            "Existing EXIT inventory is EMA/BB/RCI/VWAP/price structure/swing/BE lifecycle."
        ),
        "why_volume_is_new": (
            "Participation/volume is not in the Branch U EXIT confirm inventory. "
            "V26 F_VOLUME_DETERIORATION is close>ema9 AND volume decrease. ENTRY S5 is median*mult. "
            "This confirm is strict down-close AND volume expansion after RCI ARM."
        ),
        "exact_RCI_source": (
            f"FILE={rci.get('SOURCE_FILE')} FN={rci.get('SOURCE_FUNCTION')} LINE={rci.get('SOURCE_LINE')} "
            f"SHA={rci.get('SOURCE_SHA')} PRED={rci.get('RCI_EXACT_PREDICATE')} EVENT={rci.get('RCI_EVENT_SEMANTICS')}"
        ),
        "exact_volume_source": (
            f"FILE={vol.get('SOURCE_FILE')} FN={vol.get('SOURCE_FUNCTION')} SHA={vol.get('SOURCE_SHA')} "
            f"ARRAY={vol.get('ARRAY_SOURCE')} CONFIRM_FN={vol.get('CONFIRM_SOURCE_FUNCTION')} "
            f"PRED={vol.get('VOLUME_EXACT_PREDICATE')} EVENT={vol.get('VOLUME_EVENT_SEMANTICS')}"
        ),
        "volume_availability": f"{unc.get('volume_availability')}",
        "state_machine": f"{STATE_MACHINE}",
        "tie_semantics": f"SOURCE={TIE_RESOLUTION_SOURCE} RULE={TIE_RESOLUTION_RULE} BE={BE_SEMANTICS}",
        "identity_232": (
            f"TOTAL={ident232.get('TOTAL_RESEARCH_FILL_N')} CORE={ident232.get('CORE_FILL_N')} "
            f"ADDED={ident232.get('ADDED_FILL_N')} hash={ident232.get('RESEARCH_FILL_SET_HASH')} ok={ident232.get('ok')}"
        ),
        "U_EARLY_arm_confirm": (
            f"ARM={(byc.get('U_EARLY_NEVER_BE') or {}).get('RCI_ARM_N')} "
            f"CONFIRM={(byc.get('U_EARLY_NEVER_BE') or {}).get('RCI_THEN_VOLUME_CONFIRM_N')} "
            f"BE_BETWEEN={(byc.get('U_EARLY_NEVER_BE') or {}).get('BE_AFTER_RCI_BEFORE_CONFIRM_N')}"
        ),
        "P_EARLY_arm_confirm": (
            f"ARM={(byc.get('P_EARLY_AFTER_BE') or {}).get('RCI_ARM_N')} "
            f"CONFIRM={(byc.get('P_EARLY_AFTER_BE') or {}).get('RCI_THEN_VOLUME_CONFIRM_N')} "
            f"BE_BETWEEN={(byc.get('P_EARLY_AFTER_BE') or {}).get('BE_AFTER_RCI_BEFORE_CONFIRM_N')}"
        ),
        "GOOD_confirm": f"CONFIRM={(byc.get('PROTECTED_GOOD') or {}).get('RCI_THEN_VOLUME_CONFIRM_N')} BE_BETWEEN={(byc.get('PROTECTED_GOOD') or {}).get('BE_AFTER_RCI_BEFORE_CONFIRM_N')}",
        "DIP_confirm": f"CONFIRM={(byc.get('PROTECTED_DIP') or {}).get('RCI_THEN_VOLUME_CONFIRM_N')} BE_BETWEEN={(byc.get('PROTECTED_DIP') or {}).get('BE_AFTER_RCI_BEFORE_CONFIRM_N')}",
        "BE_between_stage": f"overall={overall.get('BE_AFTER_RCI_BEFORE_CONFIRM_N')} by_class={[ (k, (byc.get(k) or {}).get('BE_AFTER_RCI_BEFORE_CONFIRM_N')) for k in ('U_EARLY_NEVER_BE','P_EARLY_AFTER_BE','PROTECTED_GOOD','PROTECTED_DIP') ]}",
        "Phase_A_gates": f"{phase_a.get('gates')} passed={phase_a.get('passed')} hit_day_n={phase_a.get('hit_day_n')} top_day={phase_a.get('top_hit_day')} top_symbol={phase_a.get('top_hit_symbol')} PNL_USED={phase_a.get('PNL_USED_FOR_SELECTION')}",
        "Phase_A_verdict": f"{dec.get('VERDICT') if not phase_b else 'PHASE_A_PASSED'}",
        "Phase_B_ran": f"{phase_b}",
        "Control_DEV_Burned_identity": (
            f"DEV fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}; "
            f"Burned fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} PnL={ident_f.get('pnl')} ok={ident_f.get('ok')}"
            if phase_b
            else na
        ),
        "U_EARLY_direct": _n(dev.get("U_EARLY_NEVER_BE_DIRECT_DELTA")) if phase_b else na,
        "P_EARLY_direct": _n(dev.get("P_EARLY_DIRECT_DELTA")) if phase_b else na,
        "GOOD_direct": _n(dev.get("GOOD_DIRECT_DELTA")) if phase_b else na,
        "DIP_direct": _n(dev.get("DIP_DIRECT_DELTA")) if phase_b else na,
        "PTF_direct": _n(dev.get("PTF_DIRECT_DELTA")) if phase_b else na,
        "A_B_C_TOTAL": (
            f"A={_n(dev.get('DIRECT_EXIT_DELTA'))} B={_n(dev.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))} "
            f"C={_n(dev.get('DISPLACED_TRADE_DELTA'))} TOTAL={_n(dev.get('TOTAL_CAUSAL_DELTA'))} decomp_ok={dev.get('decomp_ok')}"
            if phase_b
            else na
        ),
        "incremental_fill_economics": (
            f"N/PnL/PF/win/median/CORE/ADDED/GOOD/EARLY/DIP/PTF/OTHER="
            f"{incr.get('incremental_fill_n')}/{_n(incr.get('incremental_pnl'))}/{incr.get('PF')}/"
            f"{_n(incr.get('win_rate'))}/{_n(incr.get('median_pnl'))}/"
            f"{incr.get('CORE_n')}/{incr.get('ADDED_n')}/"
            f"{incr.get('GOOD')}/{incr.get('EARLY')}/{incr.get('DIP')}/{incr.get('PTF')}/{incr.get('OTHER')} "
            f"EARLY_n={incr.get('incremental_EARLY_n')} EARLY_pnl={_n(incr.get('incremental_EARLY_pnl'))}"
            if phase_b
            else na
        ),
        "DEV_PnL_PF_MaxDD": (
            f"PnL C/T={_n((dev.get('control') or {}).get('total_pnl'))}/{_n((dev.get('treatment') or {}).get('total_pnl'))} "
            f"PF={(dev.get('control') or {}).get('PF')}/{(dev.get('treatment') or {}).get('PF')} "
            f"MaxDD={_n((dev.get('control') or {}).get('max_drawdown'))}/{_n((dev.get('treatment') or {}).get('max_drawdown'))}"
            if phase_b
            else na
        ),
        "Burned_PnL_PF_MaxDD": (
            f"PnL C/T={_n((fwd.get('control') or {}).get('total_pnl'))}/{_n((fwd.get('treatment') or {}).get('total_pnl'))} "
            f"PF={(fwd.get('control') or {}).get('PF')}/{(fwd.get('treatment') or {}).get('PF')} "
            f"MaxDD={_n((fwd.get('control') or {}).get('max_drawdown'))}/{_n((fwd.get('treatment') or {}).get('max_drawdown'))}"
            if phase_b
            else na
        ),
        "day_concentration": f"DEV={conc_d.get('day')} warn={conc_d.get('single_day_contribution_gt_50pct')} Burned={conc_f.get('day')} PhaseA_U={unc.get('concentration')}",
        "symbol_concentration": f"DEV={conc_d.get('symbol')} warn={conc_d.get('single_symbol_contribution_gt_50pct')} Burned={conc_f.get('symbol')}",
        "verdict": f"{dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "candidate_frozen": f"{dec.get('CANDIDATE_FROZEN')} id={CANDIDATE_ID}",
        "technical_exit_development_exhausted": f"{dec.get('CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED')}",
        "ENTRY_changed": "false",
        "Execution_changed": "false E4_THEN_ASK_CROSS_W5 held",
        "CAP_changed": "false",
        "Sizing_changed": "false",
        "Runtime_changed": "false research-only",
        "future_data_used": "false",
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "prospective_suspended": str(PROSPECTIVE_HARVEST_SUSPENDED),
        "TRUE_OOS": "false",
        "CERTIFIED": "false",
        "submit_cancel_live": f"{report.get('submit_cancel_live')}",
        "next": f"{dec.get('NEXT')}",
        "gates_all": f"{gates} all_dev={dec.get('all_development_gates')} burned_ok={dec.get('burned_direction_ok')}",
        "origin_vs_v29": f"this={THIS_ORIGIN} v29={V29_ORIGIN_FROZEN}",
    }


def _write(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))


def _base_report(
    *,
    pre: dict[str, Any],
    leak: dict[str, Any],
    sha: str,
    source_sha: str,
    ident_hash: str,
    prior: dict[str, str],
    pred: dict[str, Any],
    decision: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "analysis_id": ANALYSIS_ID,
        "candidate_id": CANDIDATE_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "candidate_identity_hash": ident_hash,
        "canonical_spec": canonical_candidate_spec(),
        "state_machine": STATE_MACHINE,
        "predicate": _public_pred(pred),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "CANDIDATE_FROZEN": decision.get("CANDIDATE_FROZEN"),
            "FAMILY_CLOSED": decision.get("FAMILY_CLOSED"),
            "PHASE_B_RAN": decision.get("PHASE_B_RAN"),
            "CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED": decision.get(
                "CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED"
            ),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "CANDIDATE_IDENTITY_HASH": ident_hash,
            "PARENT_SPEC_SHA256": PARENT_SPEC_SHA256_EXPECTED,
            "first_eligible_prospective_date": FIRST_PROSPECTIVE_DAY,
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "EXECUTION_CHANGED": False,
            "CAP_CHANGED": False,
            "SIZING_CHANGED": False,
            "NEW_EXIT_RULE": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "CONTROL_EXIT": CONTROL_EXIT,
            "AUTO_HOP": False,
            "STATIC_AND": False,
            "REVERSE_SEQUENCE": False,
            "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
            "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
        },
        "decision": decision,
        "development": {},
        "forward": {},
        "leak": leak,
        "preflight": pre,
        "submit_cancel_live": {"submit": int(SUBMIT_N), "cancel": int(CANCEL_N), "live_order": int(LIVE_ORDER_N)},
        "prior_hashes": prior,
        "answers": {},
        "_markdown": "",
    }
    if extra:
        report.update(extra)
    return report


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, ident_hash: str, prior: dict[str, str], pred: dict[str, Any], extra: dict[str, Any] | None = None) -> int:
    decision = {
        "CASE": "E",
        "VERDICT": "SIMPLE_TECH_BRANCH_U_RCI_VOLUME_INTEGRITY_FAILED",
        "NEXT": msg,
        "CANDIDATE_FROZEN": False,
        "FAMILY_CLOSED": False,
        "PHASE_B_RAN": False,
        "CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "gates": {"1_integrity": False},
    }
    report = _base_report(
        pre=pre, leak=leak, sha=sha, source_sha=source_sha, ident_hash=ident_hash, prior=prior, pred=pred, decision=decision, extra=extra
    )
    report["blocker"] = msg
    _write(report)
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256_candidate()
    source_sha = source_sha256_candidate()
    ident_hash = candidate_identity_hash(spec_sha=sha, source_sha=source_sha)
    pre = snapshot(phase="PRE")
    prior = _prior_hashes()
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["NEW_ENTRY_FILTER_N"] = int(bool(NEW_ENTRY_FILTER))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["PERSISTENCE_SEARCH_N"] = int(bool(PERSISTENCE_SEARCH))
    leak["K_SEARCH_N"] = int(bool(K_SEARCH))
    leak["AND_OR_SEARCH_N"] = int(bool(AND_OR_SEARCH or STATIC_AND))
    leak["REVERSE_SEQUENCE_N"] = int(bool(REVERSE_SEQUENCE))
    leak["PAIR_ENUMERATION_N"] = int(bool(PAIR_ENUMERATION))
    leak["PNL_SELECTION_N"] = int(bool(PNL_SELECTION))
    leak["AUTO_HOP_N"] = int(bool(AUTO_HOP_NEXT_PAIR))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["FUTURE_DATA_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT candidate={CANDIDATE_ID} spec={sha[:12]} today={TODAY} signal={SIGNAL} exec={EXECUTION}", flush=True)

    dup = duplicate_architecture_audit()
    if not dup.get("ok") or bool(dup.get("DUPLICATE_ARCHITECTURE")):
        return _stop(
            f"STOP. Duplicate architecture: {dup.get('blocker')}. Do not re-run.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred={},
            extra={"duplicate_architecture": dup},
        )

    pred_pack = recover_sequence_predicates()
    if not pred_pack.get("ok"):
        leak["PREDICATE_DRIFT_N"] = 1
        return _stop(
            f"STOP. Exact RCI/volume predicate restore failed: {pred_pack.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )

    residual_req = dict(_load(BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT / "report.json").get("required") or {})
    if str(residual_req.get("VERDICT") or "") != RESIDUAL_PRIOR_VERDICT:
        return _stop(
            "STOP. Prior residual actionability is not EXHAUSTED.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    vwap_req = dict(_load(BRANCH_U_RCI_THEN_VWAP_EXIT_V1_OUT / "report.json").get("required") or {})
    if str(vwap_req.get("VERDICT") or "") != VWAP_PRIOR_VERDICT:
        return _stop(
            "STOP. Prior RCI-then-VWAP is not CASE C PORTFOLIO_FAILED.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop(
            "STOP. Preflight failed.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or PERSISTENCE_SEARCH or bool(TRUE_OOS) or NEW_ENTRY_FILTER:
        return _stop(
            "STOP. Forbidden flags.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    if AUTO_HOP_NEXT_PAIR or COMBINATION_SEARCH or AND_OR_SEARCH or STATIC_AND or REVERSE_SEQUENCE or PAIR_ENUMERATION or K_SEARCH or PNL_SELECTION or ACTIONABILITY_STOP or not PROSPECTIVE_HARVEST_SUSPENDED:
        return _stop(
            "STOP. Auto-hop / combination / reverse / PnL / prospective flag.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop(
            "STOP. Eligible days include today.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop(
            "STOP. Day list includes 20260903+.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop(
            "STOP. Research write path overlaps live paths.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(LIFECYCLE_OUT / "report.json"), str(BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop(
            "STOP. Active capture input.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )

    ident232 = load_unconstrained_identity()
    if not ident232.get("ok"):
        return _stop(
            f"STOP. Unconstrained 232 identity mismatch: {ident232.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in list(ident232.get("rows") or []):
        by_day[str(r.get("date") or "")].append(r)
    unc_rows: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(
                f"STOP. Forbidden unconstrained day: {day} {blocker}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                ident_hash=ident_hash,
                prior=prior,
                pred=pred_pack,
                extra={"duplicate_architecture": dup},
            )
        body = harvest_unconstrained_day(day, list(by_day.get(str(day)) or []), spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            return _stop(
                f"STOP. Unconstrained harvest fail {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                ident_hash=ident_hash,
                prior=prior,
                pred=pred_pack,
                extra={"duplicate_architecture": dup},
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        unc_rows.extend(list(body.get("rows") or []))
    if len(unc_rows) != int(ident232.get("TOTAL_RESEARCH_FILL_N") or 0):
        return _stop(
            f"STOP. Unconstrained sequence row count {len(unc_rows)} != {ident232.get('TOTAL_RESEARCH_FILL_N')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup},
        )
    unc_audit = unconstrained_class_counts(unc_rows)
    unc_audit["identity"] = {
        "ok": True,
        "TOTAL_RESEARCH_FILL_N": ident232.get("TOTAL_RESEARCH_FILL_N"),
        "CORE_FILL_N": ident232.get("CORE_FILL_N"),
        "ADDED_FILL_N": ident232.get("ADDED_FILL_N"),
        "RESEARCH_FILL_SET_HASH": ident232.get("RESEARCH_FILL_SET_HASH"),
    }
    after_phase_a_hash = _prior_hashes()
    if after_phase_a_hash != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        return _stop(
            "STOP. Prior closed-family artifacts mutated during Phase A.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup, "unconstrained_audit": unc_audit},
        )
    leak_ok_phase_a = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    phase_a = phase_a_gates(
        unc_audit,
        identity_ok=True,
        leak_ok=leak_ok_phase_a,
        pred_ok=True,
        dup_ok=not bool(dup.get("DUPLICATE_ARCHITECTURE")),
    )
    print(
        f"PHASE A ARM={unc_audit['overall'].get('RCI_ARM_N')} CONFIRM={unc_audit['overall'].get('RCI_THEN_VOLUME_CONFIRM_N')} "
        f"U_CONFIRM={phase_a.get('U_EARLY_CONFIRM_N')} P_CONFIRM={phase_a.get('P_EARLY_CONFIRM_N')} "
        f"GOOD={phase_a.get('GOOD_CONFIRM_N')} DIP={phase_a.get('DIP_CONFIRM_N')} passed={phase_a.get('passed')}",
        flush=True,
    )
    if not phase_a.get("passed"):
        decision = decide_phase_a(phase_a)
        report = _base_report(
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            decision=decision,
            extra={
                "duplicate_architecture": dup,
                "unconstrained_audit": unc_audit,
                "identity_232": unc_audit.get("identity"),
                "phase_a": phase_a,
                "postflight": snapshot(phase="POST"),
                "CANDIDATE_SPEC_FROZEN_AFTER_PHASE_A": False,
            },
        )
        _write(report)
        print(
            f"FINAL ANALYSIS_ID={ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
            f"EXHAUSTED={decision.get('CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED')} "
            f"PHASE_B_RAN=false out={BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT}",
            flush=True,
        )
        print("STOP.", flush=True)
        return 0

    print(f"PHASE A PASS. Freeze spec={sha}. Proceed Full Causal. No predicate change.", flush=True)

    try:
        residual_dev, residual_fwd, _residual_body = load_residual_rows()
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(
            f"STOP. Residual SoT: {exc}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup, "unconstrained_audit": unc_audit, "phase_a": phase_a},
        )

    harvested_ok = True
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(
                f"STOP. Forbidden DEV day: {day} {blocker}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                ident_hash=ident_hash,
                prior=prior,
                pred=pred_pack,
                extra={"duplicate_architecture": dup, "phase_a": phase_a},
            )
        body = harvest_day(day, cohort="DEVELOPMENT", spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail DEVELOPMENT {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                ident_hash=ident_hash,
                prior=prior,
                pred=pred_pack,
                extra={"duplicate_architecture": dup, "phase_a": phase_a},
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        dev_bodies.append(body)
    for day in list(LOCKED_SERIES_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(
                f"STOP. Forbidden BURNED day: {day} {blocker}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                ident_hash=ident_hash,
                prior=prior,
                pred=pred_pack,
                extra={"duplicate_architecture": dup, "phase_a": phase_a},
            )
        body = harvest_day(day, cohort="FORWARD_BURNED", spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail BURNED_STRESS {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                ident_hash=ident_hash,
                prior=prior,
                pred=pred_pack,
                extra={"duplicate_architecture": dup, "phase_a": phase_a},
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        fwd_bodies.append(body)

    after = _prior_hashes()
    if after != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        return _stop(
            "STOP. Prior closed-family artifacts mutated.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup, "phase_a": phase_a},
        )
    if (
        int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0)
        + int(leak.get("PRE_FILL_EXIT_N") or 0)
        + int(leak.get("POST_BE_U_TRIGGER_N") or 0)
        + int(leak.get("PREDICATE_DRIFT_N") or 0)
        + int(leak.get("TF1_MISSING_N") or 0)
        + int(leak.get("VOLUME_ARRAY_MISSING_N") or 0)
        + int(leak.get("STATIC_AND_SAME_BAR_EXIT_N") or 0)
    ):
        return _stop(
            "STOP. Causal integrity leak != 0.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup, "phase_a": phase_a},
        )

    dev_m = _merge(dev_bodies)
    fwd_m = _merge(fwd_bodies)
    ident_dev = evaluate_cohort(
        harvest_rows=list(dev_m.get("rows") or []),
        cohort="DEVELOPMENT",
        days=list(ELIGIBLE_DAYS),
        ctrl_arm=dict(dev_m.get("control") or {}),
        treat_arm=dict(dev_m.get("treatment") or {}),
        residual=residual_index(residual_dev),
        causes=list(dev_m.get("causes") or []),
        leftover_ok=bool(dev_m.get("leftover_ok")),
        control_sot_ok=bool(dev_m.get("control_sot_ok")),
        treatment_sot_ok=bool(dev_m.get("treatment_sot_ok")),
    )
    if not bool(ident_dev.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. DEV CONTROL identity mismatch: {ident_dev.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup, "phase_a": phase_a},
        )
    ident_fwd = evaluate_cohort(
        harvest_rows=list(fwd_m.get("rows") or []),
        cohort="FORWARD_BURNED",
        days=list(LOCKED_SERIES_DAYS),
        ctrl_arm=dict(fwd_m.get("control") or {}),
        treat_arm=dict(fwd_m.get("treatment") or {}),
        residual=residual_index(residual_fwd),
        causes=list(fwd_m.get("causes") or []),
        leftover_ok=bool(fwd_m.get("leftover_ok")),
        control_sot_ok=bool(fwd_m.get("control_sot_ok")),
        treatment_sot_ok=bool(fwd_m.get("treatment_sot_ok")),
    )
    if not bool(ident_fwd.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. BURNED CONTROL identity mismatch: {ident_fwd.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            ident_hash=ident_hash,
            prior=prior,
            pred=pred_pack,
            extra={"duplicate_architecture": dup, "phase_a": phase_a},
        )

    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide(
        ident_dev,
        ident_fwd,
        leak_ok=leak_ok,
        harvested_ok=harvested_ok,
        pred_ok=True,
        identity_232_ok=True,
    )
    post = snapshot(phase="POST")
    report = _base_report(
        pre=pre,
        leak=leak,
        sha=sha,
        source_sha=source_sha,
        ident_hash=ident_hash,
        prior=prior,
        pred=pred_pack,
        decision=decision,
        extra={
            "duplicate_architecture": dup,
            "unconstrained_audit": unc_audit,
            "identity_232": unc_audit.get("identity"),
            "phase_a": phase_a,
            "CANDIDATE_SPEC_FROZEN_AFTER_PHASE_A": True,
            "residual_prior": {"ANALYSIS_ID": RESIDUAL_PRIOR_ANALYSIS, "VERDICT": RESIDUAL_PRIOR_VERDICT, "family_close": True},
            "vwap_prior": {"ANALYSIS_ID": VWAP_PRIOR_ANALYSIS, "VERDICT": VWAP_PRIOR_VERDICT, "CASE": VWAP_PRIOR_CASE, "family_close": True},
            "development": ident_dev,
            "forward": ident_fwd,
            "postflight": post,
            "reporting_semantics": reporting_semantics(pre, post),
            "capture_reporting_state": capture_reporting_state(pre, post),
        },
    )
    _write(report)
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
        f"FROZEN={decision.get('CANDIDATE_FROZEN')} EXHAUSTED={decision.get('CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED')} "
        f"DEV_TOTAL={ident_dev.get('TOTAL_CAUSAL_DELTA')} out={BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if harvested_ok and leak_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
