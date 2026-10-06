"""Offline T3 P2 pullback-touch-age sequence V1. No prospective harvest. No candidate eval."""
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
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_c0_indicator_exit.isolation import advanced
from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.isolation import (
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_analyze import capture_reporting_state
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_analyze import decide, evaluate
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_harvest import harvest_blocks
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.precap_t3_setup_sequence_mechanism_v1_spec import (
    ANALYSIS_ID,
    FORBIDDEN_INPUT_DAYS,
    FUTURE_DATA_USED,
    MAX_RESEARCH_DATE,
    NEXT_CANDIDATE_ID,
    NEXT_CANDIDATE_RULE,
    PARENT_EXISTING_DATA_VERDICT,
    PARENT_R1_ID,
    PRIMARY_NEXT_MECHANISM_PRIOR,
    PROSPECTIVE_HARVEST_SUSPENDED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    all_research_days,
    canonical_sequence_spec,
    source_sha256_sequence,
    spec_sha256_sequence,
)

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "RESEARCH_WRITE_PATH_OVERLAP_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "INPUT_20260903_N",
    "INPUT_20260904_N",
    "THRESHOLD_SEARCH_N",
    "FUTURE_DATA_N",
    "PROSPECTIVE_HARVESTER_RUN_N",
    "PRIOR_ARTIFACT_MUTATED_N",
    "FUTURE_BAR_USE_N",
)
PRIOR_OUTS = (
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _prior_hashes() -> dict[str, str]:
    out = {}
    for folder in PRIOR_OUTS:
        for name in ("report.json", "report.md", "audit.xlsx"):
            out[f"{folder.name}/{name}"] = _sha_file(folder / name)
    return out


def _daily(ev: dict[str, Any], bk: str) -> dict[str, Any]:
    d = ((ev.get("daily") or {}).get(bk) or {})
    return {
        "evaluable_day_n": d.get("evaluable_day_n"),
        "positive_rho_day_n": d.get("positive_rho_day_n"),
        "negative_rho_day_n": d.get("negative_rho_day_n"),
        "zero_rho_day_n": d.get("zero_rho_day_n"),
        "median_daily_rho": d.get("median_daily_rho"),
        "IQR_daily_rho": d.get("IQR_daily_rho"),
        "direction": d.get("direction"),
    }


def _econ(ev: dict[str, Any], age: str) -> dict[str, Any]:
    out = {}
    for bk, pack in dict(ev.get("economics") or {}).items():
        out[bk] = dict((pack or {}).get(age) or {})
    return out


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    cap = dict(report.get("capture_reporting_state") or {})
    pool = dict(ev.get("pool_identity") or {})
    seq = dict(ev.get("sequence_integrity") or {})
    conc = dict(ev.get("concentration") or {})
    cand = dict(dec.get("NEXT_CANDIDATE") or {})
    return {
        "1": f"pool identity A/B/C = {pool.get('pool_n')} outcome={pool.get('outcome_evaluable_n')} attrition={pool.get('attrition_n')} ok={pool.get('pool_identity_ok')}",
        "2": f"exact P2 reference parity recovered_all={seq.get('recovered_all')} fail_n={seq.get('fail_n')} reasons={seq.get('fail_by_reason')} future_bar_use_n={seq.get('future_bar_use_n')} unknown_n={seq.get('unknown_n')} recovered_n={pool.get('sequence_recovered_n')}",
        "3": f"touch-age distribution A/B/C = {ev.get('age_distribution')}",
        "4": f"NO_FILL n={ev.get('no_fill_n')} touch-age = {ev.get('no_fill_age_distribution')}",
        "5": f"BLOCK A daily rho = {_daily(ev, 'BLOCK_A_DISCOVERY')}",
        "6": f"BLOCK B daily rho = {_daily(ev, 'BLOCK_B_INTERNAL_STABILITY')}",
        "7": f"BLOCK C daily rho = {_daily(ev, 'BLOCK_C_BURNED_STRESS')}",
        "8": f"AGE0 economics = {_econ(ev, 'AGE0')}",
        "9": f"AGE1 economics = {_econ(ev, 'AGE1')}",
        "10": f"AGE2 economics = {_econ(ev, 'AGE2')}",
        "11": f"admitted/blocked = gate6={ev.get('gate6')} adm_med={(ev.get('admitted') or {}).get('median_daily_rho')} blk_med={(ev.get('cap_blocked') or {}).get('median_daily_rho')}",
        "12": f"CORE/ADDED core_eval={(ev.get('core') or {}).get('evaluable_day_n')} core_med={(ev.get('core') or {}).get('median_daily_rho')} added_med={(ev.get('added') or {}).get('median_daily_rho')} added_neg={ev.get('added_negative')} core_small_n={(ev.get('core') or {}).get('small_n_warning')}",
        "13": f"arrival thirds negative_n={ev.get('arrival_negative_n')} strata={ {k: (v.get('pooled_spearman') or {}).get('rho') for k, v in dict(ev.get('arrival_strata') or {}).items()} }",
        "14": f"day concentration top={(conc.get('day') or {}).get('rho_abs', {}).get('top')} share={(conc.get('day') or {}).get('rho_abs', {}).get('share_of_abs')} warn={(conc.get('day') or {}).get('warning_gt_50pct')}",
        "15": f"symbol concentration top={(conc.get('symbol') or {}).get('top')} share={(conc.get('symbol') or {}).get('share_of_abs')} warn={(conc.get('symbol') or {}).get('warning_gt_50pct')}",
        "16": f"TOUCH_COUNT diagnostic = {ev.get('touch_count_diagnostic')}",
        "17": f"setup geometry diagnostic = {ev.get('geometry_diagnostic')}",
        "18": f"QUALIFY gates = {ev.get('qualify_gates')} qualify={ev.get('qualify')}",
        "19": f"verdict = {dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "20": f"PRIMARY_NEXT_MECHANISM = {dec.get('PRIMARY_NEXT_MECHANISM')}",
        "21": f"candidate precommitted = true id={cand.get('candidate_id')}",
        "22": f"candidate exact rule = {cand.get('rule')}",
        "23": f"candidate evaluated this run = {dec.get('candidate_evaluated_this_run')}",
        "24": "future data used = false",
        "25": f"MAX_RESEARCH_DATE = {MAX_RESEARCH_DATE}",
        "26": "prospective harvest suspended = true",
        "27": "TRUE_OOS = false",
        "28": "CERTIFIED = false",
        "29": f"submit/cancel/live = {report.get('submit_cancel_live')}",
        "30": f"next = {dec.get('NEXT')}",
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, prior: dict[str, str]) -> int:
    decision = decide({"identity_ok": False, "sequence_integrity": {"recovered_all": False, "unknown_n": 1}, "features": []}, leak_ok=False)
    decision["NEXT"] = msg
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision["VERDICT"],
            "CASE": "D",
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        },
        "decision": decision,
        "evaluation": {},
        "leak": leak,
        "preflight": pre,
        "prior_closed_family": {
            "PARENT_R1_ID": PARENT_R1_ID,
            "VERDICT": PARENT_EXISTING_DATA_VERDICT,
            "PRIMARY_NEXT_MECHANISM_PRIOR": PRIMARY_NEXT_MECHANISM_PRIOR,
            "hashes": prior,
        },
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
    sha = spec_sha256_sequence()
    source_sha = source_sha256_sequence()
    prior = _prior_hashes()
    pre = snapshot(phase="PRE")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["FUTURE_DATA_N"] = int(bool(FUTURE_DATA_USED))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(
        f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY} MAX_RESEARCH_DATE={MAX_RESEARCH_DATE} "
        f"PROSPECTIVE_HARVEST_SUSPENDED=true PRIMARY=P2_TOUCH_AGE_BARS",
        flush=True,
    )
    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    if bool(TRUE_OOS) or bool(THRESHOLD_SEARCH) or (not PROSPECTIVE_HARVEST_SUSPENDED):
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    if prior.get("precap_entry_quality_existing_data_mechanism_v1_r1/report.json") == "MISSING":
        return _stop("STOP. Parent R1 artifacts missing.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    r1 = json.loads((PRECAP_EXISTING_MECHANISM_V1_R1_OUT / "report.json").read_text(encoding="utf-8"))
    r1_verdict = str((r1.get("required") or {}).get("VERDICT") or (r1.get("decision") or {}).get("VERDICT") or "")
    if r1_verdict != PARENT_EXISTING_DATA_VERDICT:
        return _stop("STOP. Parent existing-data verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    for day in all_research_days():
        bad = assert_research_day(day, today=TODAY)
        if bad:
            return _stop(f"STOP. {bad}:{day}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
        if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE):
            leak["FUTURE_DATA_N"] = 1
            return _stop("STOP. Future/forbidden day in research list.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [
            str(PRECAP_EXISTING_MECHANISM_V1_OUT / "report.json"),
            str(PRECAP_EXISTING_MECHANISM_V1_R1_OUT / "report.json"),
            str(PRECAP_T3_SETUP_SEQUENCE_V1_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]) or int(leak["INPUT_20260903_N"]) or int(leak["INPUT_20260904_N"]):
        return _stop("STOP. Active or forbidden capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior)
    harvested = harvest_blocks(spec_sha=sha, today=TODAY)
    if not harvested.get("ok"):
        return _stop(
            f"STOP. Harvest failed {harvested.get('date')}: {harvested.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
        )
    leak["FUTURE_BAR_USE_N"] = int((harvested.get("sequence_integrity") or {}).get("future_bar_use_n") or 0)
    pack = evaluate(dict(harvested.get("bodies") or {}), harvested=harvested)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide(pack, leak_ok=leak_ok)
    post = snapshot(phase="POST")
    pid = advanced(pre, post)
    cap_state = capture_reporting_state(pre, post)
    prior_post = _prior_hashes()
    if prior != prior_post:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        leak_ok = False
        decision = decide(pack, leak_ok=False)
        decision["integrity_reasons"] = list(decision.get("integrity_reasons") or []) + ["PRIOR_ARTIFACT_MUTATED"]
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_sequence_spec(),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "PRIMARY_FIELD": "P2_TOUCH_AGE_BARS",
            "PRIMARY_NEXT_MECHANISM": decision.get("PRIMARY_NEXT_MECHANISM"),
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "BURNED_EXISTING_DATA_ONLY": True,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_FROZEN": True,
            "PROSPECTIVE_ARMED": False,
            "NEW_ENTRY_FILTER": False,
            "NEW_EXIT_RULE": False,
            "CAP_CHANGED": False,
            "ABSOLUTE_FEATURE_FAMILY_CLOSED": True,
            "PARENT_EXISTING_DATA_VERDICT": PARENT_EXISTING_DATA_VERDICT,
            "PRIMARY_NEXT_MECHANISM_PRIOR": PRIMARY_NEXT_MECHANISM_PRIOR,
            "NEXT_CANDIDATE_ID": NEXT_CANDIDATE_ID,
            "NEXT_CANDIDATE_RULE": NEXT_CANDIDATE_RULE,
            "CAPTURE_STATE": cap_state.get("CAPTURE_STATE"),
            "FLIP_DIRECTION_FROM_RESULTS": False,
        },
        "decision": decision,
        "evaluation": pack,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "pid": pid,
        "reporting_semantics": reporting_semantics(pre, post),
        "capture_reporting_state": cap_state,
        "prior_closed_family": {
            "PARENT_R1_ID": PARENT_R1_ID,
            "VERDICT": PARENT_EXISTING_DATA_VERDICT,
            "PRIMARY_NEXT_MECHANISM_PRIOR": PRIMARY_NEXT_MECHANISM_PRIOR,
            "hashes_unchanged": prior == prior_post,
        },
        "submit_cancel_live": [int(SUBMIT_N), int(CANCEL_N), int(LIVE_ORDER_N)],
        "answers": {},
        "_markdown": "",
    }
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    if _prior_hashes() != prior:
        print("STOP. Prior artifacts mutated.", flush=True)
        print("STOP.", flush=True)
        return 2
    print(
        f"CASE {decision.get('CASE')} {decision.get('VERDICT')} qualify={decision.get('qualify')} "
        f"CAPTURE_STATE={cap_state.get('CAPTURE_STATE')} out={PRECAP_T3_SETUP_SEQUENCE_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if leak_ok and pack.get("identity_ok") and decision.get("CASE") != "D" else 2


if __name__ == "__main__":
    raise SystemExit(main())
