"""Offline POST_BE_BB_STRUCTURE_LOSS_3M_V1 full causal portfolio evaluation."""
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
from research.simple_tech_redesign.exit_lifecycle_spec import EXIT_POLICY_CREATED as LIFECYCLE_EXIT_POLICY_CREATED
from research.simple_tech_redesign.isolation import (
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    LIFECYCLE_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.branch_p_bb_structure_exit_v1_analyze import decide, evaluate_cohort
from research.simple_tech_redesign.branch_p_bb_structure_exit_v1_harvest import harvest_day, recover_lifecycle_bb_predicate
from research.simple_tech_redesign.branch_p_bb_structure_exit_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.branch_p_bb_structure_exit_v1_spec import (
    ANALYSIS_ID,
    AND_OR_CLOSED_FAMILIES,
    AUTO_HOP_P_RCI,
    AUTO_HOP_P_TREND,
    AUTO_HOP_P_VWAP,
    CANDIDATE_ID,
    CLOSED_DO_NOT_REENTER,
    CONTROL_EXIT,
    EXECUTION,
    FIRST_PROSPECTIVE_DAY,
    FORBIDDEN_INPUT_DAYS,
    LIFECYCLE_BB_DAY_AGREE,
    LIFECYCLE_BB_DAY_DISAGREE,
    LIFECYCLE_BB_FAIL_HIT_RATE,
    LIFECYCLE_BB_KEEP_HIT_RATE,
    LIFECYCLE_P_SUPPORTED,
    LIFECYCLE_PRIMITIVE,
    LIFECYCLE_RCI_FAIL_HIT_RATE,
    LIFECYCLE_RCI_KEEP_HIT_RATE,
    LIFECYCLE_TREND_FAIL_HIT_RATE,
    LIFECYCLE_TREND_KEEP_HIT_RATE,
    MAX_RESEARCH_DATE,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    PROSPECTIVE_HARVEST_SUSPENDED,
    SIGNAL,
    STATE_MACHINE,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    canonical_candidate_spec,
    candidate_identity_hash,
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
    "PRE_BE_TRIGGER_N",
    "PREDICATE_DRIFT_N",
    "TF1_MISSING_N",
    "TF3_MISSING_N",
    "FILL_PACK_INCOMPLETE_N",
    "THRESHOLD_SEARCH_N",
    "NEW_ENTRY_FILTER_N",
    "NEW_EXIT_RULE_N",
    "AUTO_HOP_N",
)
PRIOR_OUTS = (
    LIFECYCLE_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
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
    conc_d = dict(dev.get("concentration") or {})
    incr = dict(dev.get("incremental") or {})
    gates = dict(dec.get("gates") or {})
    return {
        "1": (
            "objective alignment = keep fill coverage; tolerate some ENTRY quality dilution; "
            "process failures with Technical EXIT; improve Full Causal Portfolio PnL/PF/MaxDD; then Sizing."
        ),
        "2": (
            f"why BB was selected = Lifecycle {LIFECYCLE_PRIMITIVE} SUPPORTED; VWAP already actual-causal tested and CLOSED; "
            f"BB fail_hit_rate={LIFECYCLE_BB_FAIL_HIT_RATE:.4f} keep_hit_rate={LIFECYCLE_BB_KEEP_HIT_RATE:.4f} "
            f"day {LIFECYCLE_BB_DAY_AGREE} agree / {LIFECYCLE_BB_DAY_DISAGREE} disagree vs "
            f"TREND fail={LIFECYCLE_TREND_FAIL_HIT_RATE:.4f} keep={LIFECYCLE_TREND_KEEP_HIT_RATE:.4f} and "
            f"RCI fail={LIFECYCLE_RCI_FAIL_HIT_RATE:.4f} keep={LIFECYCLE_RCI_KEEP_HIT_RATE:.4f}. Pre-existing supported mechanism, not PnL reselection."
        ),
        "3": (
            f"remaining P primitive non-hop audit = AUTO_HOP_P_TREND/RCI/VWAP={AUTO_HOP_P_TREND}/{AUTO_HOP_P_RCI}/{AUTO_HOP_P_VWAP}; "
            f"AND_OR_CLOSED={AND_OR_CLOSED_FAMILIES}; closed={list(CLOSED_DO_NOT_REENTER)}"
        ),
        "4": (
            f"exact lifecycle predicate source FILE={pred.get('SOURCE_FILE')} FN={pred.get('SOURCE_FUNCTION')} "
            f"LINE={pred.get('SOURCE_LINE_OR_SYMBOL')} SHA={pred.get('SOURCE_SHA')}"
        ),
        "5": (
            f"exact BB definition TEXT={pred.get('EXACT_PREDICATE_TEXT')} DEF={pred.get('BB_DEFINITION')} "
            f"PERIOD={pred.get('BB_PERIOD')} STD={pred.get('BB_STD')}"
        ),
        "6": (
            f"exact 3m causal semantics TF3={pred.get('TF3_BAR_CONSTRUCTION')} "
            f"BAR={pred.get('BAR_FINALIZATION_SEMANTICS')} EVENT={pred.get('EVENT_TIMESTAMP_SEMANTICS')}"
        ),
        "7": f"Control DEV fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}",
        "8": f"Control Burned fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} PnL={ident_f.get('pnl')} ok={ident_f.get('ok')}",
        "9": (
            f"BE branch parity residual mismatch DEV={ident_d.get('be_flag_mismatch_n')} FWD={ident_f.get('be_flag_mismatch_n')} "
            f"predicate_drift={ident_d.get('predicate_drift_n')}/{ident_f.get('predicate_drift_n')} "
            f"tf3_fail={ident_d.get('tf3_fail_n')}/{ident_f.get('tf3_fail_n')}"
        ),
        "10": f"P-BB trigger N DEV={dev.get('technical_exit_n')} common={dev.get('common_trigger_n')} Burned={fwd.get('technical_exit_n')} common={fwd.get('common_trigger_n')}",
        "11": f"PTF direct delta={_n(dev.get('PTF_DIRECT_DELTA'))}",
        "12": f"P_EARLY direct delta={_n(dev.get('P_EARLY_DIRECT_DELTA'))}",
        "13": f"proven failure direct delta={_n(dev.get('PROVEN_FAILURE_DIRECT_DELTA'))}",
        "14": f"GOOD direct/median={_n(dev.get('GOOD_DIRECT_DELTA'))}/{_n(dev.get('GOOD_MEDIAN_DELTA'))}",
        "15": f"DIP direct/median={_n(dev.get('DIP_DIRECT_DELTA'))}/{_n(dev.get('DIP_MEDIAN_DELTA'))}",
        "16": f"winner combined={_n(dev.get('PROTECTED_GOOD_DIP_DIRECT_DELTA'))}",
        "17": f"CORE/ADDED direct={_n(dev.get('CORE_DIRECT_DELTA'))}/{_n(dev.get('ADDED_DIRECT_DELTA'))}",
        "18": f"direct EXIT delta A={_n(dev.get('DIRECT_EXIT_DELTA'))}",
        "19": (
            f"incremental fill N/PnL/PF/win_rate/median="
            f"{incr.get('incremental_fill_n')}/{_n(incr.get('incremental_pnl'))}/{incr.get('PF')}/"
            f"{_n(incr.get('win_rate'))}/{_n(incr.get('median_pnl'))}"
        ),
        "20": f"downstream delta B={_n(dev.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))}",
        "21": f"displaced delta C={_n(dev.get('DISPLACED_TRADE_DELTA'))}",
        "22": f"total causal delta={_n(dev.get('TOTAL_CAUSAL_DELTA'))} decomp_ok={dev.get('decomp_ok')}",
        "23": f"DEV Control/Treatment PnL={_n((dev.get('control') or {}).get('total_pnl'))}/{_n((dev.get('treatment') or {}).get('total_pnl'))}",
        "24": f"DEV PF Control/Treatment={(dev.get('control') or {}).get('PF')}/{(dev.get('treatment') or {}).get('PF')}",
        "25": f"DEV MaxDD Control/Treatment={_n((dev.get('control') or {}).get('max_drawdown'))}/{_n((dev.get('treatment') or {}).get('max_drawdown'))}",
        "26": f"fill count Control/Treatment={(dev.get('control') or {}).get('fill_n')}/{(dev.get('treatment') or {}).get('fill_n')} change={dev.get('fill_n_change')}",
        "27": f"day concentration DEV={conc_d.get('day')} warn={conc_d.get('single_day_contribution_gt_50pct')}",
        "28": f"symbol concentration DEV={conc_d.get('symbol')} warn={conc_d.get('single_symbol_contribution_gt_50pct')} 285A_excluded={conc_d.get('symbol_285A_excluded')}",
        "29": f"Burned PTF={_n(fwd.get('PTF_DIRECT_DELTA'))}",
        "30": f"Burned P_EARLY={_n(fwd.get('P_EARLY_DIRECT_DELTA'))}",
        "31": f"Burned proven failure={_n(fwd.get('PROVEN_FAILURE_DIRECT_DELTA'))}",
        "32": f"Burned winner={_n(fwd.get('PROTECTED_GOOD_DIP_DIRECT_DELTA'))}",
        "33": f"Burned total={_n(fwd.get('TOTAL_CAUSAL_DELTA'))}",
        "34": f"all gates={gates} all_dev={dec.get('all_development_gates')} burned_ok={dec.get('burned_direction_ok')}",
        "35": f"verdict={dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "36": f"candidate frozen? {dec.get('CANDIDATE_FROZEN')} id={CANDIDATE_ID}",
        "37": f"BB family closed? {dec.get('FAMILY_CLOSED')}",
        "38": "ENTRY changed? false",
        "39": "Runtime EXIT changed? false",
        "40": "CAP changed? false",
        "41": "sizing changed? false",
        "42": "future data used? false",
        "43": f"MAX_RESEARCH_DATE={MAX_RESEARCH_DATE}",
        "44": f"prospective harvest suspended? {PROSPECTIVE_HARVEST_SUSPENDED}",
        "45": "TRUE_OOS=false",
        "46": "CERTIFIED=false",
        "47": f"submit/cancel/live={report.get('submit_cancel_live')}",
        "48": f"next={dec.get('NEXT')}",
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, prior: dict[str, str], pred: dict[str, Any]) -> int:
    decision = {
        "CASE": "E",
        "VERDICT": "SIMPLE_TECH_BRANCH_P_BB_INTEGRITY_FAILED",
        "NEXT": msg,
        "CANDIDATE_FROZEN": False,
        "FAMILY_CLOSED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "gates": {"1_integrity": False},
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
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
        "development": {},
        "forward": {},
        "leak": leak,
        "preflight": pre,
        "prior_hashes": prior,
        "answers": {},
        "_markdown": "",
    }
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
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
    leak["AUTO_HOP_N"] = int(bool(AUTO_HOP_P_TREND or AUTO_HOP_P_RCI or AUTO_HOP_P_VWAP))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["FUTURE_DATA_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT candidate={CANDIDATE_ID} spec={sha[:12]} today={TODAY} signal={SIGNAL} exec={EXECUTION}", flush=True)

    pred_pack = recover_lifecycle_bb_predicate()
    if not pred_pack.get("ok"):
        leak["PREDICATE_DRIFT_N"] = 1
        return _stop(
            f"STOP. Exact frozen predicate restore failed: {pred_pack.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )

    body_life = _load(LIFECYCLE_OUT / "report.json")
    p_supported = list((body_life.get("required") or {}).get("P_SUPPORTED") or [])
    if LIFECYCLE_PRIMITIVE not in p_supported:
        return _stop(
            "STOP. Lifecycle RCA does not list P_BB_STRUCTURE_LOSS_3M as supported.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )
    vwap_req = dict(_load(BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT / "report.json").get("required") or {})
    if str(vwap_req.get("VERDICT") or "") != "SIMPLE_TECH_BRANCH_P_VWAP_NOT_SUPPORTED" or bool(vwap_req.get("CANDIDATE_FROZEN")):
        return _stop(
            "STOP. VWAP Branch P prior result is not CASE D CLOSED / unfrozen.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )

    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or bool(TRUE_OOS) or NEW_ENTRY_FILTER:
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if AUTO_HOP_P_TREND or AUTO_HOP_P_RCI or AUTO_HOP_P_VWAP or AND_OR_CLOSED_FAMILIES or not PROSPECTIVE_HARVEST_SUSPENDED:
        return _stop("STOP. Auto-hop or AND/OR closed family or prospective flag.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Day list includes 20260903+.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(LIFECYCLE_OUT / "report.json"), str(BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    try:
        residual_dev, residual_fwd, _residual_body = load_residual_rows()
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Residual SoT: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)

    harvested_ok = True
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden DEV day: {day} {blocker}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
        body = harvest_day(day, cohort="DEVELOPMENT", spec_sha=sha, pred_pack=pred_pack, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail DEVELOPMENT {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
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
            return _stop(f"STOP. Forbidden BURNED day: {day} {blocker}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
        body = harvest_day(day, cohort="FORWARD_BURNED", spec_sha=sha, pred_pack=pred_pack, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail BURNED_STRESS {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha, source_sha=source_sha,
                prior=prior,
                pred=pred_pack,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        fwd_bodies.append(body)

    after = _prior_hashes()
    if after != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        return _stop("STOP. Prior closed-family artifacts mutated.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if (
        int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0)
        + int(leak.get("PRE_FILL_EXIT_N") or 0)
        + int(leak.get("PRE_BE_TRIGGER_N") or 0)
        + int(leak.get("PREDICATE_DRIFT_N") or 0)
        + int(leak.get("TF1_MISSING_N") or 0)
        + int(leak.get("TF3_MISSING_N") or 0)
    ):
        return _stop("STOP. Causal integrity leak != 0.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)

    dev_m = _merge(dev_bodies)
    fwd_m = _merge(fwd_bodies)
    ident_dev = evaluate_cohort(
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
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )
    ident_fwd = evaluate_cohort(
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
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )

    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide(ident_dev, ident_fwd, leak_ok=leak_ok, harvested_ok=harvested_ok, pred_ok=True)
    post = snapshot(phase="POST")
    report = {
        "analysis_id": ANALYSIS_ID,
        "candidate_id": CANDIDATE_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "candidate_identity_hash": ident_hash,
        "canonical_spec": canonical_candidate_spec(),
        "state_machine": STATE_MACHINE,
        "predicate": _public_pred(pred_pack),
        "lifecycle_prior": {
            "P_SUPPORTED": list(LIFECYCLE_P_SUPPORTED),
            "EXIT_POLICY_CREATED": bool(LIFECYCLE_EXIT_POLICY_CREATED),
            "fail_hit_rate": LIFECYCLE_BB_FAIL_HIT_RATE,
            "keep_hit_rate": LIFECYCLE_BB_KEEP_HIT_RATE,
            "day_agree": LIFECYCLE_BB_DAY_AGREE,
            "day_disagree": LIFECYCLE_BB_DAY_DISAGREE,
            "vwap_closed": True,
            "non_hop_trend_rci": True,
        },
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "CANDIDATE_FROZEN": decision.get("CANDIDATE_FROZEN"),
            "FAMILY_CLOSED": decision.get("FAMILY_CLOSED"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "CANDIDATE_IDENTITY_HASH": ident_hash,
            "PARENT_SPEC_SHA256": PARENT_SPEC_SHA256_EXPECTED,
            "first_eligible_prospective_date": FIRST_PROSPECTIVE_DAY,
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "CAP_CHANGED": False,
            "SIZING_CHANGED": False,
            "NEW_EXIT_RULE": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "CONTROL_EXIT": CONTROL_EXIT,
            "AUTO_HOP": False,
        },
        "decision": decision,
        "development": ident_dev,
        "forward": ident_fwd,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "reporting_semantics": reporting_semantics(pre, post),
        "capture_reporting_state": capture_reporting_state(pre, post),
        "submit_cancel_live": {"submit": int(SUBMIT_N), "cancel": int(CANCEL_N), "live_order": int(LIVE_ORDER_N)},
        "prior_hashes": prior,
        "answers": {},
        "_markdown": "",
    }
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
        f"FROZEN={decision.get('CANDIDATE_FROZEN')} DEV_TOTAL={ident_dev.get('TOTAL_CAUSAL_DELTA')} "
        f"out={BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if harvested_ok and leak_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
