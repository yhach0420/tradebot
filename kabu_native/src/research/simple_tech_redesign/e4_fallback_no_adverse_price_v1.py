"""Offline E4_THEN_ASK_CROSS_W5_NO_ADVERSE_PRICE_V1 full causal evaluation. Research only."""
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
from research.simple_tech_redesign.branch_u_bb_spec import DEVELOPMENT_ENTRY_STACK, PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_causal_harvest import _add_arm, _empty_arm
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.isolation import (
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT,
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
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_analyze import decide, evaluate_cohort
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_harvest import harvest_day, recover_e4_fallback_source
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_spec import (
    ANALYSIS_ID,
    AND_OR_CLOSED_EXIT,
    AUTO_HOP_P_RCI,
    AUTO_HOP_P_TREND,
    BB_BURNED_PTF_DIRECT,
    BB_DEV_PTF_DIRECT,
    CANDIDATE_ID,
    CLOSED_DO_NOT_REENTER,
    CONTROL_EXECUTION,
    CONTROL_EXIT,
    ENTRY_CHANGED,
    EXIT_CHANGED,
    FIRST_PROSPECTIVE_DAY,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    NARRATIVE_CORRECTION,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    P_PRIMITIVE_CLOSEOUT,
    PROSPECTIVE_HARVEST_SUSPENDED,
    RCI_FAIL_HIT_RATE,
    RCI_KEEP_HIT_RATE,
    SIGNAL,
    TECHNICAL_EXIT,
    THRESHOLD_SEARCH,
    TICK_SEARCH,
    TICK_TOLERANCE,
    TREATMENT_EXECUTION,
    TREND_FAIL_HIT_RATE,
    TREND_KEEP_HIT_RATE,
    TRUE_OOS,
    WAIT_SEARCH,
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
    "E4_LIMIT_MISSING_N",
    "E4_LIMIT_DRIFT_N",
    "W5_ASK_MISSING_N",
    "SESSION_CLOSE_MISSING_N",
    "SIGNAL_SET_DRIFT_N",
    "THRESHOLD_SEARCH_N",
    "TICK_SEARCH_N",
    "WAIT_SEARCH_N",
    "NEW_ENTRY_FILTER_N",
    "NEW_EXIT_RULE_N",
    "AUTO_HOP_N",
)
PRIOR_OUTS = (
    LIFECYCLE_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
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
    core = _empty_arm()
    rows: list[dict[str, Any]] = []
    unconstrained: list[dict[str, Any]] = []
    leftover_ok = True
    control_sot_ok = True
    treatment_sot_ok = True
    core_only_sot_ok = True
    for body in bodies:
        _add_arm(ctrl, dict(body.get("control") or {}))
        _add_arm(treat, dict(body.get("treatment") or {}))
        _add_arm(core, dict(body.get("core_only") or {}))
        rows.extend(list(body.get("rows") or []))
        unconstrained.extend(list(body.get("unconstrained") or []))
        leftover_ok = leftover_ok and bool(body.get("leftover_ok"))
        control_sot_ok = control_sot_ok and bool(body.get("control_sot_ok"))
        treatment_sot_ok = treatment_sot_ok and bool(body.get("treatment_sot_ok"))
        core_only_sot_ok = core_only_sot_ok and bool(body.get("core_only_sot_ok"))
    return {
        "control": ctrl,
        "treatment": treat,
        "core_only": core,
        "rows": rows,
        "unconstrained": unconstrained,
        "leftover_ok": leftover_ok,
        "control_sot_ok": control_sot_ok,
        "treatment_sot_ok": treatment_sot_ok,
        "core_only_sot_ok": core_only_sot_ok,
    }


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    src = dict(report.get("source") or {})
    ident_d = dict(dev.get("identity") or {})
    ident_f = dict(fwd.get("identity") or {})
    conc_d = dict(dev.get("concentration") or {})
    gates = dict(dec.get("gates") or {})
    gdev = dict(dev.get("groups") or {})
    gfwd = dict(fwd.get("groups") or {})
    fb = dict(gdev.get("W5_FALLBACK_CONTROL_FILL") or {})
    acc = dict(gdev.get("W5_ACCEPTED_NO_ADVERSE") or {})
    rej = dict(gdev.get("W5_REJECTED_ADVERSE_PRICE") or {})
    closeout = dict(report.get("p_primitive_closeout") or {})
    return {
        "1": (
            "objective alignment = keep fill coverage; tolerate some ENTRY quality dilution; "
            "process failures with Technical EXIT; Full Causal Portfolio PnL/PF/MaxDD; then Sizing. "
            "This run does not return to ENTRY precision. T3 is frozen. Only V26 5s Ask fallback acceptance changes."
        ),
        "2": (
            f"NARRATIVE_CORRECTION: {NARRATIVE_CORRECTION} "
            f"BB DEV PTF_DIRECT_DELTA=+{BB_DEV_PTF_DIRECT:.0f} Burned PTF_DIRECT_DELTA=+{BB_BURNED_PTF_DIRECT:.0f}."
        ),
        "3": (
            f"Branch P primitive closeout = {closeout} "
            f"AUTO_HOP={dec.get('AUTO_HOP')}"
        ),
        "4": (
            f"why Trend not run = {closeout.get('P_TREND_LOST_1M')}; "
            f"Lifecycle fail_hit={TREND_FAIL_HIT_RATE:.4f} keep_hit={TREND_KEEP_HIT_RATE:.4f}; discrimination weak. AUTO_HOP=false."
        ),
        "5": (
            f"why RCI not run = {closeout.get('P_RCI_ROLLOVER_3M')}; "
            f"Lifecycle fail_hit={RCI_FAIL_HIT_RATE:.4f} keep_hit={RCI_KEEP_HIT_RATE:.4f}; nearly ubiquitous. AUTO_HOP=false."
        ),
        "6": (
            f"exact Control execution source FILE={src.get('SOURCE_FILE')} FN={src.get('SOURCE_FUNCTION')} "
            f"SHA={src.get('SOURCE_SHA')} E4_LINE={src.get('E4_SOURCE_LINE')} FB_LINE={src.get('FALLBACK_SOURCE_LINE')}"
        ),
        "7": f"exact E4 limit price semantics = {src.get('EXACT_E4_LIMIT_PRICE')}",
        "8": (
            f"exact W5 Ask semantics wait={src.get('EXACT_WAIT_START')} "
            f"elig={src.get('EXACT_5S_ELIGIBILITY')} fresh={src.get('EXACT_ASK1_FRESHNESS')} "
            f"event_t={src.get('EXACT_FALLBACK_EVENT_T')} fill={src.get('EXACT_FILL_SEMANTICS')}"
        ),
        "9": (
            f"Control identity DEV fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} "
            f"PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}"
        ),
        "10": (
            f"Treatment identity={TREATMENT_EXECUTION}; Control identity={CONTROL_EXECUTION}; "
            f"TICK_TOLERANCE={TICK_TOLERANCE}; stack={DEVELOPMENT_ENTRY_STACK}; signal={SIGNAL}"
        ),
        "11": f"Control fallback fill N unconstrained={fb.get('N')} occupancy_ADDED={dev.get('CONTROL_ADDED_N')}",
        "12": f"Treatment accepted fallback N unconstrained={acc.get('N')} occupancy_ADDED={dev.get('TREATMENT_ADDED_N')}",
        "13": f"adverse-price rejected N={rej.get('N')}",
        "14": f"CORE causal fill N occupancy_CORE={ (dev.get('control') or {}).get('core_fill_n') } CORE-only occupancy={dev.get('CORE_ONLY_CAUSAL_FILL_N')}",
        "15": f"Control/Treatment total fill N={dev.get('CONTROL_FILL_N')}/{dev.get('TREATMENT_FILL_N')} delta={dev.get('FILL_DELTA')}",
        "16": f"ADDED retention={_n(dev.get('ADDED_RETENTION'))} Control_ADDED={dev.get('CONTROL_ADDED_N')} Treatment_ADDED={dev.get('TREATMENT_ADDED_N')}",
        "17": f"rejected-group PnL/PF={_n(rej.get('total_pnl'))}/{rej.get('PF')} N={rej.get('N')} win_rate={_n(rej.get('win_rate'))}",
        "18": f"accepted-group PnL/PF={_n(acc.get('total_pnl'))}/{acc.get('PF')} N={acc.get('N')} win_rate={_n(acc.get('win_rate'))}",
        "19": f"Control/Treatment DEV PnL={_n((dev.get('control') or {}).get('total_pnl'))}/{_n((dev.get('treatment') or {}).get('total_pnl'))}",
        "20": f"DEV delta={_n(dev.get('TOTAL_CAUSAL_DELTA'))}",
        "21": f"DEV PF Control/Treatment={(dev.get('control') or {}).get('PF')}/{(dev.get('treatment') or {}).get('PF')}",
        "22": f"DEV MaxDD Control/Treatment={_n((dev.get('control') or {}).get('max_drawdown'))}/{_n((dev.get('treatment') or {}).get('max_drawdown'))}",
        "23": f"day concentration DEV={conc_d.get('day')} warn={conc_d.get('single_day_contribution_gt_50pct')}",
        "24": f"symbol concentration DEV={conc_d.get('symbol')} warn={conc_d.get('single_symbol_contribution_gt_50pct')} 285A_excluded={conc_d.get('symbol_285A_excluded')}",
        "25": f"Burned PnL delta={_n(fwd.get('TOTAL_CAUSAL_DELTA'))} Control/Treatment={_n((fwd.get('control') or {}).get('total_pnl'))}/{_n((fwd.get('treatment') or {}).get('total_pnl'))}",
        "26": f"Burned PF Control/Treatment={(fwd.get('control') or {}).get('PF')}/{(fwd.get('treatment') or {}).get('PF')}",
        "27": f"Burned MaxDD Control/Treatment={_n((fwd.get('control') or {}).get('max_drawdown'))}/{_n((fwd.get('treatment') or {}).get('max_drawdown'))}",
        "28": f"coverage collapsed? DEV={dev.get('COVERAGE_COLLAPSED')} Burned={fwd.get('COVERAGE_COLLAPSED')} precommit=Treatment_ADDED==0 OR Treatment_fill_n<=CORE-only",
        "29": f"all gates={gates} all_dev={dec.get('all_development_gates')} burned_ok={dec.get('burned_direction_ok')}",
        "30": f"verdict={dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "31": f"candidate frozen? {dec.get('CANDIDATE_FROZEN')} id={CANDIDATE_ID}",
        "32": f"ENTRY changed? {dec.get('ENTRY_CHANGED')}",
        "33": f"EXIT changed? {dec.get('EXIT_CHANGED')} technical={TECHNICAL_EXIT} both_arms={CONTROL_EXIT}",
        "34": f"execution changed? {dec.get('EXECUTION_CHANGED')} {CONTROL_EXECUTION} -> {TREATMENT_EXECUTION}",
        "35": f"CAP changed? {dec.get('CAP_CHANGED')}",
        "36": f"sizing changed? {dec.get('SIZING_CHANGED')}",
        "37": f"future data used? {report.get('required', {}).get('FUTURE_DATA_USED')}",
        "38": f"MAX_RESEARCH_DATE={MAX_RESEARCH_DATE}",
        "39": f"prospective harvest suspended? {PROSPECTIVE_HARVEST_SUSPENDED}",
        "40": "TRUE_OOS=false",
        "41": "CERTIFIED=false",
        "42": f"submit/cancel/live={report.get('submit_cancel_live')}",
        "43": f"next={dec.get('NEXT')}",
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, prior: dict[str, str], src: dict[str, Any]) -> int:
    decision = {
        "CASE": "E",
        "VERDICT": "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_INTEGRITY_FAILED",
        "NEXT": msg,
        "CANDIDATE_FROZEN": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "EXECUTION_CHANGED": True,
        "CAP_CHANGED": False,
        "SIZING_CHANGED": False,
        "AUTO_HOP": False,
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
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
            "NARRATIVE_CORRECTION": NARRATIVE_CORRECTION,
        },
        "decision": decision,
        "source": {k: v for k, v in src.items() if k != "ok"},
        "p_primitive_closeout": dict(P_PRIMITIVE_CLOSEOUT),
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
    leak["TICK_SEARCH_N"] = int(bool(TICK_SEARCH))
    leak["WAIT_SEARCH_N"] = int(bool(WAIT_SEARCH))
    leak["AUTO_HOP_N"] = int(bool(AUTO_HOP_P_TREND or AUTO_HOP_P_RCI))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["FUTURE_DATA_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(
        f"PREFLIGHT candidate={CANDIDATE_ID} spec={sha[:12]} today={TODAY} signal={SIGNAL} "
        f"control={CONTROL_EXECUTION} treatment={TREATMENT_EXECUTION}",
        flush=True,
    )

    src_pack = recover_e4_fallback_source()
    if not src_pack.get("ok"):
        return _stop(
            f"STOP. Exact Control E4/fallback restore failed: {src_pack.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            src=src_pack,
        )

    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or TICK_SEARCH or WAIT_SEARCH or bool(TRUE_OOS) or NEW_ENTRY_FILTER:
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
    if AUTO_HOP_P_TREND or AUTO_HOP_P_RCI or AND_OR_CLOSED_EXIT or not PROSPECTIVE_HARVEST_SUSPENDED:
        return _stop("STOP. Auto-hop or AND/OR closed EXIT or prospective flag.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
    if ENTRY_CHANGED or EXIT_CHANGED or int(TICK_TOLERANCE) != 0:
        return _stop("STOP. ENTRY/EXIT/tick-tolerance drifted.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Day list includes 20260903+.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [
            str(LIFECYCLE_OUT / "report.json"),
            str(BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT / "report.json"),
            str(BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)

    harvested_ok = True
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden DEV day: {day} {blocker}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
        body = harvest_day(day, cohort="DEVELOPMENT", spec_sha=sha, pred_pack=src_pack, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail DEVELOPMENT {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                prior=prior,
                src=src_pack,
            )
        for k, v in dict(body.get("leak") or {}).items():
            if k in leak or str(k).endswith("_N"):
                leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        dev_bodies.append(body)
    for day in list(LOCKED_SERIES_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden BURNED day: {day} {blocker}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
        body = harvest_day(day, cohort="FORWARD_BURNED", spec_sha=sha, pred_pack=src_pack, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail BURNED_STRESS {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                prior=prior,
                src=src_pack,
            )
        for k, v in dict(body.get("leak") or {}).items():
            if k in leak or str(k).endswith("_N"):
                leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        fwd_bodies.append(body)

    after = _prior_hashes()
    if after != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        return _stop("STOP. Prior closed-family artifacts mutated.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)
    if int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0) + int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0):
        return _stop("STOP. Causal integrity leak != 0.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, src=src_pack)

    dev_m = _merge(dev_bodies)
    fwd_m = _merge(fwd_bodies)
    ident_dev = evaluate_cohort(
        cohort="DEVELOPMENT",
        days=list(ELIGIBLE_DAYS),
        harvest_rows=list(dev_m.get("rows") or []),
        unconstrained=list(dev_m.get("unconstrained") or []),
        ctrl_arm=dict(dev_m.get("control") or {}),
        treat_arm=dict(dev_m.get("treatment") or {}),
        core_arm=dict(dev_m.get("core_only") or {}),
        leftover_ok=bool(dev_m.get("leftover_ok")),
        control_sot_ok=bool(dev_m.get("control_sot_ok")),
        treatment_sot_ok=bool(dev_m.get("treatment_sot_ok")),
        core_only_sot_ok=bool(dev_m.get("core_only_sot_ok")),
    )
    if not bool(ident_dev.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. DEV CONTROL identity mismatch: {ident_dev.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            src=src_pack,
        )
    ident_fwd = evaluate_cohort(
        cohort="FORWARD_BURNED",
        days=list(LOCKED_SERIES_DAYS),
        harvest_rows=list(fwd_m.get("rows") or []),
        unconstrained=list(fwd_m.get("unconstrained") or []),
        ctrl_arm=dict(fwd_m.get("control") or {}),
        treat_arm=dict(fwd_m.get("treatment") or {}),
        core_arm=dict(fwd_m.get("core_only") or {}),
        leftover_ok=bool(fwd_m.get("leftover_ok")),
        control_sot_ok=bool(fwd_m.get("control_sot_ok")),
        treatment_sot_ok=bool(fwd_m.get("treatment_sot_ok")),
        core_only_sot_ok=bool(fwd_m.get("core_only_sot_ok")),
    )
    if not bool(ident_fwd.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. BURNED CONTROL identity mismatch: {ident_fwd.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            src=src_pack,
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
        "source": {k: v for k, v in src_pack.items() if k != "ok"},
        "p_primitive_closeout": dict(P_PRIMITIVE_CLOSEOUT),
        "narrative_correction": NARRATIVE_CORRECTION,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "CANDIDATE_FROZEN": decision.get("CANDIDATE_FROZEN"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "CANDIDATE_IDENTITY_HASH": ident_hash,
            "PARENT_SPEC_SHA256": PARENT_SPEC_SHA256_EXPECTED,
            "first_eligible_prospective_date": FIRST_PROSPECTIVE_DAY,
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "EXECUTION_CHANGED": True,
            "CAP_CHANGED": False,
            "SIZING_CHANGED": False,
            "NEW_EXIT_RULE": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "CONTROL_EXIT": CONTROL_EXIT,
            "TECHNICAL_EXIT": TECHNICAL_EXIT,
            "AUTO_HOP": False,
            "NARRATIVE_CORRECTION": NARRATIVE_CORRECTION,
            "TICK_TOLERANCE": int(TICK_TOLERANCE),
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
        f"FROZEN={decision.get('CANDIDATE_FROZEN')} DEV_DELTA={ident_dev.get('TOTAL_CAUSAL_DELTA')} "
        f"out={E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if harvested_ok and leak_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
