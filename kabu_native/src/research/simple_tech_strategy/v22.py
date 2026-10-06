"""Offline SIMPLE_TECH V22 sizing execution capacity RCA. Frozen E4+180. No sizing policy. No Capture control."""
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
from research.simple_tech_entry_family.isolation import V13_OUT
from research.simple_tech_entry_family.publish import kv_rows
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v8_harvest import V8_CACHE
from research.simple_tech_entry_family.v9_harvest import attach_bits
from research.simple_tech_entry_family.v11_harvest import is_b1
from research.simple_tech_entry_family.v12_harvest import V12_CACHE
from research.simple_tech_entry_family.v13_analyze import eligible_tuples, fill_tuples, reporting_semantics, set_hash, signal_tuples
from research.simple_tech_exit_family.isolation import V18_OUT, V19_OUT
from research.simple_tech_strategy.isolation import (
    TODAY,
    V20_OUT,
    V21_OUT,
    V22_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_strategy.v22_analyze import (
    analyze_capacity,
    capability_pack,
    decision_case,
    flatten_capacity_trade,
    identity_trade_rows,
    parity_pack,
)
from research.simple_tech_strategy.v22_harvest import (
    V18_CACHE,
    V22_CACHE,
    load_v22_day_cache,
    replay_v22_day,
    save_v22_day_cache,
)
from research.simple_tech_strategy.v22_publish import REQUIRED_KEYS, build_markdown, write_artifacts
from research.simple_tech_strategy.v22_spec import (
    ACTUAL_EXIT_SET_HASH_EXPECTED,
    ADOPT_1M_POLICY,
    ADOPT_SIZING_POLICY,
    ANALYSIS_ID,
    B1_EXECUTABLE_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    C14_USED,
    DEEPER_BOOK,
    DEVELOPMENT_ENTRY_STACK,
    DEVELOPMENT_STRATEGY_STACK,
    E4_CHANGED,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    E4_UNFILLED_N_EXPECTED,
    ELIGIBLE_SET_HASH_EXPECTED,
    EMA_CHANGED,
    ENTRY_CHANGED,
    EXIT_ADDED,
    EXIT_CHANGED,
    EXIT_WAIT_EXTRA,
    FORWARD_OOS_ELIGIBLE,
    GRID_SEARCH,
    HOLD_CHANGED,
    HOLD_SEC,
    HYPOTHETICAL_NOTIONAL_IS_POLICY,
    HYPOTHETICAL_NOTIONAL_YEN,
    KELLY,
    ML_USED,
    PARENT_SPEC_SHA256_EXPECTED,
    PARTIAL_FILL,
    PNL_OPTIMIZATION,
    POSITION_SIZING_CERTIFIED,
    POSITION_SIZING_SPEC_FROZEN,
    PRICE_BUCKET_SIZING,
    PULLBACK_CHANGED,
    QUEUE_FILL,
    QUARTILE_GATE,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    REUSE_100SHARE_FILL_SET,
    RISK_SIZING,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    SIZING_SEARCH,
    SLIPPAGE_ASSUMPTION,
    STRATEGY_CERTIFIED,
    SYMBOL_SIZING,
    TARGET_NOTIONAL_CHOSEN,
    TRUE_OOS,
    V8_SPEC_SHA256_EXPECTED,
    V12_SPEC_SHA256_EXPECTED,
    V13_SPEC_SHA256_EXPECTED,
    V18_SPEC_SHA256_EXPECTED,
    V19_SPEC_SHA256_EXPECTED,
    V19_VERDICT_EXPECTED,
    V20_SPEC_SHA256_EXPECTED,
    V20_VERDICT_EXPECTED,
    V20_VERDICT_MUTATION,
    V21_SPEC_SHA256_EXPECTED,
    V21_VERDICT_EXPECTED,
    V21_VERDICT_MUTATION,
    VOL_SIZING,
    VWAP_FILL,
    WAIT_CHANGED,
    canonical_v22_spec,
    spec_sha256_v22,
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
    "ENTRY_CHANGE_N",
    "EXIT_CHANGE_N",
    "SIZING_SEARCH_N",
    "ADOPT_SIZING_POLICY_N",
    "TARGET_NOTIONAL_N",
    "PNL_OPT_N",
    "ADOPT_1M_POLICY_N",
    "QUARTILE_GATE_N",
    "QUEUE_FILL_N",
    "PARTIAL_FILL_N",
    "VWAP_N",
    "DEEPER_BOOK_N",
    "SLIPPAGE_N",
    "EXIT_WAIT_EXTRA_N",
    "V18_CACHE_COPY_N",
    "V20_WRITE_N",
    "V21_WRITE_N",
    "V19_WRITE_N",
    "V13_WRITE_N",
    "KELLY_N",
    "VOL_SIZING_N",
    "RISK_SIZING_N",
    "SYMBOL_SIZING_N",
    "ML_USE_N",
    "GRID_SEARCH_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v22_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V22_INVALID",
            "CASE": "D",
            "NEXT": msg,
            "TRUE_OOS": False,
            "FORWARD_OOS_ELIGIBLE": False,
            "POSITION_SIZING_SPEC_FROZEN": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V22_SPEC_SHA256": v22_sha,
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "_markdown": build_markdown({"required": req}),
    }
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v22_spec()
    v22_sha = spec_sha256_v22(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v22={v22_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or RUNTIME_CANDIDATE
        or STRATEGY_CERTIFIED
        or POSITION_SIZING_SPEC_FROZEN
        or POSITION_SIZING_CERTIFIED
        or ENTRY_CHANGED
        or EXIT_CHANGED
        or EMA_CHANGED
        or RCI_CHANGED
        or PULLBACK_CHANGED
        or E4_CHANGED
        or WAIT_CHANGED
        or HOLD_CHANGED
        or EXIT_ADDED
        or SIZING_SEARCH
        or ADOPT_SIZING_POLICY
        or TARGET_NOTIONAL_CHOSEN
        or PNL_OPTIMIZATION
        or VOL_SIZING
        or RISK_SIZING
        or KELLY
        or PRICE_BUCKET_SIZING
        or SYMBOL_SIZING
        or QUARTILE_GATE
        or PARTIAL_FILL
        or QUEUE_FILL
        or VWAP_FILL
        or DEEPER_BOOK
        or SLIPPAGE_ASSUMPTION
        or EXIT_WAIT_EXTRA
        or REUSE_100SHARE_FILL_SET
        or ADOPT_1M_POLICY
        or HYPOTHETICAL_NOTIONAL_IS_POLICY
        or V20_VERDICT_MUTATION
        or V21_VERDICT_MUTATION
        or C14_USED
        or ML_USED
        or GRID_SEARCH
        or bool(FORWARD_OOS_ELIGIBLE)
        or float(HOLD_SEC) != 180.0
        or float(HYPOTHETICAL_NOTIONAL_YEN) != 1000000.0
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)

    v13_req = dict((_load(V13_OUT / "report.json").get("required") or {}))
    if str(v13_req.get("V13_SPEC_SHA256") or "") != V13_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V13 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if str(v13_req.get("DEVELOPMENT_ENTRY_STACK") or "") != DEVELOPMENT_ENTRY_STACK:
        return _stop("STOP. V13 entry stack mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    v18_req = dict((_load(V18_OUT / "report.json").get("required") or {}))
    if str(v18_req.get("V18_SPEC_SHA256") or "") != V18_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V18 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    v19_req = dict((_load(V19_OUT / "report.json").get("required") or {}))
    if str(v19_req.get("V19_SPEC_SHA256") or "") != V19_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V19 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if str(v19_req.get("VERDICT") or "") != V19_VERDICT_EXPECTED:
        return _stop("STOP. V19 official verdict mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    v20_req = dict((_load(V20_OUT / "report.json").get("required") or {}))
    if str(v20_req.get("V20_SPEC_SHA256") or "") != V20_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V20 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if str(v20_req.get("VERDICT") or "") != V20_VERDICT_EXPECTED:
        return _stop("STOP. V20 official verdict mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if bool(v20_req.get("FORWARD_OOS_ELIGIBLE")):
        return _stop("STOP. V20 FORWARD_OOS_ELIGIBLE mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    v21_req = dict((_load(V21_OUT / "report.json").get("required") or {}))
    if str(v21_req.get("V21_SPEC_SHA256") or "") != V21_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V21 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if str(v21_req.get("VERDICT") or "") != V21_VERDICT_EXPECTED:
        return _stop("STOP. V21 official verdict mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps]
        + [
            str(V8_CACHE),
            str(V12_CACHE),
            str(V13_OUT / "report.json"),
            str(V19_OUT / "report.json"),
            str(V20_OUT / "report.json"),
            str(V21_OUT / "report.json"),
            str(V22_CACHE),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)

    v8_sigs: list[dict[str, Any]] = []
    v12_rows: list[dict[str, Any]] = []
    v12_by_day: dict[str, list[dict[str, Any]]] = {}
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            if is_b1(rec):
                v8_sigs.append(rec)
        v12_day = load_day_cache(V12_CACHE / f"day_{day}.json", V12_SPEC_SHA256_EXPECTED)
        if not v12_day:
            return _stop(f"STOP. V12 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
        day_rows = list(v12_day.get("rows") or [])
        v12_by_day[day] = day_rows
        v12_rows.extend(day_rows)
    leak["TREND_BIT_MISMATCH_N"] = mismatch
    for k in (
        "MISSING_ENTRY_QTY_N",
        "MISSING_EXIT_QTY_N",
        "EXIT_BID_MISS_N",
        "ENTRY_CAPACITY_LT_100_N",
        "EXIT_CAPACITY_LT_100_N",
        "ASK_CROSS_QTY_MISS_N",
        "ASK_CROSS_FILL_N",
        "E4_UNFILLED_N",
        "NOT_ELIGIBLE_N",
        "FILL_N",
        "SESSION_CLAMP_N",
        "INSIDE_COLLAPSE_N",
    ):
        leak[k] = 0
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if len(v8_sigs) != int(B1_SIGNAL_N_EXPECTED) or len(v12_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop("STOP. Signal n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    exe_src = [r for r in v12_rows if r.get("executable_signal")]
    fills_src = [r for r in exe_src if ((r.get("exec") or {}).get("E4_INSIDE1_W5") or {}).get("filled")]
    sig_hash = set_hash(signal_tuples(v12_rows))
    elig_hash = set_hash(eligible_tuples(v12_rows))
    fill_hash_src = set_hash(fill_tuples(exe_src))
    if sig_hash != SIGNAL_SET_HASH_EXPECTED or set_hash(signal_tuples(v8_sigs)) != SIGNAL_SET_HASH_EXPECTED:
        return _stop("STOP. Signal set hash mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if elig_hash != ELIGIBLE_SET_HASH_EXPECTED or len(exe_src) != int(B1_EXECUTABLE_N_EXPECTED):
        return _stop("STOP. Eligible set hash mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if fill_hash_src != E4_FILL_SET_HASH_EXPECTED or len(fills_src) != int(E4_FILLED_N_EXPECTED):
        return _stop("STOP. V12 E4 fill set hash mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
    if len(exe_src) - len(fills_src) != int(E4_UNFILLED_N_EXPECTED):
        return _stop("STOP. Unfilled n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)

    path_rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_sigs = list(v12_by_day.get(day) or [])
        cache_path = V22_CACHE / f"day_{day}.json"
        if V18_CACHE in cache_path.parents or cache_path.parent == V18_CACHE:
            leak["V18_CACHE_COPY_N"] = 1
            return _stop("STOP. V22 attempted V18 cache path.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
        cached = load_v22_day_cache(cache_path, v22_sha)
        if cached and len(list(cached.get("rows") or [])) == len(day_sigs):
            print(f"{day} v22 cache hit n={len(day_sigs)}", flush=True)
            path_rows.extend(list(cached.get("rows") or []))
            for k, v in dict(cached.get("leak") or {}).items():
                if k in leak:
                    leak[k] = int(leak.get(k) or 0) + int(v or 0)
            continue
        body = replay_v22_day(
            {"date": day, "capture_path": cap.get("capture_path"), "signals": day_sigs, "spec_sha": v22_sha}
        )
        if not body.get("ok"):
            return _stop(f"STOP. V22 harvest failed {day}: {body.get('blocker')}.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)
        save_v22_day_cache(cache_path, body)
        path_rows.extend(list(body.get("rows") or []))
        for k, v in dict(body.get("leak") or {}).items():
            if k in leak:
                leak[k] = int(leak.get(k) or 0) + int(v or 0)

    if len(path_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. Replay rows {len(path_rows)} != {B1_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v22_sha=v22_sha)

    par = parity_pack(path_rows)
    cap = capability_pack(path_rows, leak)
    stats = analyze_capacity(path_rows)
    fills = identity_trade_rows(path_rows)
    stack_parity = bool(
        str(v19_req.get("DEVELOPMENT_STRATEGY_STACK") or "") == DEVELOPMENT_STRATEGY_STACK
        and str(v20_req.get("DEVELOPMENT_STRATEGY_STACK") or "") == DEVELOPMENT_STRATEGY_STACK
        and bool(v21_req.get("STRATEGY_STACK_PARITY"))
        and par.get("PARITY_OK")
    )

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS") and stack_parity)
    dec = decision_case(integrity_ok=integ_ok, parity_ok=bool(par.get("PARITY_OK")), cap=cap, stats=stats)

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V21_SPEC_SHA256": V21_SPEC_SHA256_EXPECTED,
        "V22_SPEC_SHA256": v22_sha,
        "STRATEGY_STACK_PARITY": bool(stack_parity),
        "E4_100SHARE_FILL_PARITY": bool(par.get("E4_100SHARE_FILL_PARITY")),
        "ACTUAL_EXIT_SET_HASH_PARITY": bool(par.get("ACTUAL_EXIT_SET_HASH_PARITY")),
        "ELIGIBLE_N": par.get("ELIGIBLE_N"),
        "FILLED_N_100": par.get("FILLED_N_100"),
        "E4_FILL_SET_HASH": par.get("E4_FILL_SET_HASH"),
        "ACTUAL_EXIT_SET_HASH": par.get("ACTUAL_EXIT_SET_HASH"),
        "ENTRY_SIZE_REPLAY_CAPABILITY": bool(cap.get("ENTRY_SIZE_REPLAY_CAPABILITY")),
        "EXIT_SIZE_REPLAY_CAPABILITY": bool(cap.get("EXIT_SIZE_REPLAY_CAPABILITY")),
        "ENTRY_CAPACITY_DISTRIBUTION": stats.get("ENTRY_CAPACITY_DISTRIBUTION"),
        "EXIT_CAPACITY_DISTRIBUTION": stats.get("EXIT_CAPACITY_DISTRIBUTION"),
        "ROUNDTRIP_CAPACITY_DISTRIBUTION": stats.get("ROUNDTRIP_CAPACITY_DISTRIBUTION"),
        "CAPACITY_EQ_100_N": stats.get("CAPACITY_EQ_100_N"),
        "CAPACITY_GE_200_N": stats.get("CAPACITY_GE_200_N"),
        "CAPACITY_GE_300_N": stats.get("CAPACITY_GE_300_N"),
        "CAPACITY_GE_500_N": stats.get("CAPACITY_GE_500_N"),
        "CAPACITY_GE_1000_N": stats.get("CAPACITY_GE_1000_N"),
        "MULTILOT_DAY_N_GE_200": stats.get("MULTILOT_DAY_N_GE_200"),
        "NORMALIZED_1M_EXECUTION_FEASIBLE_N": stats.get("NORMALIZED_1M_EXECUTION_FEASIBLE_N"),
        "CASE": dec.get("CASE"),
        "POSITION_SIZING_SPEC_FROZEN": False,
        "POSITION_SIZING_CERTIFIED": False,
        "TRUE_OOS": bool(TRUE_OOS),
        "FORWARD_OOS_ELIGIBLE": False,
        "V20_OFFICIAL_UNCHANGED": True,
        "V21_OFFICIAL_UNCHANGED": True,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": dec.get("VERDICT"),
        "NEXT": dec.get("NEXT"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "parity": par,
        "capability": cap,
        "capacity": stats,
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
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "V22_SPEC_SHA256": v22_sha,
                "HYPOTHETICAL_NOTIONAL_IS_POLICY": False,
                "SIZING_SEARCH": False,
                "ADOPT_SIZING_POLICY": False,
                "TRUE_OOS": False,
            }
        ),
        "Identity": kv_rows({k: par.get(k) for k in par.keys() if k != "details"}),
        "Capability": kv_rows(cap),
        "EntryDist": kv_rows(dict(stats.get("ENTRY_CAPACITY_DISTRIBUTION") or {})),
        "ExitDist": kv_rows(dict(stats.get("EXIT_CAPACITY_DISTRIBUTION") or {})),
        "RoundtripDist": kv_rows(dict(stats.get("ROUNDTRIP_CAPACITY_DISTRIBUTION") or {})),
        "Counts": kv_rows(
            {
                "CAPACITY_EQ_100_N": stats.get("CAPACITY_EQ_100_N"),
                "CAPACITY_GE_200_N": stats.get("CAPACITY_GE_200_N"),
                "CAPACITY_GE_300_N": stats.get("CAPACITY_GE_300_N"),
                "CAPACITY_GE_500_N": stats.get("CAPACITY_GE_500_N"),
                "CAPACITY_GE_1000_N": stats.get("CAPACITY_GE_1000_N"),
                "MULTILOT_DAY_N_GE_200": stats.get("MULTILOT_DAY_N_GE_200"),
                "NORMALIZED_1M_EXECUTION_FEASIBLE_N": stats.get("NORMALIZED_1M_EXECUTION_FEASIBLE_N"),
                "WINDOW_GT_FILL_SNAPSHOT_N": stats.get("WINDOW_GT_FILL_SNAPSHOT_N"),
            }
        ),
        "Eligible": [flatten_capacity_trade(r) for r in path_rows if r.get("executable_signal")] or [{"empty": True}],
        "Fills": [flatten_capacity_trade(r) for r in fills] or [{"empty": True}],
        "Reporting": kv_rows(reporting),
        "Integrity": kv_rows(leak),
        "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
    }
    write_artifacts(report, sheets)
    v20_after = _load(V20_OUT / "report.json")
    v21_after = _load(V21_OUT / "report.json")
    if str((v20_after.get("required") or {}).get("VERDICT") or "") != V20_VERDICT_EXPECTED:
        print("STOP. V20 official verdict mutated.", flush=True)
        return 2
    if str((v21_after.get("required") or {}).get("VERDICT") or "") != V21_VERDICT_EXPECTED:
        print("STOP. V21 official verdict mutated.", flush=True)
        return 2
    print(
        f"DONE verdict={req.get('VERDICT')} case={req.get('CASE')} "
        f"ge200={req.get('CAPACITY_GE_200_N')} ni={ni_ok} out={V22_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
