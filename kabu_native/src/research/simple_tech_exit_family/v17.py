"""Offline SIMPLE_TECH V17 G3 two-bar BE-reloss. Frozen V13 entry. Last BE-family test. No C14."""
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

from research.am_entry_profit_improvement import (
    CANCEL_N,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    PAPER_OPERATED,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
)
from research.simple_tech_entry_family.harvest import load_day_cache, sealed_day_caps
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.isolation import V8_OUT, V10_OUT, V13_OUT
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v8_harvest import V8_CACHE
from research.simple_tech_entry_family.v9_harvest import attach_bits
from research.simple_tech_entry_family.v11_harvest import is_b1
from research.simple_tech_entry_family.v12_harvest import V12_CACHE
from research.simple_tech_entry_family.v13_analyze import (
    eligible_tuples,
    fill_tuples,
    reporting_semantics,
    set_hash,
    signal_tuples,
)
from research.simple_tech_exit_family.isolation import (
    TODAY,
    V14_OUT,
    V15_OUT,
    V16_OUT,
    V17_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_exit_family.v14_analyze import entry_stack_ok
from research.simple_tech_exit_family.v14_harvest import V14_CACHE, _e4, load_v14_day_cache
from research.simple_tech_exit_family.v16_analyze import v14_p3_ok, v14_taxonomy_ok
from research.simple_tech_exit_family.v16_harvest import V16_CACHE, load_v16_day_cache
from research.simple_tech_exit_family.v17_analyze import (
    arm_parity_n,
    decision_case,
    g2_compare,
    g2_event_parity_n,
    g3_pack,
    g3_summary_row,
    hold_benchmark,
    required_g3_block,
    taxonomy,
    v14_p1_ok,
)
from research.simple_tech_exit_family.v17_harvest import (
    V17_CACHE,
    load_v17_day_cache,
    replay_v17_day,
    save_v17_day_cache,
)
from research.simple_tech_exit_family.v17_publish import REQUIRED_KEYS, build_markdown, flatten_trade, kv_rows, write_artifacts
from research.simple_tech_exit_family.v17_spec import (
    ANALYSIS_ID,
    B1_EXECUTABLE_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    BB_CHANGED,
    BOARD_RESTORED,
    C14_USED,
    DEVELOPMENT_CHALLENGER,
    DEVELOPMENT_ENTRY_STACK,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    E4_UNFILLED_N_EXPECTED,
    ELIGIBLE_SET_HASH_EXPECTED,
    EMA_CHANGED,
    ENTRY_CERTIFIED,
    ENTRY_RULE_CHANGED,
    EXIT_CERTIFIED,
    EXIT_SPEC_FROZEN,
    FOUR_BARS_CONFIRMATION,
    GRID_SEARCH,
    MFE10_ARMED,
    ML_USED,
    NEW_INDICATOR,
    PARENT_SPEC_SHA256_EXPECTED,
    PA_RESTORED,
    PLUS_1BPS_ARMED,
    PLUS_3BPS_ARMED,
    PLUS_5BPS_ARMED,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    THREE_BARS_CONFIRMATION,
    THRESHOLD_SEARCH,
    TRAIL_50,
    TRAIL_80,
    TRUE_OOS,
    UNFILLED_VIRTUAL_POSITION,
    V8_SPEC_SHA256_EXPECTED,
    V10_SPEC_SHA256_EXPECTED,
    V12_SPEC_SHA256_EXPECTED,
    V13_SPEC_SHA256_EXPECTED,
    V14_SPEC_SHA256_EXPECTED,
    V14_VERDICT_EXPECTED,
    V15_SPEC_SHA256_EXPECTED,
    V15_VERDICT_EXPECTED,
    V16_SPEC_SHA256_EXPECTED,
    V16_VERDICT_EXPECTED,
    VOLUME_RESTORED,
    canonical_v17_spec,
    spec_sha256_v17,
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
    "C14_REPLAY_N",
    "EXIT_POLICY_N",
    "THRESHOLD_SEARCH_N",
    "PLUS_BPS_ARMED_N",
    "TRAIL_SEARCH_N",
    "THREE_BARS_N",
    "FOUR_BARS_N",
    "UNFILLED_VIRTUAL_N",
    "PRE_FILL_EVENT_N",
    "FUTURE_QUOTE_N",
    "ENTRY_RULE_CHANGE_N",
    "EMA_CHANGE_N",
    "BB_CHANGE_N",
    "RCI_CHANGE_N",
    "GRID_SEARCH_N",
    "ML_USE_N",
    "NEW_INDICATOR_N",
    "V13_WRITE_N",
    "V14_WRITE_N",
    "V15_WRITE_N",
    "V16_WRITE_N",
    "G3_ARM_MISMATCH_N",
    "G2_EVENT_MISMATCH_N",
    "BAR_INTEG_FAIL_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v17_sha: str, extra: dict[str, Any] | None = None) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V17_INVALID",
            "NEXT": msg,
            "TRUE_OOS": False,
            "ENTRY_CERTIFIED": False,
            "EXIT_SPEC_FROZEN": False,
            "BE_RELOSS_FAMILY_CLOSED": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V17_SPEC_SHA256": v17_sha,
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "extra": extra or {},
        "_markdown": build_markdown({"required": req}),
    }
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v17_spec()
    v17_sha = spec_sha256_v17(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} v17={v17_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or EXIT_SPEC_FROZEN
        or EXIT_CERTIFIED
        or ENTRY_CERTIFIED
        or RUNTIME_CANDIDATE
        or ENTRY_RULE_CHANGED
        or EMA_CHANGED
        or BB_CHANGED
        or RCI_CHANGED
        or PA_RESTORED
        or VOLUME_RESTORED
        or BOARD_RESTORED
        or THRESHOLD_SEARCH
        or GRID_SEARCH
        or ML_USED
        or C14_USED
        or NEW_INDICATOR
        or UNFILLED_VIRTUAL_POSITION
        or PLUS_1BPS_ARMED
        or PLUS_3BPS_ARMED
        or PLUS_5BPS_ARMED
        or MFE10_ARMED
        or TRAIL_50
        or TRAIL_80
        or THREE_BARS_CONFIRMATION
        or FOUR_BARS_CONFIRMATION
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)

    if str((_load(V8_OUT / "report.json").get("required") or {}).get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    if str((_load(V10_OUT / "report.json").get("required") or {}).get("V10_SPEC_SHA256") or "") != V10_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V10 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    v13_req = dict((_load(V13_OUT / "report.json").get("required") or {}))
    if str(v13_req.get("V13_SPEC_SHA256") or "") != V13_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V13 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    stack_ok = entry_stack_ok(v13_req)
    if not stack_ok:
        return _stop("STOP. V13 development freeze stack mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    v14_req = dict((_load(V14_OUT / "report.json").get("required") or {}))
    if str(v14_req.get("V14_SPEC_SHA256") or "") != V14_SPEC_SHA256_EXPECTED or str(v14_req.get("VERDICT") or "") != V14_VERDICT_EXPECTED:
        return _stop("STOP. V14 official mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    if str(v14_req.get("PRIMARY_EXIT_PROBLEM") or "") != "PROFIT_GIVEBACK":
        return _stop("STOP. V14 primary problem is not PROFIT_GIVEBACK.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    v15_req = dict((_load(V15_OUT / "report.json").get("required") or {}))
    if str(v15_req.get("V15_SPEC_SHA256") or "") != V15_SPEC_SHA256_EXPECTED or str(v15_req.get("VERDICT") or "") != V15_VERDICT_EXPECTED:
        return _stop("STOP. V15 official mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    v16_req = dict((_load(V16_OUT / "report.json").get("required") or {}))
    if str(v16_req.get("V16_SPEC_SHA256") or "") != V16_SPEC_SHA256_EXPECTED or str(v16_req.get("VERDICT") or "") != V16_VERDICT_EXPECTED:
        return _stop("STOP. V16 official mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [str(V12_CACHE), str(V13_OUT / "report.json"), str(V14_OUT / "report.json"), str(V15_OUT / "report.json"), str(V16_OUT / "report.json"), str(V14_CACHE), str(V16_CACHE)],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)

    v8_sigs: list[dict[str, Any]] = []
    v12_rows: list[dict[str, Any]] = []
    v12_by_day: dict[str, list[dict[str, Any]]] = {}
    v14_rows: list[dict[str, Any]] = []
    v16_rows: list[dict[str, Any]] = []
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            if is_b1(rec):
                v8_sigs.append(rec)
        v12_day = load_day_cache(V12_CACHE / f"day_{day}.json", V12_SPEC_SHA256_EXPECTED)
        if not v12_day:
            return _stop(f"STOP. V12 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
        day_rows = list(v12_day.get("rows") or [])
        v12_by_day[day] = day_rows
        v12_rows.extend(day_rows)
        v14_day = load_v14_day_cache(V14_CACHE / f"day_{day}.json", V14_SPEC_SHA256_EXPECTED)
        if not v14_day:
            return _stop(f"STOP. V14 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
        v14_rows.extend(list(v14_day.get("rows") or []))
        v16_day = load_v16_day_cache(V16_CACHE / f"day_{day}.json", V16_SPEC_SHA256_EXPECTED)
        if not v16_day:
            return _stop(f"STOP. V16 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
        v16_rows.extend(list(v16_day.get("rows") or []))
    leak["TREND_BIT_MISMATCH_N"] = mismatch
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    if len(v8_sigs) != int(B1_SIGNAL_N_EXPECTED) or len(v12_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. Signal n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)

    exe = [r for r in v12_rows if r.get("executable_signal")]
    fills_src = [r for r in exe if _e4(r).get("filled")]
    unfilled_n = len(exe) - len(fills_src)
    sig_hash = set_hash(signal_tuples(v12_rows))
    elig_hash = set_hash(eligible_tuples(v12_rows))
    fill_hash = set_hash(fill_tuples(exe))
    sig_ok = sig_hash == SIGNAL_SET_HASH_EXPECTED and set_hash(signal_tuples(v8_sigs)) == SIGNAL_SET_HASH_EXPECTED
    elig_ok = elig_hash == ELIGIBLE_SET_HASH_EXPECTED and len(exe) == int(B1_EXECUTABLE_N_EXPECTED)
    fill_ok = fill_hash == E4_FILL_SET_HASH_EXPECTED and len(fills_src) == int(E4_FILLED_N_EXPECTED)
    if unfilled_n != int(E4_UNFILLED_N_EXPECTED) or len(v14_rows) != int(E4_FILLED_N_EXPECTED) or len(v16_rows) != int(E4_FILLED_N_EXPECTED):
        return _stop("STOP. Fill identity n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
    identity_ok = bool(stack_ok and sig_ok and elig_ok and fill_ok)

    path_rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_fills = [r for r in v12_by_day[day] if r.get("executable_signal") and _e4(r).get("filled")]
        cache_path = V17_CACHE / f"day_{day}.json"
        cached = load_v17_day_cache(cache_path, v17_sha)
        if cached and len(list(cached.get("rows") or [])) == len(day_fills):
            print(f"{day} v17 cache hit fills={len(day_fills)}", flush=True)
            path_rows.extend(list(cached.get("rows") or []))
            for k, v in dict(cached.get("leak") or {}).items():
                if k in leak:
                    leak[k] = int(leak.get(k) or 0) + int(v or 0)
            continue
        body = replay_v17_day(
            {"date": day, "capture_path": cap.get("capture_path"), "fills": day_fills, "spec_sha": v17_sha}
        )
        if not body.get("ok"):
            return _stop(f"STOP. V17 path replay failed {day}: {body.get('blocker')}.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)
        save_v17_day_cache(cache_path, body)
        path_rows.extend(list(body.get("rows") or []))
        for k, v in dict(body.get("leak") or {}).items():
            if k in leak:
                leak[k] = int(leak.get(k) or 0) + int(v or 0)

    if len(path_rows) != int(E4_FILLED_N_EXPECTED):
        return _stop(f"STOP. Path rows {len(path_rows)} != {E4_FILLED_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha)

    v16_by_key = {
        (str(r.get("date") or ""), str(r.get("symbol") or ""), float(r.get("fill_t") or 0.0)): r for r in v16_rows
    }
    leak["G3_ARM_MISMATCH_N"] = arm_parity_n(path_rows, v16_by_key)
    leak["G2_EVENT_MISMATCH_N"] = g2_event_parity_n(path_rows, v16_by_key)

    tax = taxonomy(path_rows)
    if not v14_taxonomy_ok(tax) or not v14_p3_ok(tax) or not v14_p1_ok(tax):
        return _stop("STOP. V17 taxonomy mismatch vs V14 lock.", pre=pre, leak=leak, parent_sha=parent_sha, v17_sha=v17_sha, extra={"taxonomy": tax})

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS") and int(leak.get("PRE_FILL_EVENT_N") or 0) == 0)

    hold = hold_benchmark(path_rows)
    pack = g3_pack(path_rows, integrity_ok=integ_ok)
    cmp = g2_compare(pack)
    dec = decision_case(identity_ok=identity_ok, integrity_ok=integ_ok, pack=pack)
    g3_req = required_g3_block(pack, cmp)
    t180 = tax.get(180) or {}
    t300 = tax.get(300) or {}
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
        "V14_SPEC_SHA256": V14_SPEC_SHA256_EXPECTED,
        "V15_SPEC_SHA256": V15_SPEC_SHA256_EXPECTED,
        "V16_SPEC_SHA256": V16_SPEC_SHA256_EXPECTED,
        "V17_SPEC_SHA256": v17_sha,
        "ENTRY_STACK_PARITY": bool(stack_ok),
        "E4_FILL_SET_HASH_PARITY": bool(fill_ok),
        "SIGNAL_SET_HASH": sig_hash,
        "ELIGIBLE_SET_HASH": elig_hash,
        "E4_FILL_SET_HASH": fill_hash,
        "FILLED_N": len(path_rows),
        "G3_EVENT_N_180": g3_req.get("G3_EVENT_N_180"),
        "G3_EVENT_N_300": g3_req.get("G3_EVENT_N_300"),
        "P2_EVENT_RATE_180": g3_req.get("P2_EVENT_RATE_180"),
        "P2_EVENT_RATE_300": g3_req.get("P2_EVENT_RATE_300"),
        "P3_EVENT_RATE_180": g3_req.get("P3_EVENT_RATE_180"),
        "P3_EVENT_RATE_300": g3_req.get("P3_EVENT_RATE_300"),
        "P2_CAPTURE_RATE_180": g3_req.get("P2_CAPTURE_RATE_180"),
        "P2_CAPTURE_RATE_300": g3_req.get("P2_CAPTURE_RATE_300"),
        "P2_MISSED_N": {180: g3_req.get("P2_MISSED_N_180"), 300: g3_req.get("P2_MISSED_N_300")},
        "DELTA_VS_HOLD180": g3_req.get("DELTA_VS_HOLD180"),
        "DELTA_VS_HOLD300": g3_req.get("DELTA_VS_HOLD300"),
        "P2_DELTA": {180: g3_req.get("P2_DELTA_180"), 300: g3_req.get("P2_DELTA_300")},
        "P3_DELTA": {180: g3_req.get("P3_DELTA_180"), 300: g3_req.get("P3_DELTA_300")},
        "TOTAL_P2_SAVED_BPS": {180: g3_req.get("TOTAL_P2_SAVED_BPS_180"), 300: g3_req.get("TOTAL_P2_SAVED_BPS_300")},
        "TOTAL_P3_DESTROYED_BPS": {180: g3_req.get("TOTAL_P3_DESTROYED_BPS_180"), 300: g3_req.get("TOTAL_P3_DESTROYED_BPS_300")},
        "NET_G3_CONTRIBUTION_BPS": {180: g3_req.get("NET_G3_CONTRIBUTION_BPS_180"), 300: g3_req.get("NET_G3_CONTRIBUTION_BPS_300")},
        "POST_EVENT_RECOVERY_P3": {180: g3_req.get("POST_EVENT_RECOVERY_P3_180"), 300: g3_req.get("POST_EVENT_RECOVERY_P3_300")},
        "G3_SUPPORTED_180": g3_req.get("G3_SUPPORTED_180"),
        "G3_SUPPORTED_300": g3_req.get("G3_SUPPORTED_300"),
        "G3_CROSS_HORIZON_SUPPORTED": g3_req.get("G3_CROSS_HORIZON_SUPPORTED"),
        "BE_RELOSS_FAMILY_CLOSED": bool(dec.get("BE_RELOSS_FAMILY_CLOSED")),
        "G2_COMPARE": cmp,
        "P1_180": t180.get("P1_NEVER_POSITIVE"),
        "P2_180": t180.get("P2_PROFIT_TO_LOSS"),
        "P3_180": t180.get("P3_LOSS_TO_RECOVERY"),
        "P1_300": t300.get("P1_NEVER_POSITIVE"),
        "P2_300": t300.get("P2_PROFIT_TO_LOSS"),
        "P3_300": t300.get("P3_LOSS_TO_RECOVERY"),
        "HOLD_180": hold.get(180),
        "HOLD_300": hold.get(300),
        "TIMING_180": g3_req.get("TIMING_180"),
        "TIMING_300": g3_req.get("TIMING_300"),
        "CASE": dec.get("CASE"),
        "EXIT_SPEC_FROZEN": False,
        "EXIT_CERTIFIED": False,
        "ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT": True,
        "ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT": True,
        "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
        "TRUE_OOS": bool(TRUE_OOS),
        "ENTRY_CERTIFIED": False,
        "RUNTIME_CANDIDATE": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": dec.get("VERDICT"),
        "NEXT": dec.get("NEXT"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "taxonomy": tax,
        "hold": hold,
        "g3": pack,
        "g2_compare": cmp,
        "decision": dec,
        "reporting": reporting,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH", "LIVE_PIDS")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH", "LIVE_PIDS")},
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    a180 = pack.get(180) or {}
    a300 = pack.get(300) or {}
    sheets = {
        "Precommit": kv_rows({"ANALYSIS_ID": ANALYSIS_ID, "V17_SPEC_SHA256": v17_sha, "ENTRY_STACK": DEVELOPMENT_ENTRY_STACK, "CHALLENGER": DEVELOPMENT_CHALLENGER, "MECHANISM": "G3_PROFIT_ARMED_TWO_BAR_BE_RELOSS", "CONFIRMATION_BARS": 2, "THREE_BARS": False, "FOUR_BARS": False, "EXIT_SPEC_FROZEN": False, "BE_FAMILY_LAST_TEST": True}),
        "Identity": kv_rows({"ENTRY_STACK_PARITY": stack_ok, "E4_FILL_SET_HASH_PARITY": fill_ok, "FILLED_N": len(path_rows), "G3_ARM_MISMATCH_N": leak.get("G3_ARM_MISMATCH_N"), "G2_EVENT_MISMATCH_N": leak.get("G2_EVENT_MISMATCH_N")}),
        "Hold": kv_rows({f"H{h}_{k}": (hold.get(h) or {}).get(k) for h in (180, 300) for k in ("mean", "median", "positive_rate")}),
        "Taxonomy": kv_rows({f"{k}_{h}": (tax.get(h) or {}).get(k) for h in (180, 300) for k in ("P1_NEVER_POSITIVE", "P2_PROFIT_TO_LOSS", "P3_LOSS_TO_RECOVERY")}),
        "G3": [g3_summary_row(pack, 180), g3_summary_row(pack, 300)],
        "P2P3": kv_rows({**{f"H180_{k}": a180.get(k) for k in ("P2_EVENT_N", "P2_EVENT_RATE", "P3_EVENT_N", "P3_EVENT_RATE", "P2_CAPTURE_RATE", "P2_MISSED_N", "TOTAL_P2_SAVED_BPS", "TOTAL_P3_DESTROYED_BPS", "NET_G3_CONTRIBUTION_BPS")}, **{f"H300_{k}": a300.get(k) for k in ("P2_EVENT_N", "P2_EVENT_RATE", "P3_EVENT_N", "P3_EVENT_RATE", "P2_CAPTURE_RATE", "P2_MISSED_N", "TOTAL_P2_SAVED_BPS", "TOTAL_P3_DESTROYED_BPS", "NET_G3_CONTRIBUTION_BPS")}}),
        "Recovery": kv_rows({**{f"H180_P3_{k}": ((a180.get("POST_EVENT_RECOVERY") or {}).get("P3") or {}).get(k) for k in ("N", "mean", "median")}, **{f"H300_P3_{k}": ((a300.get("POST_EVENT_RECOVERY") or {}).get("P3") or {}).get(k) for k in ("N", "mean", "median")}}),
        "G2Compare": kv_rows(cmp),
        "Trades": [flatten_trade(r) for r in path_rows] or [{"empty": True}],
        "Reporting": kv_rows(reporting),
        "Integrity": kv_rows(leak),
        "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
    }
    write_artifacts(report, sheets)
    if str((_load(V13_OUT / "report.json").get("required") or {}).get("DEVELOPMENT_ENTRY_STACK") or "") != DEVELOPMENT_ENTRY_STACK:
        print("STOP. V13 official stack mutated.", flush=True)
        return 2
    if str((_load(V14_OUT / "report.json").get("required") or {}).get("V14_SPEC_SHA256") or "") != V14_SPEC_SHA256_EXPECTED:
        print("STOP. V14 official report mutated.", flush=True)
        return 2
    if str((_load(V15_OUT / "report.json").get("required") or {}).get("V15_SPEC_SHA256") or "") != V15_SPEC_SHA256_EXPECTED:
        print("STOP. V15 official report mutated.", flush=True)
        return 2
    if str((_load(V16_OUT / "report.json").get("required") or {}).get("V16_SPEC_SHA256") or "") != V16_SPEC_SHA256_EXPECTED:
        print("STOP. V16 official report mutated.", flush=True)
        return 2
    print(
        f"DONE verdict={req.get('VERDICT')} closed={req.get('BE_RELOSS_FAMILY_CLOSED')} "
        f"n={req.get('G3_EVENT_N_180')}/{req.get('G3_EVENT_N_300')} ni={ni_ok} out={V17_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
