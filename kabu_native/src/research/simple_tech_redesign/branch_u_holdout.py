"""Offline SIMPLE_TECH Branch U temporal holdout V1. Frozen U. No Capture control. No Branch P."""
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
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import (
    ASK_FALLBACK_CHANGED,
    BRANCH_P_TECHNICAL_EXIT,
    CANONICAL_FRESHNESS_SEC,
    E4_CHANGED,
    E4_WAIT_BUDGET_SEC,
    ENTRY_CHANGED,
    LIFECYCLE_SPEC_SHA256_EXPECTED,
    LIFECYCLE_VERDICT_EXPECTED,
    PARENT_SPEC_SHA256_EXPECTED,
    SIZING_CHANGED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
)
from research.simple_tech_redesign.branch_u_causal_harvest import merge_causal_days, replay_causal_day
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED, BRANCH_U_ONE_SHOT_VERDICT_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_analyze import decide, summarize
from research.simple_tech_redesign.branch_u_holdout_harvest import (
    LOCKED_SERIES_DAYS,
    harvest_b1_day,
    harvest_branch_u_day,
    plan_append,
    provenance_row,
)
from research.simple_tech_redesign.branch_u_holdout_publish import REQUIRED_KEYS, build_markdown, build_sheets, write_artifacts
from research.simple_tech_redesign.branch_u_holdout_spec import (
    ANALYSIS_ID,
    BRANCH_P_ADDED,
    CAUSAL_SPEC_SHA256_EXPECTED,
    CAUSAL_VERDICT_EXPECTED,
    DATE_CHERRY_PICK,
    FORCE_TREATMENT_FILL_SET,
    HOLDOUT_CLASS,
    K_SEARCH,
    PNL_SELECTION,
    RESEARCH_PARALLELISM,
    RUNTIME_CANDIDATE,
    TIME_STOP_USED,
    TRUE_OOS,
    V1R_PROCESS_MARKET_PUSH_USED,
    canonical_holdout_spec,
    spec_sha256_causal,
    spec_sha256_holdout,
)
from research.simple_tech_redesign.exit_lifecycle_spec import (
    V29_SPEC_SHA256_EXPECTED,
    V29_VERDICT_EXPECTED,
    spec_sha256_lifecycle,
)
from research.simple_tech_redesign.isolation import (
    BRANCH_U_CAUSAL_OUT,
    BRANCH_U_HOLDOUT_OUT,
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
    "SIZING_CHANGE_N",
    "FORCE_TREATMENT_FILL_SET_N",
    "V1R_PROCESS_MARKET_PUSH_N",
    "DATE_CHERRY_PICK_N",
    "TODAY_INCLUDED_N",
    "OCCUPANCY_SOT_FAIL_N",
    "PRE_DIVERGENCE_FAIL_N",
    "LEFTOVER_FAIL_N",
    "V22_WRITE_N",
    "V26_WRITE_N",
    "V27_WRITE_N",
    "V28_WRITE_N",
    "V29_WRITE_N",
    "LIFECYCLE_WRITE_N",
    "BRANCH_U_WRITE_N",
    "BRANCH_U_CAUSAL_WRITE_N",
)

HOLDOUT_SPEC_SHA256_EXPECTED = "9499969eecda43cb7dbc5ef68a5e735cb6b30263b9ea170792afd984252ea94a"

FROZEN_OFFICIAL = (
    V26_OUT / "report.json",
    V27_OUT / "report.json",
    V28_OUT / "report.json",
    V29_OUT / "report.json",
    LIFECYCLE_OUT / "report.json",
    BRANCH_U_OUT / "report.json",
    BRANCH_U_CAUSAL_OUT / "report.json",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _mtimes(paths: tuple[Path, ...]) -> dict[str, float]:
    return {str(p): float(p.stat().st_mtime) for p in paths if p.is_file()}


def _capture_day_from_path(p: str) -> str:
    parts = str(p or "").replace("\\", "/").split("/")
    if "market_capture" in parts:
        i = parts.index("market_capture")
        if i + 1 < len(parts) and parts[i + 1].isdigit():
            return parts[i + 1]
    return ""


def _study(name: str, report: dict[str, Any]) -> dict[str, Any]:
    snap = set()
    for pack in (dict(report.get("preflight") or {}), dict(report.get("postflight") or {})):
        t = str(pack.get("today") or "")
        if t.isdigit():
            snap.add(t)
        d = _capture_day_from_path(str(pack.get("ACTIVE_CAPTURE_PATH") or ""))
        if d:
            snap.add(d)
    return {
        "name": name,
        "input_days": list(ELIGIBLE_DAYS),
        "if_used_reason": "research_input_day",
        "preflight_today_or_active_capture_days": sorted(snap),
    }


def _locked_prefix(prev: dict[str, Any]) -> tuple[str, ...]:
    days = [str(d) for d in list((prev.get("required") or {}).get("HOLDOUT_DAYS") or [])]
    if days:
        return tuple(days)
    return tuple(LOCKED_SERIES_DAYS)


def _merge_series_history(prev: dict[str, Any], rec: dict[str, Any]) -> list[dict[str, Any]]:
    hist = [dict(x) for x in list(prev.get("series_history") or [])]
    if not hist:
        req = dict(prev.get("required") or {})
        if req.get("HOLDOUT_DAYS"):
            attr = dict(req.get("ATTRIBUTION") or {})
            occ = dict(req.get("WINNER_SAFETY_OCCUPANCY") or {})
            hist.append(
                {
                    "version": "published_prefix",
                    "appended_days": [],
                    "holdout_days": list(req.get("HOLDOUT_DAYS") or []),
                    "branch_u_exit_n": occ.get("BRANCH_U_EXIT_N"),
                    "verdict": req.get("VERDICT"),
                    "DIRECT_EXIT_DELTA": attr.get("DIRECT_EXIT_DELTA"),
                    "SLOT_RELEASE_DOWNSTREAM_DELTA": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
                    "TOTAL_CAUSAL_DELTA": attr.get("TOTAL_CAUSAL_DELTA"),
                }
            )
    hist.append(rec)
    return hist


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_BRANCH_U_HOLDOUT_INVALID",
            "TRUE_OOS": False,
            "HOLDOUT_CLASS": HOLDOUT_CLASS,
            "ENTRY_CHANGED": False,
            "SIZING_CHANGED": False,
            "FORCE_TREATMENT_FILL_SET": False,
            "NEXT": msg,
            "OCCUPANCY_SOT_PARITY": False,
            "PRE_DIVERGENCE_PARITY": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "HOLDOUT_SPEC_SHA256": sha,
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
        "decision": {"CASE": "E", "VERDICT": "SIMPLE_TECH_BRANCH_U_HOLDOUT_INVALID", "NEXT": msg},
        "_markdown": build_markdown({"required": req, "decision": {"CASE": "E"}}),
    }
    write_artifacts(report, build_sheets(report, leak, {}))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_holdout_spec()
    sha = spec_sha256_holdout(spec)
    causal_sha = spec_sha256_causal()
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
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} holdout={sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if lifecycle_sha != LIFECYCLE_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Lifecycle spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if causal_sha != CAUSAL_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Causal spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if sha != HOLDOUT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Holdout spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
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
        or SIZING_CHANGED
        or PNL_SELECTION
        or TIME_STOP_USED
        or K_SEARCH
        or BRANCH_P_TECHNICAL_EXIT
        or BRANCH_P_ADDED
        or bool(TRUE_OOS)
        or RUNTIME_CANDIDATE
        or FORCE_TREATMENT_FILL_SET
        or V1R_PROCESS_MARKET_PUSH_USED
        or DATE_CHERRY_PICK
        or abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) > 1e-12
        or abs(float(E4_WAIT_BUDGET_SEC) - 5.0) > 1e-12
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
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
    u_req = dict(_load(BRANCH_U_OUT / "report.json").get("required") or {})
    if str(u_req.get("VERDICT") or "") != BRANCH_U_ONE_SHOT_VERDICT_EXPECTED:
        return _stop("STOP. Branch U one-shot official freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if str(u_req.get("BRANCH_U_SPEC_SHA256") or "") != BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Branch U one-shot spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    causal_req = dict(_load(BRANCH_U_CAUSAL_OUT / "report.json").get("required") or {})
    if str(causal_req.get("VERDICT") or "") != CAUSAL_VERDICT_EXPECTED:
        return _stop("STOP. Branch U causal official freeze failed.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if str(causal_req.get("CAUSAL_SPEC_SHA256") or "") != CAUSAL_SPEC_SHA256_EXPECTED:
        return _stop("STOP. Branch U causal spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)

    prev_report = _load(BRANCH_U_HOLDOUT_OUT / "report.json")
    locked = _locked_prefix(prev_report)
    if list(locked[: len(LOCKED_SERIES_DAYS)]) != list(LOCKED_SERIES_DAYS):
        leak["DATE_CHERRY_PICK_N"] = 1
        return _stop("STOP. Existing holdout series days were dropped.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    plan = plan_append(today=TODAY, locked=locked)
    discovered = dict(plan.get("discovery") or {})
    holdout_days = list(plan.get("holdout_days") or [])
    appended_days = list(plan.get("appended_days") or [])
    blocked = dict(plan.get("blocked") or {})
    print(
        f"HOLDOUT_DAYS={holdout_days} appended={appended_days} "
        f"blocked={(blocked.get('date'), blocked.get('reason'))} "
        f"excluded={[(e.get('date'), e.get('reason')) for e in discovered.get('excluded') or []]}",
        flush=True,
    )
    if list(plan.get("missing_locked") or []):
        return _stop("STOP. Locked holdout series days are not complete/usable.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if TODAY in set(holdout_days):
        leak["TODAY_INCLUDED_N"] = 1
        return _stop("STOP. Today included in holdout days.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if bool(plan.get("date_cherry_pick")) or bool(discovered.get("date_cherry_pick")):
        leak["DATE_CHERRY_PICK_N"] = 1
        return _stop("STOP. Date cherry-pick flag set.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    prev_days = [str(d) for d in list((prev_report.get("required") or {}).get("HOLDOUT_DAYS") or [])]
    if prev_days and holdout_days[: len(prev_days)] != prev_days:
        leak["DATE_CHERRY_PICK_N"] = 1
        return _stop("STOP. Prior published holdout days were dropped.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if not holdout_days:
        return _stop("STOP. No complete sealed holdout captures after development end.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)

    usable = list(plan.get("usable") or [])
    if [str(c.get("date") or "") for c in usable] != holdout_days:
        return _stop("STOP. Holdout usable captures do not match series days.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in usable]
        + [
            str(V22_OUT / "report.json"),
            str(V26_OUT / "report.json"),
            str(V27_OUT / "report.json"),
            str(V28_OUT / "report.json"),
            str(V29_OUT / "report.json"),
            str(LIFECYCLE_OUT / "report.json"),
            str(BRANCH_U_OUT / "report.json"),
            str(BRANCH_U_CAUSAL_OUT / "report.json"),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)

    studies = [
        _study("V26", _load(V26_OUT / "report.json")),
        _study("V27", _load(V27_OUT / "report.json")),
        _study("V28", _load(V28_OUT / "report.json")),
        _study("V29", _load(V29_OUT / "report.json")),
        _study("LIFECYCLE", _load(LIFECYCLE_OUT / "report.json")),
        _study("BRANCH_U_ONE_SHOT", _load(BRANCH_U_OUT / "report.json")),
        _study("BRANCH_U_CAUSAL", _load(BRANCH_U_CAUSAL_OUT / "report.json")),
    ]
    provenance = [provenance_row(d, studies=studies) for d in holdout_days]
    if any(bool(p.get("previously_used")) for p in provenance):
        return _stop("STOP. Holdout day was used as a research input for rule selection.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)

    rows: list[dict[str, Any]] = []
    day_bodies: list[dict[str, Any]] = []
    u_sha = BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
    for cap in usable:
        day = str(cap["date"])
        b1 = harvest_b1_day(cap, v1_sha=parent_sha)
        if not b1.get("ok"):
            return _stop(f"STOP. B1 harvest failed {day}: {b1.get('blocker')}.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
        sigs = list(b1.get("rows") or [])
        print(f"{day} b1_signals={len(sigs)} events={b1.get('events_n')}", flush=True)
        ubody = harvest_branch_u_day(cap, sigs, u_sha=u_sha)
        if not ubody.get("ok"):
            return _stop(f"STOP. Branch U harvest failed {day}: {ubody.get('blocker')}.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
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
            "BRANCH_U_WRITE_N",
            "BRANCH_U_CAUSAL_WRITE_N",
            "BOARD_EVENT_AFTER_EVENT_T_N",
            "DATE_CHERRY_PICK_N",
            "TODAY_INCLUDED_N",
        }
        for k, v in dict(ubody.get("leak") or {}).items():
            if not str(k).endswith("_N") or k in skip:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                leak[k] = int(leak.get(k) or 0) + int(v)
        day_rows = list(ubody.get("rows") or [])
        body = replay_causal_day(day_rows)
        body["date"] = day
        if not body.get("control_sot_ok") or not body.get("treatment_sot_ok"):
            leak["OCCUPANCY_SOT_FAIL_N"] = int(leak.get("OCCUPANCY_SOT_FAIL_N") or 0) + 1
        if not body.get("pre_divergence_ok"):
            leak["PRE_DIVERGENCE_FAIL_N"] = int(leak.get("PRE_DIVERGENCE_FAIL_N") or 0) + 1
        if not body.get("leftover_ok"):
            leak["LEFTOVER_FAIL_N"] = int(leak.get("LEFTOVER_FAIL_N") or 0) + 1
        print(
            f"{day} occupancy ctrl_fill={body['control']['fill_n']} treat_fill={body['treatment']['fill_n']} "
            f"u_exit={body['treatment']['branch_u_exit_n']}",
            flush=True,
        )
        rows.extend(day_rows)
        day_bodies.append(body)

    merged = merge_causal_days(day_bodies)
    occupancy_ok = bool(merged.get("occupancy_sot_ok"))
    pre_div_ok = bool(merged.get("pre_divergence_ok"))
    leftover_ok = bool(merged.get("leftover_ok"))
    if not occupancy_ok:
        leak["OCCUPANCY_SOT_FAIL_N"] = max(int(leak.get("OCCUPANCY_SOT_FAIL_N") or 0), 1)
    if not pre_div_ok:
        leak["PRE_DIVERGENCE_FAIL_N"] = max(int(leak.get("PRE_DIVERGENCE_FAIL_N") or 0), 1)
    if not leftover_ok:
        leak["LEFTOVER_FAIL_N"] = max(int(leak.get("LEFTOVER_FAIL_N") or 0), 1)

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
                elif "/branch_u_bb_lower_exit_one_shot" in pl:
                    leak["BRANCH_U_WRITE_N"] = int(leak.get("BRANCH_U_WRITE_N") or 0) + 1
                elif "/branch_u_full_causal_portfolio_replay" in pl:
                    leak["BRANCH_U_CAUSAL_WRITE_N"] = int(leak.get("BRANCH_U_CAUSAL_WRITE_N") or 0) + 1

    summary = summarize(
        rows,
        days=holdout_days,
        ctrl=dict(merged.get("control") or {}),
        treat=dict(merged.get("treatment") or {}),
        day_bodies=day_bodies,
        provenance=provenance,
        parity={
            "OCCUPANCY_SOT_PARITY": occupancy_ok,
            "PRE_DIVERGENCE_PARITY": pre_div_ok,
            "LEFTOVER_OK": leftover_ok,
            "FORCE_TREATMENT_FILL_SET": False,
            "V1R_PROCESS_MARKET_PUSH_USED": False,
            "DISCOVERY": {
                "scanned_days": discovered.get("scanned_days"),
                "excluded": discovered.get("excluded"),
                "appended_days": appended_days,
                "locked_days": list(locked),
                "blocked": blocked,
                "later_capture_days": list(plan.get("later_capture_days") or []),
            },
        },
    )
    attr_sum = dict(summary.get("ATTRIBUTION") or {})
    daily_rows = list((summary.get("DAY_ROBUSTNESS") or {}).get("daily") or [])
    sum_direct = sum(float(r.get("direct_exit_delta") or 0.0) for r in daily_rows)
    sum_down = sum(float(r.get("downstream_delta") or 0.0) for r in daily_rows)
    sum_disp = sum(float(r.get("displaced_delta") or 0.0) for r in daily_rows)
    sum_delta = sum(float(r.get("delta_pnl") or 0.0) for r in daily_rows)
    if abs(sum_direct - float(attr_sum.get("DIRECT_EXIT_DELTA") or 0.0)) > 0.01:
        return _stop("STOP. Daily DIRECT_EXIT_DELTA does not sum to cumulative.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if abs(sum_down - float(attr_sum.get("SLOT_RELEASE_DOWNSTREAM_DELTA") or 0.0)) > 0.01:
        return _stop("STOP. Daily SLOT_RELEASE_DOWNSTREAM_DELTA does not sum to cumulative.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if abs(sum_disp - float(attr_sum.get("DISPLACED_TRADE_DELTA") or 0.0)) > 0.01:
        return _stop("STOP. Daily DISPLACED_TRADE_DELTA does not sum to cumulative.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    if abs(sum_delta - float(attr_sum.get("TOTAL_CAUSAL_DELTA") or 0.0)) > 0.01:
        return _stop("STOP. Daily delta_pnl does not sum to TOTAL_CAUSAL_DELTA.", pre=pre, leak=leak, parent_sha=parent_sha, sha=sha)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED") and reporting.get("REPORTING_SEMANTICS_PASS"))
    cherry_ok = not bool(plan.get("date_cherry_pick"))
    decision = decide(
        summary,
        leak_ok=leak_ok,
        ni_ok=ni_ok,
        occupancy_ok=occupancy_ok,
        pre_div_ok=pre_div_ok,
        leftover_ok=leftover_ok,
        cherry_ok=cherry_ok,
        today_excluded=TODAY not in set(holdout_days),
    )
    occ = dict(summary.get("WINNER_SAFETY_OCCUPANCY") or {})
    continuation = {
        "as_of_today": TODAY,
        "appended_days": appended_days,
        "holdout_days": holdout_days,
        "branch_u_exit_n": occ.get("BRANCH_U_EXIT_N"),
        "verdict": decision.get("VERDICT"),
        "DIRECT_EXIT_DELTA": attr_sum.get("DIRECT_EXIT_DELTA"),
        "SLOT_RELEASE_DOWNSTREAM_DELTA": attr_sum.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
        "TOTAL_CAUSAL_DELTA": attr_sum.get("TOTAL_CAUSAL_DELTA"),
        "next_capture_candidate": blocked.get("date"),
        "next_capture_status": blocked.get("reason"),
        "date_cherry_pick": False,
    }
    series_history = _merge_series_history(prev_report, continuation)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "HOLDOUT_DAYS": holdout_days,
        "HOLDOUT_DAY_N": len(holdout_days),
        "APPENDED_DAYS": appended_days,
        "LOCKED_SERIES_DAYS": list(locked),
        "NEXT_CAPTURE_CANDIDATE": blocked.get("date"),
        "NEXT_CAPTURE_STATUS": blocked.get("reason"),
        "DATE_CHERRY_PICK": False,
        "SIGNAL_N": summary.get("SIGNAL_N"),
        "EXECUTION_EVALUABLE_N": summary.get("EXECUTION_EVALUABLE_N"),
        "UNCONSTRAINED_FILL_N": summary.get("UNCONSTRAINED_FILL_N"),
        "TESTED_BRANCH_U_EXIT_MECHANISMS": summary.get("TESTED_BRANCH_U_EXIT_MECHANISMS"),
        "CONTROL_COUNTS": summary.get("CONTROL_COUNTS"),
        "TREATMENT_COUNTS": summary.get("TREATMENT_COUNTS"),
        "incremental_trade_n": summary.get("incremental_trade_n"),
        "control_only_trade_n": summary.get("control_only_trade_n"),
        "CONTROL_ECONOMICS": summary.get("CONTROL_ECONOMICS"),
        "TREATMENT_ECONOMICS": summary.get("TREATMENT_ECONOMICS"),
        "ATTRIBUTION": summary.get("ATTRIBUTION"),
        "WINNER_SAFETY_OCCUPANCY": occ,
        "WINNER_SAFETY_UNCONSTRAINED": summary.get("WINNER_SAFETY_UNCONSTRAINED"),
        "GOOD_BRANCH_U_EXIT_N": occ.get("GOOD_BRANCH_U_EXIT_N"),
        "DIP_BRANCH_U_EXIT_N": occ.get("DIP_BRANCH_U_EXIT_N"),
        "DAY_ROBUSTNESS": summary.get("DAY_ROBUSTNESS"),
        "PROVENANCE": provenance,
        "DELTA_TOTAL_PNL": decision.get("DELTA_TOTAL_PNL"),
        "DELTA_PF": decision.get("DELTA_PF"),
        "DELTA_MAX_DD": decision.get("DELTA_MAX_DD"),
        "OCCUPANCY_SOT_PARITY": occupancy_ok,
        "PRE_DIVERGENCE_PARITY": pre_div_ok,
        "FORCE_TREATMENT_FILL_SET": False,
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "HOLDOUT_CLASS": HOLDOUT_CLASS,
        "CERTIFIED": False,
        "BRANCH_P_ADDED": False,
        "V1R_PROCESS_MARKET_PUSH_USED": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "CAUSAL_SPEC_SHA256": CAUSAL_SPEC_SHA256_EXPECTED,
        "BRANCH_U_ONE_SHOT_SPEC_SHA256": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
        "HOLDOUT_SPEC_SHA256": sha,
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
        "discovery": discovered,
        "append": continuation,
        "series_history": series_history,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report, leak, reporting))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} DAYS={holdout_days} "
        f"U_EXIT={occ.get('BRANCH_U_EXIT_N')} DELTA_PNL={summary.get('DELTA_TOTAL_PNL')} "
        f"VERDICT={decision.get('VERDICT')} ni={ni_ok} out={BRANCH_U_HOLDOUT_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    ok = decision.get("CASE") in {"A", "C", "D"} and ni_ok and occupancy_ok and pre_div_ok
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
