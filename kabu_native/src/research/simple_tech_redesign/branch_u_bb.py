"""Offline SIMPLE_TECH Branch U one-shot U_BB_LOWER_BREAK EXIT. No Capture control. No Branch P."""
from __future__ import annotations

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
from research.simple_tech_entry_family.harvest import sealed_day_caps
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics, set_hash, signal_tuples
from research.simple_tech_redesign.branch_u_bb_analyze import decide, summarize
from research.simple_tech_redesign.branch_u_bb_harvest import (
    BRANCH_U_CACHE,
    load_branch_u_day_cache,
    replay_branch_u_day,
    save_branch_u_day_cache,
)
from research.simple_tech_redesign.branch_u_bb_publish import REQUIRED_KEYS, build_markdown, build_sheets, write_artifacts
from research.simple_tech_redesign.branch_u_bb_spec import (
    ANALYSIS_ID,
    ASK_FALLBACK_CHANGED,
    B1_SIGNAL_N_EXPECTED,
    BPS_THRESHOLD_SEARCH,
    BRANCH_P_TECHNICAL_EXIT,
    CANONICAL_FRESHNESS_SEC,
    COMBINATION_SEARCH,
    E4_CHANGED,
    E4_WAIT_BUDGET_SEC,
    ENTRY_CHANGED,
    FRESHNESS_THRESHOLD_CHANGED,
    GIVEBACK_SEARCH,
    K_SEARCH,
    LIFECYCLE_SPEC_SHA256_EXPECTED,
    LIFECYCLE_VERDICT_EXPECTED,
    PARENT_SPEC_SHA256_EXPECTED,
    PNL_SELECTION,
    PRIMITIVE_CHANGED,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    RESEARCH_PARALLELISM,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    SIZING_CHANGED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
    THRESHOLD_SEARCH,
    TIME_STOP_USED,
    TRUE_OOS,
    canonical_branch_u_spec,
    spec_sha256_branch_u,
)
from research.simple_tech_redesign.exit_lifecycle_spec import (
    V29_SPEC_SHA256_EXPECTED,
    V29_VERDICT_EXPECTED,
    spec_sha256_lifecycle,
)
from research.simple_tech_redesign.isolation import (
    BRANCH_U_OUT,
    LIFECYCLE_OUT,
    TODAY,
    V22_OUT,
    V26_OUT,
    V27_OUT,
    V28_OUT,
    V29_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.v24_harvest import V22_CACHE, load_v22_day_rows
from research.simple_tech_redesign.v27_analyze import research_fill_tuples
from research.simple_tech_redesign.v29_spec import (
    V26_SPEC_SHA256_FROZEN,
    V26_VERDICT_FROZEN,
    V27_PRIMARY_EXPECTED,
    V27_SPEC_SHA256_FROZEN,
    V27_VERDICT_FROZEN,
    V28_SPEC_SHA256_EXPECTED,
    V28_VERDICT_EXPECTED,
)

INTEGRITY_ZERO = (
    "LIVE_PROCESS_CONTROL_CALL_N",
    "RUNTIME_WRITE_N",
    "CAPTURE_WRITE_N",
    "ADDITIONAL_WEBSOCKET_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "KABUS_RESTART_N",
    "CAPTURE_RESTART_N",
    "RUNTIME_RESTART_N",
    "VIRTUAL_FILL_N",
    "FUTURE_BOARD_CARRYBACK_N",
    "FUTURE_QUOTE_CARRYBACK_N",
    "QUEUE_ASSUMPTION_N",
    "OPTIMISTIC_TOUCH_N",
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
    "ENTRY_RULE_CHANGE_N",
    "E4_CHANGE_N",
    "FRESHNESS_CHANGE_N",
    "THRESHOLD_SEARCH_N",
    "K_SEARCH_N",
    "BAR_COUNT_SEARCH_N",
    "COMBINATION_SEARCH_N",
    "PNL_SELECTION_N",
    "GIVEBACK_SEARCH_N",
    "BPS_THRESHOLD_SEARCH_N",
    "WAIT_EXTENSION_N",
    "REPRICE_N",
    "CHASE_N",
    "SECOND_FALLBACK_N",
    "FALLBACK_MARKET_N",
    "TIME_STOP_N",
    "EXIT_COMBINATION_N",
    "MIXED_TF_RULE_N",
    "PRE_DECISION_EXIT_N",
    "PRE_BAR_EXIT_N",
    "LAST_QUOTE_CARRYBACK_TECH_N",
    "VWAP_N",
    "EXIT_PRICE_OPT_N",
    "DELAY_OPT_N",
    "RCI_EXIT_N",
    "VOLUME_EXIT_N",
    "MFE_EXIT_N",
    "ALTERNATE_MECHANISM_N",
    "BRANCH_P_TECH_EXIT_N",
    "U_EXIT_AFTER_PROVEN_N",
    "BB_DEF_DRIFT_N",
    "BB_PARAM_CHANGE_N",
    "V22_WRITE_N",
    "V26_WRITE_N",
    "V27_WRITE_N",
    "V28_WRITE_N",
    "V29_WRITE_N",
    "LIFECYCLE_WRITE_N",
)

FROZEN_OFFICIAL = (
    V26_OUT / "report.json",
    V27_OUT / "report.json",
    V28_OUT / "report.json",
    V29_OUT / "report.json",
    LIFECYCLE_OUT / "report.json",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _mtimes(paths: tuple[Path, ...]) -> dict[str, float]:
    return {str(p): float(p.stat().st_mtime) for p in paths if p.is_file()}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_BRANCH_U_EXIT_INVALID",
            "TRUE_OOS": False,
            "ENTRY_CHANGED": False,
            "SIZING_CHANGED": False,
            "NEXT": msg,
            "V27_FILL_IDENTITY_PARITY": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "BRANCH_U_SPEC_SHA256": sha,
            "TESTED_BRANCH_U_EXIT_MECHANISMS": list(TESTED_BRANCH_U_EXIT_MECHANISMS),
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "summary": {},
        "decision": {"CASE": "E", "VERDICT": "SIMPLE_TECH_BRANCH_U_EXIT_INVALID", "NEXT": msg},
        "_markdown": build_markdown({"required": req, "decision": {"CASE": "E"}}),
    }
    write_artifacts(report, build_sheets(report, leak, {}))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_branch_u_spec()
    sha = spec_sha256_branch_u(spec)
    lifecycle_sha = spec_sha256_lifecycle()
    chk = self_check()
    pre = snapshot(phase="PRE")
    frozen_mtime = _mtimes(FROZEN_OFFICIAL)
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} branch_u={sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if lifecycle_sha != LIFECYCLE_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Lifecycle spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if list(TESTED_BRANCH_U_EXIT_MECHANISMS) != ["U_BB_LOWER_BREAK"]:
        return _stop("STOP. TESTED_BRANCH_U_EXIT_MECHANISMS invalid.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if (
        ENTRY_CHANGED
        or E4_CHANGED
        or ASK_FALLBACK_CHANGED
        or FRESHNESS_THRESHOLD_CHANGED
        or PRIMITIVE_CHANGED
        or SIZING_CHANGED
        or PNL_SELECTION
        or TIME_STOP_USED
        or K_SEARCH
        or COMBINATION_SEARCH
        or THRESHOLD_SEARCH
        or GIVEBACK_SEARCH
        or BPS_THRESHOLD_SEARCH
        or BRANCH_P_TECHNICAL_EXIT
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
        or abs(float(E4_WAIT_BUDGET_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)

    v26_req = dict(_load(V26_OUT / "report.json").get("required") or {})
    if str(v26_req.get("VERDICT") or "") != V26_VERDICT_FROZEN or str(v26_req.get("V26_SPEC_SHA256") or "") != V26_SPEC_SHA256_FROZEN:
        return _stop("STOP. V26 official freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    v27_req = dict(_load(V27_OUT / "report.json").get("required") or {})
    if str(v27_req.get("VERDICT") or "") != V27_VERDICT_FROZEN or str(v27_req.get("V27_SPEC_SHA256") or "") != V27_SPEC_SHA256_FROZEN:
        return _stop("STOP. V27 official freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    prim = dict(v27_req.get("PRIMARY_EXIT_STATE_MECHANISM") or {})
    if str(prim.get("name") or "") != V27_PRIMARY_EXPECTED:
        return _stop("STOP. V27 primary mechanism freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    v28_req = dict(_load(V28_OUT / "report.json").get("required") or {})
    if str(v28_req.get("VERDICT") or "") != V28_VERDICT_EXPECTED or str(v28_req.get("V28_SPEC_SHA256") or "") != V28_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V28 official freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    v29_req = dict(_load(V29_OUT / "report.json").get("required") or {})
    if str(v29_req.get("VERDICT") or "") != V29_VERDICT_EXPECTED or str(v29_req.get("V29_SPEC_SHA256") or "") != V29_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V29 official freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    life_req = dict(_load(LIFECYCLE_OUT / "report.json").get("required") or {})
    if str(life_req.get("VERDICT") or "") != LIFECYCLE_VERDICT_EXPECTED or str(life_req.get("LIFECYCLE_SPEC_SHA256") or "") != LIFECYCLE_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Lifecycle official freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [
            str(V22_CACHE),
            str(V22_OUT / "report.json"),
            str(V26_OUT / "report.json"),
            str(V27_OUT / "report.json"),
            str(V28_OUT / "report.json"),
            str(V29_OUT / "report.json"),
            str(LIFECYCLE_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)

    v22_by_day: dict[str, list[dict[str, Any]]] = {}
    v22_n = 0
    for cap in caps:
        day = str(cap["date"])
        day_rows = load_v22_day_rows(day)
        if not day_rows:
            return _stop(f"STOP. V22 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
        v22_by_day[day] = day_rows
        v22_n += len(day_rows)
    if v22_n != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. V22 signal n={v22_n} expected {B1_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)

    def _absorb(body: dict[str, Any]) -> None:
        skip = {
            "ACTIVE_CAPTURE_INPUT_N",
            "RESEARCH_INPUT_ACTIVE_FILE_N",
            "LIVE_PROCESS_CONTROL_CALL_N",
            "RUNTIME_WRITE_N",
            "CAPTURE_WRITE_N",
            "ADDITIONAL_WEBSOCKET_N",
            "SUBMIT_N",
            "CANCEL_N",
            "LIVE_ORDER_N",
            "KABUS_RESTART_N",
            "CAPTURE_RESTART_N",
            "RUNTIME_RESTART_N",
            "V22_WRITE_N",
            "V26_WRITE_N",
            "V27_WRITE_N",
            "V28_WRITE_N",
            "V29_WRITE_N",
            "LIFECYCLE_WRITE_N",
            "BOARD_EVENT_AFTER_EVENT_T_N",
        }
        for k, v in dict(body.get("leak") or {}).items():
            if not str(k).endswith("_N") or k in skip:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                leak[k] = int(leak.get(k) or 0) + int(v)
        leak["BOARD_EVENT_AFTER_EVENT_T_N"] = int(leak.get("BOARD_EVENT_AFTER_EVENT_T_N") or 0) + int(
            (body.get("leak") or {}).get("BOARD_EVENT_AFTER_EVENT_T_N") or 0
        )

    rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_sigs = v22_by_day[day]
        cache_path = BRANCH_U_CACHE / f"day_{day}.json"
        cached = load_branch_u_day_cache(cache_path, sha)
        if cached and int(cached.get("signal_n") or 0) == len(day_sigs) and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} branch-u cache hit rows={len(day_sigs)}", flush=True)
            rows.extend(list(cached.get("rows") or []))
            _absorb(cached)
            continue
        body = replay_branch_u_day({"date": day, "capture_path": cap.get("capture_path"), "signals": day_sigs, "spec_sha": sha})
        if not body.get("ok"):
            return _stop(f"STOP. Branch U replay failed {day}: {body.get('blocker')}.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
        save_branch_u_day_cache(cache_path, body)
        rows.extend(list(body.get("rows") or []))
        _absorb(body)

    post_frozen = _mtimes(FROZEN_OFFICIAL)
    if post_frozen != frozen_mtime:
        for p, mt in post_frozen.items():
            if frozen_mtime.get(p) != mt:
                pl = p.replace("\\", "/")
                if "/v26_" in pl:
                    leak["V26_WRITE_N"] = int(leak.get("V26_WRITE_N") or 0) + 1
                elif "/v27_" in pl:
                    leak["V27_WRITE_N"] = int(leak.get("V27_WRITE_N") or 0) + 1
                elif "/v28_" in pl:
                    leak["V28_WRITE_N"] = int(leak.get("V28_WRITE_N") or 0) + 1
                elif "/v29_" in pl:
                    leak["V29_WRITE_N"] = int(leak.get("V29_WRITE_N") or 0) + 1
                elif "/exit_lifecycle_branch_rca" in pl:
                    leak["LIFECYCLE_WRITE_N"] = int(leak.get("LIFECYCLE_WRITE_N") or 0) + 1

    sig_hash = set_hash(signal_tuples(rows))
    signal_parity = sig_hash == SIGNAL_SET_HASH_EXPECTED and len(rows) == int(B1_SIGNAL_N_EXPECTED)
    fill_identity = set_hash(research_fill_tuples(rows)) == RESEARCH_FILL_SET_HASH_EXPECTED
    summary = summarize(rows)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED") and reporting.get("REPORTING_SEMANTICS_PASS"))
    exit_miss_ok = (
        int((summary.get("OVERALL_CONTROL_ECONOMICS") or {}).get("EXIT_MISS_N") or 0) == 0
        and int((summary.get("OVERALL_TREATMENT_ECONOMICS") or {}).get("EXIT_MISS_N") or 0) == 0
    )
    decision = decide(
        summary,
        signal_parity=signal_parity,
        fill_identity=fill_identity,
        leak_ok=leak_ok,
        ni_ok=ni_ok,
        exit_miss_ok=exit_miss_ok,
    )
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "V27_FILL_IDENTITY_PARITY": decision.get("V27_FILL_IDENTITY_PARITY"),
        "SIGNAL_N": summary.get("SIGNAL_N"),
        "EXECUTION_EVALUABLE_N": summary.get("EXECUTION_EVALUABLE_N"),
        "CORE_FILL_N": summary.get("CORE_FILL_N"),
        "ADDED_FILL_N": summary.get("ADDED_FILL_N"),
        "TOTAL_RESEARCH_FILL_N": summary.get("TOTAL_RESEARCH_FILL_N"),
        "TESTED_BRANCH_U_EXIT_MECHANISMS": summary.get("TESTED_BRANCH_U_EXIT_MECHANISMS"),
        "CONTROL_UNIQUE": summary.get("CONTROL_UNIQUE"),
        "BRANCH_U_EXIT_N": summary.get("BRANCH_U_EXIT_N"),
        "SESSION_CLOSE_EXIT_N": summary.get("SESSION_CLOSE_EXIT_N"),
        "BRANCH_U_CORE_EXIT_N": summary.get("BRANCH_U_CORE_EXIT_N"),
        "BRANCH_U_ADDED_EXIT_N": summary.get("BRANCH_U_ADDED_EXIT_N"),
        "DIP_BRANCH_U_EXIT_N": summary.get("DIP_BRANCH_U_EXIT_N"),
        "GOOD_BRANCH_U_EXIT_N": summary.get("GOOD_BRANCH_U_EXIT_N"),
        "EARLY_BRANCH_U_EXIT_N": summary.get("EARLY_BRANCH_U_EXIT_N"),
        "PATH_ECONOMICS": summary.get("PATH_ECONOMICS"),
        "OVERALL_CONTROL_ECONOMICS": summary.get("OVERALL_CONTROL_ECONOMICS"),
        "OVERALL_TREATMENT_ECONOMICS": summary.get("OVERALL_TREATMENT_ECONOMICS"),
        "CORE_CONTROL_ECONOMICS": summary.get("CORE_CONTROL_ECONOMICS"),
        "CORE_TREATMENT_ECONOMICS": summary.get("CORE_TREATMENT_ECONOMICS"),
        "ADDED_CONTROL_ECONOMICS": summary.get("ADDED_CONTROL_ECONOMICS"),
        "ADDED_TREATMENT_ECONOMICS": summary.get("ADDED_TREATMENT_ECONOMICS"),
        "DELTA_TOTAL_PNL": decision.get("DELTA_TOTAL_PNL"),
        "DELTA_PF": decision.get("DELTA_PF"),
        "DELTA_MAX_DD": decision.get("DELTA_MAX_DD"),
        "DELTA_EX_BEST_DAY": decision.get("DELTA_EX_BEST_DAY"),
        "DELTA_EX_TOP3_DAY": decision.get("DELTA_EX_TOP3_DAY"),
        "DELTA_DROP_TOP_SYMBOL": decision.get("DELTA_DROP_TOP_SYMBOL"),
        "IMPROVE_DAY_N": decision.get("IMPROVE_DAY_N"),
        "WORSEN_DAY_N": decision.get("WORSEN_DAY_N"),
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "BRANCH_P_ADDED": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "SIGNAL_SET_HASH": sig_hash,
        "CORRECTED_FILL_HASH": summary.get("CORRECTED_FILL_HASH"),
        "RESEARCH_FILL_SET_HASH": summary.get("RESEARCH_FILL_SET_HASH"),
        "V26_VERDICT_FROZEN": V26_VERDICT_FROZEN,
        "V27_VERDICT_FROZEN": V27_VERDICT_FROZEN,
        "V28_VERDICT_FROZEN": V28_VERDICT_EXPECTED,
        "V29_VERDICT_FROZEN": V29_VERDICT_EXPECTED,
        "LIFECYCLE_VERDICT_FROZEN": LIFECYCLE_VERDICT_EXPECTED,
        "V29_SPEC_SHA256": V29_SPEC_SHA256_EXPECTED,
        "LIFECYCLE_SPEC_SHA256": LIFECYCLE_SPEC_SHA256_EXPECTED,
        "BRANCH_U_SPEC_SHA256": sha,
        "PARENT_SPEC_SHA256": parent_sha,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "decision": decision,
        "summary": summary,
        "preflight": pre,
        "postflight": post,
        "reporting": reporting,
        "leak": leak,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report, leak, reporting))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} FILLS={summary.get('TOTAL_RESEARCH_FILL_N')} "
        f"U_EXIT={summary.get('BRANCH_U_EXIT_N')} DIP_U={summary.get('DIP_BRANCH_U_EXIT_N')} "
        f"DELTA_PNL={summary.get('DELTA_TOTAL_PNL')} VERDICT={decision.get('VERDICT')} "
        f"ni={ni_ok} out={BRANCH_U_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    ok = decision.get("CASE") in {"A", "B", "C"} and ni_ok and signal_parity and fill_identity
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
