"""Offline Branch U residual actionability gate + at most one Full Causal EXIT. Research only."""
from __future__ import annotations

import hashlib
import json
import os
import sys
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
from research.simple_tech_redesign.branch_u_residual_actionability_v1_analyze import (
    decide_phase_a,
    decide_phase_b,
    evaluate_cohort,
)
from research.simple_tech_redesign.branch_u_residual_actionability_v1_harvest import (
    harvest_day,
    load_phase_a_fills,
    phase_a_table,
    recover_remaining_predicates,
    select_qualifier,
    semantic_duplicate_audit,
)
from research.simple_tech_redesign.branch_u_residual_actionability_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.branch_u_residual_actionability_v1_spec import (
    ANALYSIS_ID,
    AND_OR_SEARCH,
    AUTO_HOP_NEXT_PRIMITIVE,
    COMBINATION_SEARCH,
    CONTROL_EXIT,
    EXCLUDED_NOT_CANDIDATE,
    EXECUTION,
    FIRST_PROSPECTIVE_DAY,
    FORBIDDEN_INPUT_DAYS,
    K_SEARCH,
    MAX_RESEARCH_DATE,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    PERSISTENCE_SEARCH,
    PNL_SELECTION,
    PROSPECTIVE_HARVEST_SUSPENDED,
    REMAINING_PRIMITIVES,
    SIGNAL,
    THRESHOLD_SEARCH,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
    TRUE_OOS,
    U_EMA_PRIOR_CANDIDATE,
    U_EMA_PRIOR_CASE,
    U_EMA_PRIOR_VERDICT,
    candidate_id_for,
    candidate_identity_hash,
    canonical_actionability_spec,
    exit_reason_for,
    freeze_selected_spec,
    source_sha256_actionability,
    spec_sha256_actionability,
    spec_sha256_selected,
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
    "FILL_PACK_INCOMPLETE_N",
    "THRESHOLD_SEARCH_N",
    "PERSISTENCE_SEARCH_N",
    "K_SEARCH_N",
    "AND_OR_SEARCH_N",
    "COMBINATION_SEARCH_N",
    "PNL_SELECTION_N",
    "NEW_ENTRY_FILTER_N",
    "NEW_EXIT_RULE_N",
    "AUTO_HOP_N",
)
PRIOR_OUTS = (
    LIFECYCLE_OUT,
    BRANCH_U_OUT,
    BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT,
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
    phase_a = dict(report.get("phase_a") or {})
    sel = dict(report.get("selection") or {})
    inv = dict(report.get("inventory") or {})
    dup = dict(report.get("semantic_duplicate") or {})
    incr = dict(dev.get("incremental") or {})
    incr_f = dict(fwd.get("incremental") or {})
    conc_d = dict(dev.get("concentration") or {})
    conc_f = dict(fwd.get("concentration") or {})
    gates = dict(dec.get("gates") or {})
    u_ema = dict(report.get("u_ema_prior") or {})
    return {
        "1": (
            "objective alignment = keep fill coverage; tolerate some ENTRY quality dilution; "
            "process failure with Technical EXIT; improve Full Causal Portfolio PnL/PF/MaxDD; then Sizing. "
            "Not ENTRY precision. Not a new RCA. At most one remaining Branch U primitive."
        ),
        "2": (
            f"prior U_EMA = {u_ema.get('VERDICT') or U_EMA_PRIOR_VERDICT} CASE {u_ema.get('CASE') or U_EMA_PRIOR_CASE} "
            f"id={u_ema.get('CANDIDATE_ID') or U_EMA_PRIOR_CANDIDATE} CANDIDATE_FROZEN=false family CLOSE. "
            "DEV U_EARLY direct +111400 / P_EARLY -27100 / GOOD+DIP 0 / downstream +32500 / total +116800. "
            "Burned U_EARLY +400 / P_EARLY -13500 / GOOD+DIP 0 / downstream -104200 / total -117300. "
            "U failure exists, but pre-BE false EXIT of future-BE EARLY plus slot-release downstream broke Portfolio."
        ),
        "3": (
            "prior Lifecycle U gate was insufficient because EARLY_FAILURE mixed U_EARLY_NEVER_BE with P_EARLY_AFTER_BE. "
            "New candidate-actionability gate separates those residual classes. Future labels are diagnostic only."
        ),
        "4": f"exact remaining inventory={list(inv.get('remaining') or REMAINING_PRIMITIVES)} excluded={list(inv.get('excluded') or EXCLUDED_NOT_CANDIDATE)}",
        "5": (
            f"semantic duplicate audit ok={dup.get('ok')} duplicates={dup.get('duplicate_primitives')} "
            f"pullback_vs_floor={dup.get('pullback_vs_floor_duplicate')} rci_vs_p3={dup.get('rci_vs_p3_duplicate')}"
        ),
        "6": (
            f"Phase A identity TOTAL={phase_a.get('TOTAL_RESEARCH_FILL_N')} CORE={phase_a.get('CORE_FILL_N')} "
            f"ADDED={phase_a.get('ADDED_FILL_N')} hash={phase_a.get('RESEARCH_FILL_SET_HASH')} ok={phase_a.get('identity_ok')} "
            f"U_EARLY_N={phase_a.get('U_EARLY_N')} P_EARLY_N={phase_a.get('P_EARLY_N')} GOOD={phase_a.get('GOOD_N')} DIP={phase_a.get('DIP_N')}"
        ),
        "7": f"Phase A table={json.dumps(phase_a.get('table') or [], ensure_ascii=True, default=str)[:4000]}",
        "8": f"qualified primitive N={sel.get('n')} qualified={sel.get('qualified')}",
        "9": f"selected primitive={sel.get('selected')} CANDIDATE_ID={sel.get('CANDIDATE_ID') or dec.get('CANDIDATE_ID')}",
        "10": f"selection rule={sel.get('rule')} parity keys=max U_EARLY_HIT_N, max hit_day_n, min top U-hit symbol share",
        "11": f"PnL-used-for-selection={bool(sel.get('PNL_USED_FOR_SELECTION')) or bool(PNL_SELECTION)}",
        "12": f"candidate spec frozen before Phase B? {report.get('CANDIDATE_SPEC_FROZEN_BEFORE_PHASE_B')} sha={report.get('spec_sha256')}",
        "13": f"Control DEV occupancy fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}",
        "14": f"Control Burned occupancy fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} PnL={ident_f.get('pnl')} ok={ident_f.get('ok')}",
        "15": f"U_EARLY direct DEV={_n(dev.get('U_EARLY_NEVER_BE_DIRECT_DELTA'))} Burned={_n(fwd.get('U_EARLY_NEVER_BE_DIRECT_DELTA'))}",
        "16": f"P_EARLY direct DEV={_n(dev.get('P_EARLY_DIRECT_DELTA'))} Burned={_n(fwd.get('P_EARLY_DIRECT_DELTA'))}",
        "17": f"GOOD direct DEV={_n(dev.get('GOOD_DIRECT_DELTA'))} DIP direct DEV={_n(dev.get('DIP_DIRECT_DELTA'))}",
        "18": f"direct delta A={_n(dev.get('DIRECT_EXIT_DELTA'))} downstream B={_n(dev.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))} displaced C={_n(dev.get('DISPLACED_TRADE_DELTA'))} total={_n(dev.get('TOTAL_CAUSAL_DELTA'))} decomp_ok={dev.get('decomp_ok')}",
        "19": (
            f"incremental DEV N/PnL/PF/win/median/CORE/ADDED/GOOD/EARLY/DIP/PTF/OTHER="
            f"{incr.get('incremental_fill_n')}/{_n(incr.get('incremental_pnl'))}/{incr.get('PF')}/"
            f"{_n(incr.get('win_rate'))}/{_n(incr.get('median_pnl'))}/"
            f"{incr.get('CORE_n')}/{incr.get('ADDED_n')}/"
            f"{incr.get('GOOD')}/{incr.get('EARLY')}/{incr.get('DIP')}/{incr.get('PTF')}/{incr.get('OTHER')} "
            f"EARLY_n={incr.get('incremental_EARLY_n')} EARLY_pnl={_n(incr.get('incremental_EARLY_pnl'))} "
            f"Burned EARLY_n={incr_f.get('incremental_EARLY_n')} EARLY_pnl={_n(incr_f.get('incremental_EARLY_pnl'))}"
        ),
        "20": (
            f"DEV PnL Control/Treatment={_n((dev.get('control') or {}).get('total_pnl'))}/{_n((dev.get('treatment') or {}).get('total_pnl'))} "
            f"PF={(dev.get('control') or {}).get('PF')}/{(dev.get('treatment') or {}).get('PF')} "
            f"MaxDD={_n((dev.get('control') or {}).get('max_drawdown'))}/{_n((dev.get('treatment') or {}).get('max_drawdown'))}"
        ),
        "21": (
            f"Burned PnL Control/Treatment={_n((fwd.get('control') or {}).get('total_pnl'))}/{_n((fwd.get('treatment') or {}).get('total_pnl'))} "
            f"PF={(fwd.get('control') or {}).get('PF')}/{(fwd.get('treatment') or {}).get('PF')} "
            f"MaxDD={_n((fwd.get('control') or {}).get('max_drawdown'))}/{_n((fwd.get('treatment') or {}).get('max_drawdown'))}"
        ),
        "22": f"day concentration DEV={conc_d.get('day')} warn={conc_d.get('single_day_contribution_gt_50pct')} Burned={conc_f.get('day')} warn={conc_f.get('single_day_contribution_gt_50pct')}",
        "23": f"symbol concentration DEV={conc_d.get('symbol')} warn={conc_d.get('single_symbol_contribution_gt_50pct')} 285A_excluded={conc_d.get('symbol_285A_excluded')} Burned={conc_f.get('symbol')}",
        "24": f"all gates={gates} all_dev={dec.get('all_development_gates')} burned_ok={dec.get('burned_direction_ok')}",
        "25": f"verdict={dec.get('VERDICT')} CASE {dec.get('CASE')} PHASE {dec.get('PHASE')}",
        "26": f"candidate frozen? {dec.get('CANDIDATE_FROZEN')} id={dec.get('CANDIDATE_ID')}",
        "27": "ENTRY changed? false",
        "28": "execution changed? false E4_THEN_ASK_CROSS_W5 held",
        "29": "EXIT Runtime changed? false research-only",
        "30": "CAP changed? false",
        "31": "sizing changed? false",
        "32": "future data used? false",
        "33": f"MAX_RESEARCH_DATE={MAX_RESEARCH_DATE}",
        "34": f"prospective harvest suspended? {PROSPECTIVE_HARVEST_SUSPENDED}",
        "35": "TRUE_OOS=false",
        "36": "CERTIFIED=false",
        "37": f"submit/cancel/live={report.get('submit_cancel_live')}",
        "38": f"next={dec.get('NEXT')}",
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, prior: dict[str, str], pred: dict[str, Any], extra: dict[str, Any] | None = None) -> int:
    decision = {
        "PHASE": "A",
        "CASE": "E",
        "VERDICT": "SIMPLE_TECH_BRANCH_U_RESIDUAL_INTEGRITY_FAILED",
        "NEXT": msg,
        "CANDIDATE_FROZEN": False,
        "FAMILY_CLOSED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "SELECTED_PRIMITIVE": None,
        "run_phase_b": False,
        "gates": {"1_integrity": False},
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision["VERDICT"],
            "CASE": "E",
            "CANDIDATE_FROZEN": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "FUTURE_DATA_USED": False,
        },
        "decision": decision,
        "predicate": _public_pred(pred),
        "inventory": {"remaining": list(REMAINING_PRIMITIVES), "excluded": list(EXCLUDED_NOT_CANDIDATE)},
        "development": {},
        "forward": {},
        "leak": leak,
        "preflight": pre,
        "prior_hashes": prior,
        "CANDIDATE_SPEC_FROZEN_BEFORE_PHASE_B": False,
        "answers": {},
        "_markdown": "",
    }
    if extra:
        report.update(extra)
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256_actionability()
    source_sha = source_sha256_actionability()
    ident_hash = candidate_identity_hash(spec_sha=sha, source_sha=source_sha, selected_primitive=None)
    pre = snapshot(phase="PRE")
    prior = _prior_hashes()
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["NEW_ENTRY_FILTER_N"] = int(bool(NEW_ENTRY_FILTER))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["PERSISTENCE_SEARCH_N"] = int(bool(PERSISTENCE_SEARCH))
    leak["K_SEARCH_N"] = int(bool(K_SEARCH))
    leak["AND_OR_SEARCH_N"] = int(bool(AND_OR_SEARCH))
    leak["COMBINATION_SEARCH_N"] = int(bool(COMBINATION_SEARCH))
    leak["PNL_SELECTION_N"] = int(bool(PNL_SELECTION))
    leak["AUTO_HOP_N"] = int(bool(AUTO_HOP_NEXT_PRIMITIVE))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["FUTURE_DATA_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT analysis={ANALYSIS_ID} spec={sha[:12]} today={TODAY} signal={SIGNAL} exec={EXECUTION}", flush=True)

    pred_pack = recover_remaining_predicates()
    if not pred_pack.get("ok"):
        leak["PREDICATE_DRIFT_N"] = 1
        return _stop(
            f"STOP. Exact remaining primitive restore failed: {pred_pack.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )
    dup_pack = semantic_duplicate_audit(pred_pack)
    if not dup_pack.get("ok"):
        leak["PREDICATE_DRIFT_N"] = 1
        return _stop(
            f"STOP. Semantic duplicate source restore failed: {dup_pack.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
            extra={"semantic_duplicate": dup_pack},
        )

    u_ema_req = dict(_load(BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT / "report.json").get("required") or {})
    if str(u_ema_req.get("VERDICT") or "") != U_EMA_PRIOR_VERDICT or bool(u_ema_req.get("CANDIDATE_FROZEN")):
        return _stop(
            "STOP. Prior U_EMA result is not CASE C CLOSED / unfrozen.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
            extra={"semantic_duplicate": dup_pack, "u_ema_prior": u_ema_req},
        )

    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or PERSISTENCE_SEARCH or bool(TRUE_OOS) or NEW_ENTRY_FILTER:
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if AUTO_HOP_NEXT_PRIMITIVE or COMBINATION_SEARCH or AND_OR_SEARCH or K_SEARCH or PNL_SELECTION or not PROSPECTIVE_HARVEST_SUSPENDED:
        return _stop("STOP. Auto-hop / combination / PnL selection / prospective flag.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Day list includes 20260903+.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(LIFECYCLE_OUT / "report.json"), str(BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)

    phase_body = load_phase_a_fills()
    if not phase_body.get("ok"):
        return _stop(
            f"STOP. Phase A research-fill identity mismatch: {phase_body.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
            extra={"semantic_duplicate": dup_pack, "phase_a_identity": phase_body},
        )
    dup_pids = set(dup_pack.get("duplicate_primitives") or [])
    table_pack = phase_a_table(list(phase_body.get("fills") or []), duplicate_pids=dup_pids, pred_ok=True)
    selection = select_qualifier(list(table_pack.get("table") or []))
    phase_a_out = {
        **{k: v for k, v in phase_body.items() if k not in {"rows", "fills"}},
        **{k: v for k, v in table_pack.items() if k != "table"},
        "table": table_pack.get("table"),
        "identity_ok": True,
        "identity": {
            "TOTAL_RESEARCH_FILL_N": phase_body.get("TOTAL_RESEARCH_FILL_N"),
            "CORE_FILL_N": phase_body.get("CORE_FILL_N"),
            "ADDED_FILL_N": phase_body.get("ADDED_FILL_N"),
            "RESEARCH_FILL_SET_HASH": phase_body.get("RESEARCH_FILL_SET_HASH"),
            "ok": True,
        },
    }
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision_a = decide_phase_a(
        identity_ok=True,
        pred_ok=True,
        dup_ok=bool(dup_pack.get("ok")),
        leak_ok=leak_ok,
        selection=selection,
    )
    after_a = _prior_hashes()
    if after_a != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        return _stop("STOP. Prior closed-family artifacts mutated.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)

    base_report = {
        "analysis_id": ANALYSIS_ID,
        "candidate_id": selection.get("CANDIDATE_ID"),
        "selected_primitive": selection.get("selected"),
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "candidate_identity_hash": ident_hash,
        "canonical_spec": canonical_actionability_spec(),
        "predicate": _public_pred(pred_pack),
        "semantic_duplicate": dup_pack,
        "inventory": {"remaining": list(REMAINING_PRIMITIVES), "excluded": list(EXCLUDED_NOT_CANDIDATE)},
        "phase_a": phase_a_out,
        "selection": selection,
        "u_ema_prior": {
            "VERDICT": U_EMA_PRIOR_VERDICT,
            "CASE": U_EMA_PRIOR_CASE,
            "CANDIDATE_ID": U_EMA_PRIOR_CANDIDATE,
            "CANDIDATE_FROZEN": False,
            "family_close": True,
        },
        "CANDIDATE_SPEC_FROZEN_BEFORE_PHASE_B": False,
        "development": {},
        "forward": {},
        "leak": leak,
        "preflight": pre,
        "prior_hashes": prior,
        "submit_cancel_live": {"submit": int(SUBMIT_N), "cancel": int(CANCEL_N), "live_order": int(LIVE_ORDER_N)},
    }

    if not decision_a.get("run_phase_b"):
        post = snapshot(phase="POST")
        report = {
            **base_report,
            "required": {
                "ANALYSIS_ID": ANALYSIS_ID,
                "CANDIDATE_ID": None,
                "VERDICT": decision_a.get("VERDICT"),
                "CASE": decision_a.get("CASE"),
                "PHASE": "A",
                "CANDIDATE_FROZEN": False,
                "FAMILY_CLOSED": decision_a.get("FAMILY_CLOSED"),
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
                "PNL_USED_FOR_SELECTION": False,
                "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
                "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
            },
            "decision": decision_a,
            "postflight": post,
            "reporting_semantics": reporting_semantics(pre, post),
            "capture_reporting_state": capture_reporting_state(pre, post),
            "answers": {},
            "_markdown": "",
        }
        _publish(report)
        print(
            f"FINAL ANALYSIS_ID={ANALYSIS_ID} PHASE=A CASE={decision_a.get('CASE')} VERDICT={decision_a.get('VERDICT')} "
            f"qualified_n={selection.get('n')} out={BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT}",
            flush=True,
        )
        print("STOP.", flush=True)
        return 0 if leak_ok else 2

    primitive = str(selection.get("selected") or "")
    frozen_spec = freeze_selected_spec(primitive)
    sha_b = spec_sha256_selected(primitive)
    ident_hash_b = candidate_identity_hash(spec_sha=sha_b, source_sha=source_sha, selected_primitive=primitive)
    exit_reason = exit_reason_for(primitive)
    print(
        f"PHASE B freeze primitive={primitive} candidate={candidate_id_for(primitive)} spec={sha_b[:12]}",
        flush=True,
    )

    try:
        residual_dev, residual_fwd, _residual_body = load_residual_rows()
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Residual SoT: {exc}.", pre=pre, leak=leak, sha=sha_b, source_sha=source_sha, prior=prior, pred=pred_pack)

    harvested_ok = True
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden DEV day: {day} {blocker}.", pre=pre, leak=leak, sha=sha_b, source_sha=source_sha, prior=prior, pred=pred_pack)
        body = harvest_day(day, cohort="DEVELOPMENT", spec_sha=sha_b, primitive=primitive, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail DEVELOPMENT {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha_b,
                source_sha=source_sha,
                prior=prior,
                pred=pred_pack,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        dev_bodies.append(body)
    for day in list(LOCKED_SERIES_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden BURNED day: {day} {blocker}.", pre=pre, leak=leak, sha=sha_b, source_sha=source_sha, prior=prior, pred=pred_pack)
        body = harvest_day(day, cohort="FORWARD_BURNED", spec_sha=sha_b, primitive=primitive, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail BURNED_STRESS {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha_b,
                source_sha=source_sha,
                prior=prior,
                pred=pred_pack,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        fwd_bodies.append(body)

    after = _prior_hashes()
    if after != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        return _stop("STOP. Prior closed-family artifacts mutated.", pre=pre, leak=leak, sha=sha_b, source_sha=source_sha, prior=prior, pred=pred_pack)
    if (
        int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0)
        + int(leak.get("PRE_FILL_EXIT_N") or 0)
        + int(leak.get("POST_BE_U_TRIGGER_N") or 0)
        + int(leak.get("PREDICATE_DRIFT_N") or 0)
        + int(leak.get("TF1_MISSING_N") or 0)
    ):
        return _stop("STOP. Causal integrity leak != 0.", pre=pre, leak=leak, sha=sha_b, source_sha=source_sha, prior=prior, pred=pred_pack)

    dev_m = _merge(dev_bodies)
    fwd_m = _merge(fwd_bodies)
    ident_dev = evaluate_cohort(
        exit_reason=exit_reason,
        cohort="DEVELOPMENT",
        days=list(ELIGIBLE_DAYS),
        harvest_rows=list(dev_m.get("rows") or []),
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
            sha=sha_b,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )
    ident_fwd = evaluate_cohort(
        exit_reason=exit_reason,
        cohort="FORWARD_BURNED",
        days=list(LOCKED_SERIES_DAYS),
        harvest_rows=list(fwd_m.get("rows") or []),
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
            sha=sha_b,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )

    leak_ok_b = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide_phase_b(ident_dev, ident_fwd, leak_ok=leak_ok_b, harvested_ok=harvested_ok, pred_ok=True, primitive=primitive)
    post = snapshot(phase="POST")
    report = {
        **base_report,
        "candidate_id": candidate_id_for(primitive),
        "selected_primitive": primitive,
        "spec_sha256": sha_b,
        "candidate_identity_hash": ident_hash_b,
        "canonical_spec": frozen_spec,
        "CANDIDATE_SPEC_FROZEN_BEFORE_PHASE_B": True,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": candidate_id_for(primitive),
            "SELECTED_PRIMITIVE": primitive,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "PHASE": "B",
            "CANDIDATE_FROZEN": decision.get("CANDIDATE_FROZEN"),
            "FAMILY_CLOSED": decision.get("FAMILY_CLOSED"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_SPEC_SHA256": sha_b,
            "SOURCE_SHA256": source_sha,
            "CANDIDATE_IDENTITY_HASH": ident_hash_b,
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
            "PNL_USED_FOR_SELECTION": False,
            "CANDIDATE_SPEC_FROZEN_BEFORE_PHASE_B": True,
            "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
            "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
        },
        "decision": decision,
        "development": ident_dev,
        "forward": ident_fwd,
        "leak": leak,
        "postflight": post,
        "reporting_semantics": reporting_semantics(pre, post),
        "capture_reporting_state": capture_reporting_state(pre, post),
        "answers": {},
        "_markdown": "",
    }
    _publish(report)
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} PHASE=B CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
        f"FROZEN={decision.get('CANDIDATE_FROZEN')} primitive={primitive} DEV_TOTAL={ident_dev.get('TOTAL_CAUSAL_DELTA')} "
        f"out={BRANCH_U_RESIDUAL_ACTIONABILITY_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if harvested_ok and leak_ok_b else 2


if __name__ == "__main__":
    raise SystemExit(main())
