"""Offline existing-data ENTRY quality mechanism V1 R1. Daily Spearman. No prospective harvest."""
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
    PRECAP_TIMING_RCA_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_analyze import (
    capture_reporting_state,
    decide,
    evaluate,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_harvest import harvest_blocks
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_spec import (
    ANALYSIS_ID,
    FEATURES,
    FORBIDDEN_INPUT_DAYS,
    FUTURE_DATA_USED,
    MAX_RESEARCH_DATE,
    MISSING_POLICY_FROZEN,
    NEXT_CANDIDATE_ID_IF_CASE_A,
    NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED,
    PARENT_VERDICT,
    PRIOR_ANALYSIS_ID,
    PRIOR_VERDICT,
    PROSPECTIVE_HARVEST_SUSPENDED,
    SUPERSEDED_FOR_DECISION,
    SUPERSEDED_REASON,
    THRESHOLD_POLICY,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    all_research_days,
    canonical_mechanism_spec,
    source_sha256_mechanism,
    spec_sha256_mechanism,
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
    "V1_ARTIFACT_MUTATED_N",
    "CAPTURE_RESTREAM_N",
)
V1_ARTIFACTS = ("report.json", "report.md", "audit.xlsx")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _v1_hashes() -> dict[str, str]:
    return {name: _sha_file(PRECAP_EXISTING_MECHANISM_V1_OUT / name) for name in V1_ARTIFACTS}


def _feat(pack: dict[str, Any], name: str) -> dict[str, Any]:
    for f in list(pack.get("features") or []):
        if f.get("feature") == name:
            return dict(f)
    return {}


def _daily_line(feat: dict[str, Any], bk: str) -> dict[str, Any]:
    d = ((feat.get("daily") or {}).get(bk) or {})
    return {
        "evaluable_day_n": d.get("evaluable_day_n"),
        "positive_rho_day_n": d.get("positive_rho_day_n"),
        "negative_rho_day_n": d.get("negative_rho_day_n"),
        "zero_rho_day_n": d.get("zero_rho_day_n"),
        "median_daily_rho": d.get("median_daily_rho"),
        "IQR_daily_rho": d.get("IQR_daily_rho"),
        "direction": d.get("direction"),
    }


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    cap = dict(report.get("capture_reporting_state") or {})
    pool = dict(ev.get("pool_identity") or {})
    att = dict(ev.get("attrition") or {})
    f1, f2, f3, f4 = [_feat(ev, n) for n in FEATURES]
    by_block = dict(att.get("by_block") or {})

    def _attr_txt(bk: str) -> str:
        b = dict(by_block.get(bk) or {})
        return f"n={b.get('n')} reasons={b.get('by_reason')} admitted={b.get('by_control_admitted')} cap={b.get('by_cap_only_blocked')} role={b.get('by_fill_role')} arrival={b.get('by_arrival_third')} day={b.get('by_day')}"

    conc_lines = []
    for f in (f1, f2, f3, f4):
        conc = dict(f.get("concentration") or {})
        conc_lines.append(
            f"{f.get('feature')}: day_top={(conc.get('day') or {}).get('rho_abs', {}).get('top')} "
            f"day_share={(conc.get('day') or {}).get('rho_abs', {}).get('share_of_abs')} "
            f"sym_top={(conc.get('symbol') or {}).get('top')} "
            f"sym_share={(conc.get('symbol') or {}).get('share_of_abs')} "
            f"warn={conc.get('warning_gt_50pct')}"
        )
    return {
        "1": f"pool identity A/B/C = {pool.get('pool_n')} outcome={pool.get('outcome_evaluable_n')} ok={pool.get('pool_identity_ok')}",
        "2": (
            f"outcome attrition A/B/C = {pool.get('attrition_n')} unexplained={att.get('unexplained_n')}; "
            f"A: {_attr_txt('BLOCK_A_DISCOVERY')}; B: {_attr_txt('BLOCK_B_INTERNAL_STABILITY')}; C: {_attr_txt('BLOCK_C_BURNED_STRESS')}"
        ),
        "3": f"feature availability all-100pct={ev.get('availability_all_100pct')} missing={json.dumps(json_sanitize(ev.get('missing') or {}), ensure_ascii=False, default=str)}",
        "4": f"F1 volume_percentile_60s A={_daily_line(f1, 'BLOCK_A_DISCOVERY')} B={_daily_line(f1, 'BLOCK_B_INTERNAL_STABILITY')} C={_daily_line(f1, 'BLOCK_C_BURNED_STRESS')}",
        "5": f"F2 distance_from_vwap_bps A={_daily_line(f2, 'BLOCK_A_DISCOVERY')} B={_daily_line(f2, 'BLOCK_B_INTERNAL_STABILITY')} C={_daily_line(f2, 'BLOCK_C_BURNED_STRESS')}",
        "6": f"F3 rebound_from_recent_low_bps A={_daily_line(f3, 'BLOCK_A_DISCOVERY')} B={_daily_line(f3, 'BLOCK_B_INTERNAL_STABILITY')} C={_daily_line(f3, 'BLOCK_C_BURNED_STRESS')}",
        "7": f"F4 trading_value_percentile_180s A={_daily_line(f4, 'BLOCK_A_DISCOVERY')} B={_daily_line(f4, 'BLOCK_B_INTERNAL_STABILITY')} C={_daily_line(f4, 'BLOCK_C_BURNED_STRESS')}",
        "8": (
            f"admitted/blocked daily-rho: F1 adm={ (f1.get('admitted') or {}).get('median_daily_rho') }/"
            f"{(f1.get('admitted') or {}).get('evaluable_day_n')} blk={(f1.get('cap_blocked') or {}).get('median_daily_rho')}/"
            f"{(f1.get('cap_blocked') or {}).get('evaluable_day_n')} not_one_sided={f1.get('not_one_sided')}; "
            f"F2 not_one_sided={f2.get('not_one_sided')}; F3 not_one_sided={f3.get('not_one_sided')}; F4 not_one_sided={f4.get('not_one_sided')}"
        ),
        "9": (
            f"CORE/ADDED: F1 core_eval={(f1.get('core') or {}).get('evaluable_day_n')} added_rho={(f1.get('added') or {}).get('median_daily_rho')} "
            f"added_same={f1.get('added_same_direction')}; F2 added_same={f2.get('added_same_direction')}; "
            f"F3 added_same={f3.get('added_same_direction')}; F4 added_same={f4.get('added_same_direction')}"
        ),
        "10": (
            f"arrival thirds A pooled same-sign n: F1={f1.get('arrival_same_direction_n')} F2={f2.get('arrival_same_direction_n')} "
            f"F3={f3.get('arrival_same_direction_n')} F4={f4.get('arrival_same_direction_n')} "
            f"multi F1={f1.get('arrival_multi_same_direction')} F2={f2.get('arrival_multi_same_direction')} "
            f"F3={f3.get('arrival_multi_same_direction')} F4={f4.get('arrival_multi_same_direction')}"
        ),
        "11": "feature-specific day concentration: " + " | ".join(conc_lines),
        "12": "feature-specific symbol concentration included in 11 (pairwise Spearman numerator share).",
        "13": "median-split diagnostic only = true; QUALIFY/direction/verdict did not use median high/low split.",
        "14": f"qualified features = {dec.get('qualified_features')}",
        "15": f"selected feature = {dec.get('selected_feature')}",
        "16": f"PRIMARY_NEXT_MECHANISM = {dec.get('PRIMARY_NEXT_MECHANISM')}",
        "17": f"verdict = {dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "18": f"prior run superseded for decision = {bool(SUPERSEDED_FOR_DECISION)} reason={SUPERSEDED_REASON} prior={PRIOR_ANALYSIS_ID}/{PRIOR_VERDICT}",
        "19": "new ENTRY filter = false",
        "20": "candidate evaluated = false",
        "21": "future data used = false",
        "22": f"MAX_RESEARCH_DATE = {MAX_RESEARCH_DATE}",
        "23": "prospective harvest suspended = true",
        "24": "TRUE_OOS = false",
        "25": "CERTIFIED = false",
        "26": f"Capture reporting state = {cap.get('CAPTURE_STATE')} (null==null is not alive)",
        "27": f"submit/cancel/live = {report.get('submit_cancel_live')}",
        "28": f"next = {dec.get('NEXT')}",
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, v1_pre: dict[str, str]) -> int:
    decision = decide({"identity_ok": False, "features": [], "methodology_ok": False, "pool_identity": {"pool_identity_ok": False}}, leak_ok=False)
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
            "BURNED_EXISTING_DATA_ONLY": True,
            "NEXT_THRESHOLD_POLICY_FROZEN": THRESHOLD_POLICY,
            "MISSING_POLICY_FROZEN": MISSING_POLICY_FROZEN,
            "NEXT_CANDIDATE_ID_IF_CASE_A": NEXT_CANDIDATE_ID_IF_CASE_A,
            "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "SUPERSEDED_FOR_DECISION": True,
            "SUPERSEDED_REASON": SUPERSEDED_REASON,
        },
        "decision": decision,
        "evaluation": {},
        "leak": leak,
        "preflight": pre,
        "prior_run": {
            "ANALYSIS_ID": PRIOR_ANALYSIS_ID,
            "VERDICT": PRIOR_VERDICT,
            "SUPERSEDED_FOR_DECISION": True,
            "REASON": SUPERSEDED_REASON,
            "artifacts_preserved": v1_pre,
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
    sha = spec_sha256_mechanism()
    source_sha = source_sha256_mechanism()
    v1_pre = _v1_hashes()
    pre = snapshot(phase="PRE")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["FUTURE_DATA_N"] = int(bool(FUTURE_DATA_USED))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["CAPTURE_RESTREAM_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(
        f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY} MAX_RESEARCH_DATE={MAX_RESEARCH_DATE} "
        f"PROSPECTIVE_HARVEST_SUSPENDED=true PRIMARY=DAILY_SPEARMAN",
        flush=True,
    )
    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, v1_pre=v1_pre)
    if bool(TRUE_OOS) or bool(THRESHOLD_SEARCH) or (not PROSPECTIVE_HARVEST_SUSPENDED):
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, v1_pre=v1_pre)
    if any(v1_pre.get(name) == "MISSING" for name in V1_ARTIFACTS):
        return _stop("STOP. Prior V1 artifacts missing; do not rewrite.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, v1_pre=v1_pre)
    for day in all_research_days():
        bad = assert_research_day(day, today=TODAY)
        if bad:
            return _stop(f"STOP. {bad}:{day}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, v1_pre=v1_pre)
        if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE):
            leak["FUTURE_DATA_N"] = 1
            return _stop("STOP. Future/forbidden day in research list.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, v1_pre=v1_pre)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, v1_pre=v1_pre)
    parent = _load(PRECAP_TIMING_RCA_OUT / "report.json")
    parent_req = dict(parent.get("required") or {})
    parent_dec = dict(parent.get("decision") or {})
    parent_verdict = str(parent_req.get("VERDICT") or parent_dec.get("VERDICT") or "")
    if parent_verdict != PARENT_VERDICT:
        return _stop("STOP. Parent timing-RCA verdict mismatch.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, v1_pre=v1_pre)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [
            str(PRECAP_TIMING_RCA_OUT / "report.json"),
            str(PRECAP_EXISTING_MECHANISM_V1_OUT / "report.json"),
            str(PRECAP_EXISTING_MECHANISM_V1_R1_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]) or int(leak["INPUT_20260903_N"]) or int(leak["INPUT_20260904_N"]):
        return _stop("STOP. Active or forbidden capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, v1_pre=v1_pre)
    harvested = harvest_blocks(today=TODAY)
    if not harvested.get("ok"):
        return _stop(
            f"STOP. Harvest failed {harvested.get('date')}: {harvested.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            v1_pre=v1_pre,
        )
    leak["CAPTURE_RESTREAM_N"] = int(bool(harvested.get("capture_restreamed")))
    pack = evaluate(dict(harvested.get("bodies") or {}), harvested=harvested)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide(pack, leak_ok=leak_ok)
    post = snapshot(phase="POST")
    pid = advanced(pre, post)
    cap_state = capture_reporting_state(pre, post)
    v1_post = _v1_hashes()
    if v1_pre != v1_post:
        leak["V1_ARTIFACT_MUTATED_N"] = 1
        leak_ok = False
        decision = decide(pack, leak_ok=False)
        decision["integrity_reasons"] = list(decision.get("integrity_reasons") or []) + ["PRIOR_V1_ARTIFACT_MUTATED"]
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_mechanism_spec(),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "PRIMARY_METHOD": "CONTINUOUS_DAILY_SPEARMAN",
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "BURNED_EXISTING_DATA_ONLY": True,
            "NEXT_THRESHOLD_POLICY_FROZEN": THRESHOLD_POLICY,
            "MISSING_POLICY_FROZEN": MISSING_POLICY_FROZEN,
            "NEXT_CANDIDATE_ID_IF_CASE_A": decision.get("NEXT_CANDIDATE_ID_IF_CASE_A"),
            "NEXT_CANDIDATE_THRESHOLD_SEARCH_ALLOWED": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_FROZEN": False,
            "PROSPECTIVE_ARMED": False,
            "NEW_ENTRY_FILTER": False,
            "NEW_EXIT_RULE": False,
            "CAP_CHANGED": False,
            "INDEPENDENT_ALPHA_FAMILY": False,
            "selected_feature": decision.get("selected_feature"),
            "PRIMARY_NEXT_MECHANISM": decision.get("PRIMARY_NEXT_MECHANISM"),
            "SUPERSEDED_FOR_DECISION": True,
            "SUPERSEDED_REASON": SUPERSEDED_REASON,
            "CAPTURE_STATE": cap_state.get("CAPTURE_STATE"),
            "SECONDARY_DIAGNOSTIC_ONLY": True,
        },
        "decision": decision,
        "evaluation": pack,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "pid": pid,
        "reporting_semantics": reporting_semantics(pre, post),
        "capture_reporting_state": cap_state,
        "prior_run": {
            "ANALYSIS_ID": PRIOR_ANALYSIS_ID,
            "VERDICT": PRIOR_VERDICT,
            "SUPERSEDED_FOR_DECISION": True,
            "REASON": SUPERSEDED_REASON,
            "artifacts_preserved": v1_post,
            "artifacts_unchanged": v1_pre == v1_post,
        },
        "submit_cancel_live": [int(SUBMIT_N), int(CANCEL_N), int(LIVE_ORDER_N)],
        "answers": {},
        "_markdown": "",
    }
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    if _v1_hashes() != v1_pre:
        print("STOP. Prior V1 artifacts mutated.", flush=True)
        print("STOP.", flush=True)
        return 2
    print(
        f"CASE {decision.get('CASE')} {decision.get('VERDICT')} selected={decision.get('selected_feature')} "
        f"CAPTURE_STATE={cap_state.get('CAPTURE_STATE')} out={PRECAP_EXISTING_MECHANISM_V1_R1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if leak_ok and pack.get("identity_ok") and decision.get("CASE") != "D" else 2


if __name__ == "__main__":
    raise SystemExit(main())
