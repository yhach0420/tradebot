"""Offline POST_BE_CONFIRMED_SWING_FLOOR_V1 full causal portfolio evaluation."""
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
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT,
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_analyze import decide, evaluate_cohort
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_harvest import harvest_day
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_spec import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    CLOSED_DO_NOT_REENTER,
    CONTROL_EXIT,
    EXECUTION,
    FAMILY_B_SWING_AVAILABLE_FROZEN,
    FIRST_PROSPECTIVE_DAY,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    P2_TOUCH_AGE_THIS_RUN,
    PRIMARY_ARCHITECTURE,
    PROSPECTIVE_HARVEST_SUSPENDED,
    SIGNAL,
    STATE_MACHINE,
    THRESHOLD_SEARCH,
    TF_SEARCH,
    TIMEFRAME,
    TRUE_OOS,
    UNCONSTRAINED_ADDED_FILL_N,
    UNCONSTRAINED_CORE_FILL_N,
    UNCONSTRAINED_EXECUTION_EVALUABLE_N,
    UNCONSTRAINED_SIGNAL_N,
    UNCONSTRAINED_TOTAL_RESEARCH_FILL_N,
    canonical_candidate_spec,
    candidate_identity_hash,
    source_sha256_candidate,
    spec_sha256_candidate,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_analyze import capture_reporting_state
from research.simple_tech_redesign.v29_spec import FAMILY_B_SWING_AVAILABLE

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
    "PIVOT_BACKDATE_N",
    "BE_REACHED_FLAG_MISMATCH_N",
    "TF1_MISSING_N",
    "FILL_PACK_INCOMPLETE_N",
    "THRESHOLD_SEARCH_N",
    "TF_SEARCH_N",
    "NEW_ENTRY_FILTER_N",
    "NEW_EXIT_RULE_N",
)
PRIOR_OUTS = (
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT,
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


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    ident_d = dict(dev.get("identity") or {})
    ident_f = dict(fwd.get("identity") or {})
    conc_d = dict(dev.get("concentration") or {})
    conc_f = dict(fwd.get("concentration") or {})
    gates = dict(dec.get("gates") or {})
    return {
        "1": (
            "objective alignment = keep fill coverage; tolerate some ENTRY quality loss; "
            "process failures with Technical EXIT; improve Full Causal Portfolio PnL/PF/MaxDD; then Sizing. "
            "Not ENTRY-precision maximization."
        ),
        "2": (
            "existing-work duplication check = not V27 EMA persistence, not V28 K6, not V29 terminal sequence, "
            "not Branch U BB, not Branch P second-BE, not entry-anchored SETUP_LOW floor. "
            f"This is post-BE confirmed 1m 3-bar swing-low ratchet-up Close-break only. "
            f"FAMILY_B_SWING_AVAILABLE remains {FAMILY_B_SWING_AVAILABLE} / frozen-spec {FAMILY_B_SWING_AVAILABLE_FROZEN}."
        ),
        "3": f"closed-family non-reentry = {list(CLOSED_DO_NOT_REENTER)}; P2_TOUCH_AGE_THIS_RUN={P2_TOUCH_AGE_THIS_RUN}",
        "4": (
            f"Control identity DEV fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} "
            f"PnL={ident_d.get('pnl')} expected 84/18/66/+146680 ok={ident_d.get('ok')}"
        ),
        "5": (
            f"Control identity Burned Stress fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} "
            f"PnL={ident_f.get('pnl')} expected 20/9/11/-45100 ok={ident_f.get('ok')}"
        ),
        "6": (
            "swing definition = confirmed 3-bar pivot Low[j]<Low[j-1] AND Low[j]<Low[j+1]; equal-low is not a pivot; "
            "1m completed bars only; floor = pivot low; ratchet up only."
        ),
        "7": (
            "pivot causal confirmation parity = confirmation_time is finalize of bar[j+1]; "
            f"pivot_backdate_n={report.get('leak', {}).get('PIVOT_BACKDATE_N')}; no backdate to pivot center."
        ),
        "8": (
            "first-BE arm parity = walk_break_even fresh executable Bid1, 100-share net PnL>=0, fees excluded, ARM only; "
            f"BE_REACHED_FLAG_MISMATCH_N={report.get('leak', {}).get('BE_REACHED_FLAG_MISMATCH_N')} "
            f"DEV_mismatch={ident_d.get('be_flag_mismatch_n')} FWD_mismatch={ident_f.get('be_flag_mismatch_n')}"
        ),
        "9": f"technical exit N DEV={dev.get('technical_exit_n')} BURNED={fwd.get('technical_exit_n')} common_trigger_DEV={dev.get('common_trigger_n')}",
        "10": f"path-by-path direct delta DEV={dev.get('path_audit')}",
        "11": f"P_EARLY direct delta={_n(dev.get('P_EARLY_DIRECT_DELTA'))}",
        "12": f"PTF direct delta={_n(dev.get('PTF_DIRECT_DELTA'))}",
        "13": f"GOOD direct delta={_n(dev.get('GOOD_DIRECT_DELTA'))}",
        "14": f"DIP direct delta={_n(dev.get('DIP_DIRECT_DELTA'))}",
        "15": f"CORE direct delta={_n(dev.get('CORE_DIRECT_DELTA'))} CORE_winner={_n(dev.get('CORE_WINNER_DIRECT_DELTA'))} n={dev.get('CORE_WINNER_N')}",
        "16": f"ADDED direct delta={_n(dev.get('ADDED_DIRECT_DELTA'))}",
        "17": f"Control/Treatment DEV PnL={_n((dev.get('control') or {}).get('total_pnl'))} / {_n((dev.get('treatment') or {}).get('total_pnl'))}",
        "18": f"Control/Treatment DEV PF={(dev.get('control') or {}).get('PF')} / {(dev.get('treatment') or {}).get('PF')}",
        "19": f"Control/Treatment DEV MaxDD={_n((dev.get('control') or {}).get('max_drawdown'))} / {_n((dev.get('treatment') or {}).get('max_drawdown'))}",
        "20": f"direct exit delta A={_n(dev.get('DIRECT_EXIT_DELTA'))}",
        "21": f"slot-release downstream delta B={_n(dev.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))}",
        "22": f"displaced-trade delta C={_n(dev.get('DISPLACED_TRADE_DELTA'))}",
        "23": f"total causal delta={_n(dev.get('TOTAL_CAUSAL_DELTA'))} decomp_ok={dev.get('decomp_ok')}",
        "24": f"fill count change={dev.get('fill_n_change')} Control={(dev.get('control') or {}).get('fill_n')} Treatment={(dev.get('treatment') or {}).get('fill_n')}",
        "25": f"day concentration DEV={conc_d.get('day')} warn={conc_d.get('single_day_contribution_gt_50pct')}",
        "26": f"symbol concentration DEV={conc_d.get('symbol')} warn={conc_d.get('single_symbol_contribution_gt_50pct')} 285A_excluded={conc_d.get('symbol_285A_excluded')}",
        "27": (
            f"Burned Stress economics Control PnL/PF/MaxDD/fill="
            f"{_n((fwd.get('control') or {}).get('total_pnl'))}/{(fwd.get('control') or {}).get('PF')}/"
            f"{_n((fwd.get('control') or {}).get('max_drawdown'))}/{(fwd.get('control') or {}).get('fill_n')} "
            f"Treatment={_n((fwd.get('treatment') or {}).get('total_pnl'))}/{(fwd.get('treatment') or {}).get('PF')}/"
            f"{_n((fwd.get('treatment') or {}).get('max_drawdown'))}/{(fwd.get('treatment') or {}).get('fill_n')} "
            f"TOTAL_CAUSAL_DELTA={_n(fwd.get('TOTAL_CAUSAL_DELTA'))}"
        ),
        "28": (
            f"Burned Stress direction TOTAL={_n(fwd.get('TOTAL_CAUSAL_DELTA'))} "
            f"FAILURE_DIRECT={_n(fwd.get('FAILURE_DIRECT_DELTA'))} "
            f"not_reversed={gates.get('burned_failure_direct_not_reversed')} "
            f"total_ge_0={gates.get('burned_total_causal_delta_ge_0')}"
        ),
        "29": (
            f"winner-harm gate PROTECTED_GOOD_DIP_DIRECT_DELTA={_n(dev.get('PROTECTED_GOOD_DIP_DIRECT_DELTA'))} "
            f"pass={gates.get('3_PROTECTED_GOOD_DIP_DIRECT_DELTA_ge_0')}"
        ),
        "30": f"all qualification gates={gates} all_dev={dec.get('all_development_gates')}",
        "31": f"verdict={dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "32": f"candidate frozen? {dec.get('CANDIDATE_FROZEN')} id={CANDIDATE_ID}",
        "33": "ENTRY changed? false",
        "34": "EXIT changed? false at runtime; research evaluated POST_BE_SWING_FLOOR_BREAK only",
        "35": "CAP changed? false",
        "36": "sizing changed? false",
        "37": "future data used? false",
        "38": f"MAX_RESEARCH_DATE={MAX_RESEARCH_DATE}",
        "39": f"prospective harvest suspended? {PROSPECTIVE_HARVEST_SUSPENDED}",
        "40": "TRUE_OOS=false",
        "41": "CERTIFIED=false",
        "42": f"submit/cancel/live={report.get('submit_cancel_live')}",
        "43": f"next={dec.get('NEXT')}",
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, prior: dict[str, str]) -> int:
    decision = {
        "CASE": "E",
        "VERDICT": "SIMPLE_TECH_POST_BE_SWING_FLOOR_INTEGRITY_FAILED",
        "NEXT": msg,
        "CANDIDATE_FROZEN": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "FAMILY_CLOSED": False,
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
            "CANDIDATE_IDENTITY_HASH": candidate_identity_hash(spec_sha=sha, source_sha=source_sha),
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
        },
        "decision": decision,
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
    leak["TF_SEARCH_N"] = int(bool(TF_SEARCH))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["FUTURE_DATA_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(
        f"PREFLIGHT candidate={CANDIDATE_ID} spec={sha[:12]} today={TODAY} "
        f"signal={SIGNAL} exec={EXECUTION} tf={TIMEFRAME} arch={PRIMARY_ARCHITECTURE}",
        flush=True,
    )
    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or TF_SEARCH or bool(TRUE_OOS) or NEW_ENTRY_FILTER:
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    if P2_TOUCH_AGE_THIS_RUN or not PROSPECTIVE_HARVEST_SUSPENDED:
        return _stop("STOP. Closed-family or prospective flag.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Day list includes 20260903+.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [
            str(PRECAP_T3_SETUP_SEQUENCE_V1_OUT / "report.json"),
            str(ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    try:
        residual_dev, residual_fwd, _residual_body = load_residual_rows()
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Residual SoT: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)

    harvested_ok = True
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["INPUT_20260903_N"] = int(day == "20260903")
            leak["INPUT_20260904_N"] = int(day == "20260904")
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden DEV day: {day} {blocker}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
        body = harvest_day(day, cohort="DEVELOPMENT", spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            print(f"harvest fail DEVELOPMENT {day}: {body.get('blocker')}", flush=True)
            return _stop(
                f"STOP. Harvest/identity fail DEVELOPMENT {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                prior=prior,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        dev_bodies.append(body)
    for day in list(LOCKED_SERIES_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden BURNED day: {day} {blocker}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
        body = harvest_day(day, cohort="FORWARD_BURNED", spec_sha=sha, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            print(f"harvest fail BURNED_STRESS {day}: {body.get('blocker')}", flush=True)
            return _stop(
                f"STOP. Harvest/identity fail BURNED_STRESS {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                prior=prior,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        fwd_bodies.append(body)

    after = _prior_hashes()
    if after != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        return _stop("STOP. Prior closed-family artifacts mutated.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)

    carry = (
        int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0)
        + int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0)
        + int(leak.get("PRE_FILL_EXIT_N") or 0)
        + int(leak.get("PRE_BE_TRIGGER_N") or 0)
        + int(leak.get("PIVOT_BACKDATE_N") or 0)
        + int(leak.get("BE_REACHED_FLAG_MISMATCH_N") or 0)
        + int(leak.get("TF1_MISSING_N") or 0)
    )
    if carry:
        return _stop("STOP. Causal integrity leak != 0.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)

    dev_m = _merge(dev_bodies)
    fwd_m = _merge(fwd_bodies)
    ident_dev_preview = evaluate_cohort(
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
    if not bool(ident_dev_preview.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. DEV CONTROL identity mismatch: {ident_dev_preview.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
        )
    ident_fwd_preview = evaluate_cohort(
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
    if not bool(ident_fwd_preview.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. BURNED CONTROL identity mismatch: {ident_fwd_preview.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
        )

    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide(ident_dev_preview, ident_fwd_preview, leak_ok=leak_ok, harvested_ok=harvested_ok)
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    cap_state = capture_reporting_state(pre, post)
    report = {
        "analysis_id": ANALYSIS_ID,
        "candidate_id": CANDIDATE_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "candidate_identity_hash": ident_hash,
        "canonical_spec": canonical_candidate_spec(),
        "state_machine": STATE_MACHINE,
        "unconstrained": {
            "SIGNAL_N": UNCONSTRAINED_SIGNAL_N,
            "EXECUTION_EVALUABLE_N": UNCONSTRAINED_EXECUTION_EVALUABLE_N,
            "CORE_FILL_N": UNCONSTRAINED_CORE_FILL_N,
            "ADDED_FILL_N": UNCONSTRAINED_ADDED_FILL_N,
            "TOTAL_RESEARCH_FILL_N": UNCONSTRAINED_TOTAL_RESEARCH_FILL_N,
        },
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
            "CAP_CHANGED": False,
            "SIZING_CHANGED": False,
            "THRESHOLD_SEARCH": False,
            "NEW_EXIT_RULE": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "CONTROL_EXIT": CONTROL_EXIT,
        },
        "decision": decision,
        "development": ident_dev_preview,
        "forward": ident_fwd_preview,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "reporting_semantics": reporting,
        "capture_reporting_state": cap_state,
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
        f"FROZEN={decision.get('CANDIDATE_FROZEN')} DEV_TOTAL={ident_dev_preview.get('TOTAL_CAUSAL_DELTA')} "
        f"out={POST_BE_SWING_FLOOR_EXIT_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if harvested_ok and leak_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
