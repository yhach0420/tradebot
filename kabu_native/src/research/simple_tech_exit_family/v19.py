"""Offline SIMPLE_TECH V19 EXIT structure verification. Independent Capture replay. No V18 cache copy. No C14."""
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
    V18_OUT,
    V19_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_exit_family.v14_analyze import entry_stack_ok
from research.simple_tech_exit_family.v14_harvest import _e4
from research.simple_tech_exit_family.v18_analyze import policy_pack
from research.simple_tech_exit_family.v18_publish import flatten_trade, kv_rows
from research.simple_tech_exit_family.v19_analyze import (
    causal_exit_audit,
    decision_case,
    identity_hashes,
    latency_parity,
    load_v18_official_trades,
    numeric_parity,
    trade_by_trade_parity,
)
from research.simple_tech_exit_family.v19_harvest import (
    V18_CACHE,
    V19_CACHE,
    load_v19_day_cache,
    replay_v19_day,
    save_v19_day_cache,
)
from research.simple_tech_exit_family.v19_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_exit_family.v19_spec import (
    ALT_HOLD_SEARCH,
    ANALYSIS_ID,
    B1_EXECUTABLE_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    BB_CHANGED,
    BOARD_RESTORED,
    BREAK_EVEN_ARMED,
    C14_USED,
    D1_ARMED,
    D2_ARMED,
    D3_ARMED,
    DEVELOPMENT_CHALLENGER,
    DEVELOPMENT_ENTRY_STACK,
    DEVELOPMENT_STRATEGY_STACK,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    E4_UNFILLED_N_EXPECTED,
    ELIGIBLE_SET_HASH_EXPECTED,
    EMA_CHANGED,
    ENTRY_CERTIFIED,
    ENTRY_RULE_CHANGED,
    EXIT_CERTIFIED,
    EXIT_POLICY_NAME,
    EXIT_SPEC_FROZEN,
    FOUR_BARS_CONFIRMATION,
    G1_ARMED,
    G2_ARMED,
    G3_ARMED,
    GRID_SEARCH,
    HOLD_60_POLICY,
    HOLD_300_POLICY,
    HOLD_SEC,
    INSIDE_SELL,
    MFE10_ARMED,
    ML_USED,
    NEW_INDICATOR,
    NEW_PERFORMANCE_GATE,
    PARENT_SPEC_SHA256_EXPECTED,
    PASSIVE_ASK_SELL,
    PA_RESTORED,
    PLUS_1BPS_ARMED,
    PLUS_3BPS_ARMED,
    PLUS_5BPS_ARMED,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    STOP_LOSS_ARMED,
    STRATEGY_CERTIFIED,
    THREE_BARS_CONFIRMATION,
    THRESHOLD_SEARCH,
    TRAIL_50,
    TRAIL_80,
    TRAILING,
    TRUE_OOS,
    UNFILLED_VIRTUAL_POSITION,
    V8_SPEC_SHA256_EXPECTED,
    V10_SPEC_SHA256_EXPECTED,
    V12_SPEC_SHA256_EXPECTED,
    V13_SPEC_SHA256_EXPECTED,
    V18_CACHE_COPY,
    V18_CASE_EXPECTED,
    V18_SPEC_SHA256_EXPECTED,
    V18_VERDICT_EXPECTED,
    VOLUME_RESTORED,
    WAIT_REPRICE_CHASE,
    canonical_v19_spec,
    spec_sha256_v19,
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
    "THRESHOLD_SEARCH_N",
    "ALT_HOLD_N",
    "G1_N",
    "G2_N",
    "G3_N",
    "D1_N",
    "D2_N",
    "D3_N",
    "PASSIVE_ASK_N",
    "INSIDE_SELL_N",
    "WAIT_REPRICE_CHASE_N",
    "UNFILLED_VIRTUAL_N",
    "PRE_FILL_EVENT_N",
    "PRE_DECISION_EXIT_N",
    "LAST_BEFORE_USED_AS_EXIT_N",
    "FUTURE_QUOTE_N",
    "ENTRY_RULE_CHANGE_N",
    "EMA_CHANGE_N",
    "BB_CHANGE_N",
    "RCI_CHANGE_N",
    "GRID_SEARCH_N",
    "ML_USE_N",
    "NEW_INDICATOR_N",
    "NEW_PERFORMANCE_GATE_N",
    "V13_WRITE_N",
    "V18_WRITE_N",
    "V18_CACHE_COPY_N",
    "EXIT_BID_MISS_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v19_sha: str, extra: dict[str, Any] | None = None) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V19_EXIT_STRUCTURE_VERIFICATION_FAILED",
            "NEXT": msg,
            "TRUE_OOS": False,
            "STRATEGY_CERTIFIED": False,
            "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT": False,
            "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V19_SPEC_SHA256": v19_sha,
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
    spec = canonical_v19_spec()
    v19_sha = spec_sha256_v19(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} v19={v19_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or EXIT_SPEC_FROZEN
        or EXIT_CERTIFIED
        or ENTRY_CERTIFIED
        or RUNTIME_CANDIDATE
        or STRATEGY_CERTIFIED
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
        or TRAILING
        or THREE_BARS_CONFIRMATION
        or FOUR_BARS_CONFIRMATION
        or G1_ARMED
        or G2_ARMED
        or G3_ARMED
        or D1_ARMED
        or D2_ARMED
        or D3_ARMED
        or STOP_LOSS_ARMED
        or BREAK_EVEN_ARMED
        or PASSIVE_ASK_SELL
        or INSIDE_SELL
        or WAIT_REPRICE_CHASE
        or ALT_HOLD_SEARCH
        or HOLD_60_POLICY
        or HOLD_300_POLICY
        or NEW_PERFORMANCE_GATE
        or V18_CACHE_COPY
        or float(HOLD_SEC) != 180.0
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)

    if str((_load(V8_OUT / "report.json").get("required") or {}).get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    if str((_load(V10_OUT / "report.json").get("required") or {}).get("V10_SPEC_SHA256") or "") != V10_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V10 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    v13_req = dict((_load(V13_OUT / "report.json").get("required") or {}))
    if str(v13_req.get("V13_SPEC_SHA256") or "") != V13_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V13 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    stack_ok = entry_stack_ok(v13_req)
    if not stack_ok:
        return _stop("STOP. V13 development freeze stack mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    v18_req = dict((_load(V18_OUT / "report.json").get("required") or {}))
    if str(v18_req.get("V18_SPEC_SHA256") or "") != V18_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V18 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    if str(v18_req.get("VERDICT") or "") != V18_VERDICT_EXPECTED or str(v18_req.get("CASE") or "") != V18_CASE_EXPECTED:
        return _stop("STOP. V18 official CASE/VERDICT mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    policy_ok = str(v18_req.get("EXIT_POLICY") or "") == EXIT_POLICY_NAME

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [str(V12_CACHE), str(V13_OUT / "report.json"), str(V18_OUT / "report.json"), str(V18_OUT / "audit.xlsx")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)

    v8_sigs: list[dict[str, Any]] = []
    v12_rows: list[dict[str, Any]] = []
    v12_by_day: dict[str, list[dict[str, Any]]] = {}
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            if is_b1(rec):
                v8_sigs.append(rec)
        v12_day = load_day_cache(V12_CACHE / f"day_{day}.json", V12_SPEC_SHA256_EXPECTED)
        if not v12_day:
            return _stop(f"STOP. V12 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
        day_rows_v12 = list(v12_day.get("rows") or [])
        v12_by_day[day] = day_rows_v12
        v12_rows.extend(day_rows_v12)
    leak["TREND_BIT_MISMATCH_N"] = mismatch
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    if len(v8_sigs) != int(B1_SIGNAL_N_EXPECTED) or len(v12_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop("STOP. Signal n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)

    exe = [r for r in v12_rows if r.get("executable_signal")]
    fills_src = [r for r in exe if _e4(r).get("filled")]
    unfilled_n = len(exe) - len(fills_src)
    sig_hash = set_hash(signal_tuples(v12_rows))
    elig_hash = set_hash(eligible_tuples(v12_rows))
    fill_hash = set_hash(fill_tuples(exe))
    sig_ok = sig_hash == SIGNAL_SET_HASH_EXPECTED and set_hash(signal_tuples(v8_sigs)) == SIGNAL_SET_HASH_EXPECTED
    elig_ok = elig_hash == ELIGIBLE_SET_HASH_EXPECTED and len(exe) == int(B1_EXECUTABLE_N_EXPECTED)
    fill_ok = fill_hash == E4_FILL_SET_HASH_EXPECTED and len(fills_src) == int(E4_FILLED_N_EXPECTED)
    if unfilled_n != int(E4_UNFILLED_N_EXPECTED):
        return _stop("STOP. Fill identity n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
    identity_ok = bool(stack_ok and sig_ok and elig_ok and fill_ok and policy_ok)

    v18_trades = load_v18_official_trades(V18_OUT / "audit.xlsx")
    if len(v18_trades) != int(E4_FILLED_N_EXPECTED):
        return _stop(f"STOP. V18 official trades n={len(v18_trades)}.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)

    path_rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_fills = [r for r in v12_by_day[day] if r.get("executable_signal") and _e4(r).get("filled")]
        cache_path = V19_CACHE / f"day_{day}.json"
        if V18_CACHE in cache_path.parents or cache_path.parent == V18_CACHE:
            leak["V18_CACHE_COPY_N"] = 1
            return _stop("STOP. V19 attempted V18 cache path.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
        cached = load_v19_day_cache(cache_path, v19_sha)
        if cached and len(list(cached.get("rows") or [])) == len(day_fills):
            print(f"{day} v19 cache hit fills={len(day_fills)}", flush=True)
            path_rows.extend(list(cached.get("rows") or []))
            for k, v in dict(cached.get("leak") or {}).items():
                if k in leak:
                    leak[k] = int(leak.get(k) or 0) + int(v or 0)
            continue
        body = replay_v19_day(
            {"date": day, "capture_path": cap.get("capture_path"), "fills": day_fills, "spec_sha": v19_sha}
        )
        if not body.get("ok"):
            return _stop(f"STOP. V19 independent replay failed {day}: {body.get('blocker')}.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)
        save_v19_day_cache(cache_path, body)
        path_rows.extend(list(body.get("rows") or []))
        for k, v in dict(body.get("leak") or {}).items():
            if k in leak:
                leak[k] = int(leak.get(k) or 0) + int(v or 0)

    if len(path_rows) != int(E4_FILLED_N_EXPECTED):
        return _stop(f"STOP. Path rows {len(path_rows)} != {E4_FILLED_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v19_sha=v19_sha)

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS") and int(leak.get("PRE_FILL_EVENT_N") or 0) == 0)

    hashes = identity_hashes(path_rows)
    tbt = trade_by_trade_parity(path_rows, v18_trades)
    causal = causal_exit_audit(path_rows)
    pack = policy_pack(path_rows, integrity_ok=integ_ok)
    numeric = numeric_parity(pack)
    latp = latency_parity(path_rows)
    leak["PRE_DECISION_EXIT_N"] = int(causal.get("PRE_DECISION_EXIT_N") or 0)
    leak["LAST_BEFORE_USED_AS_EXIT_N"] = int(causal.get("LAST_BEFORE_USED_AS_EXIT_N") or 0)
    leak["EXIT_BID_MISS_N"] = int(causal.get("EXIT_BID_MISS_N") or 0)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS") and int(leak.get("PRE_FILL_EVENT_N") or 0) == 0)
    dec = decision_case(
        identity_ok=identity_ok,
        integrity_ok=integ_ok,
        policy_ok=policy_ok,
        hashes=hashes,
        tbt=tbt,
        causal=causal,
        numeric=numeric,
        latency=latp,
    )
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
        "V18_SPEC_SHA256": V18_SPEC_SHA256_EXPECTED,
        "V19_SPEC_SHA256": v19_sha,
        "ENTRY_STACK_PARITY": bool(stack_ok),
        "E4_FILL_SET_HASH_PARITY": bool(fill_ok),
        "EXIT_POLICY_PARITY": bool(policy_ok),
        "SIGNAL_SET_HASH": sig_hash,
        "ELIGIBLE_SET_HASH": elig_hash,
        "E4_FILL_SET_HASH": fill_hash,
        "FILLED_N": len(path_rows),
        "EXIT_POLICY": EXIT_POLICY_NAME,
        "EXIT_INPUT_FILL_SET_HASH": hashes.get("EXIT_INPUT_FILL_SET_HASH"),
        "SCHEDULED_EXIT_SET_HASH": hashes.get("SCHEDULED_EXIT_SET_HASH"),
        "ACTUAL_EXIT_SET_HASH": hashes.get("ACTUAL_EXIT_SET_HASH"),
        "EXIT_TRADE_BY_TRADE_PARITY": bool(tbt.get("EXIT_TRADE_BY_TRADE_PARITY")),
        "EXIT_MISMATCH_N": tbt.get("EXIT_MISMATCH_N"),
        "CAUSAL_EXIT_AUDIT_PASS": bool(causal.get("CAUSAL_EXIT_AUDIT_PASS")),
        "V18_NUMERIC_PARITY": bool(numeric.get("V18_NUMERIC_PARITY")),
        "V18_LATENCY_PARITY": bool(latp.get("V18_LATENCY_PARITY")),
        "ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT": bool(dec.get("ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT")),
        "ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT": bool(dec.get("ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT")),
        "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT": bool(dec.get("EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT")),
        "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT": bool(dec.get("EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT")),
        "DEVELOPMENT_STRATEGY_STACK": dec.get("DEVELOPMENT_STRATEGY_STACK") or (DEVELOPMENT_STRATEGY_STACK if dec.get("PASS") else None),
        "TRUE_OOS": bool(TRUE_OOS),
        "ENTRY_CERTIFIED": False,
        "EXIT_CERTIFIED": False,
        "STRATEGY_CERTIFIED": False,
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
        "hashes": hashes,
        "trade_parity": tbt,
        "causal": causal,
        "numeric": numeric,
        "latency": latp,
        "policy": pack,
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
    sheets = {
        "Precommit": kv_rows({"ANALYSIS_ID": ANALYSIS_ID, "V19_SPEC_SHA256": v19_sha, "V18_SPEC_SHA256": V18_SPEC_SHA256_EXPECTED, "EXIT_POLICY": EXIT_POLICY_NAME, "VERIFICATION_ONLY": True, "V18_CACHE_COPY": False, "TRUE_OOS": False}),
        "Identity": kv_rows({"ENTRY_STACK_PARITY": stack_ok, "E4_FILL_SET_HASH_PARITY": fill_ok, "EXIT_POLICY_PARITY": policy_ok, "FILLED_N": len(path_rows)}),
        "Hashes": kv_rows(hashes),
        "Parity": kv_rows({**dict(numeric.get("checks") or {}), **{f"LAT_{k}": v for k, v in dict(latp.get("checks") or {}).items()}, "EXIT_TRADE_BY_TRADE_PARITY": tbt.get("EXIT_TRADE_BY_TRADE_PARITY"), "EXIT_MISMATCH_N": tbt.get("EXIT_MISMATCH_N")}),
        "Causal": kv_rows(causal),
        "Latency": kv_rows(dict(latp.get("LATENCY") or {})),
        "Trades": [flatten_trade(r) for r in path_rows] or [{"empty": True}],
        "Mismatch": list(tbt.get("details") or []) or [{"EXIT_MISMATCH_N": 0}],
        "Reporting": kv_rows(reporting),
        "Integrity": kv_rows(leak),
        "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
    }
    write_artifacts(report, sheets)
    if str((_load(V13_OUT / "report.json").get("required") or {}).get("DEVELOPMENT_ENTRY_STACK") or "") != DEVELOPMENT_ENTRY_STACK:
        print("STOP. V13 official stack mutated.", flush=True)
        return 2
    v18_after = _load(V18_OUT / "report.json")
    if str((v18_after.get("required") or {}).get("V18_SPEC_SHA256") or "") != V18_SPEC_SHA256_EXPECTED:
        print("STOP. V18 official report mutated.", flush=True)
        return 2
    if str((v18_after.get("required") or {}).get("VERDICT") or "") != V18_VERDICT_EXPECTED:
        print("STOP. V18 official verdict mutated.", flush=True)
        return 2
    print(
        f"DONE verdict={req.get('VERDICT')} mismatch={req.get('EXIT_MISMATCH_N')} "
        f"exec_frozen={req.get('EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT')} ni={ni_ok} out={V19_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
