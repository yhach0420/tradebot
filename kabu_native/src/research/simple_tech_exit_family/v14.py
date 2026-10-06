"""Offline SIMPLE_TECH V14 EXIT state-path RCA. Frozen V13 entry. No EXIT rule. No C14."""
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
from research.simple_tech_exit_family.v14_harvest import _e4
from research.simple_tech_exit_family.isolation import (
    TODAY,
    V14_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_exit_family.v14_analyze import (
    decision_case,
    entry_stack_ok,
    event_pack,
    event_summary_row,
    hold_benchmark,
    required_event_block,
    taxonomy,
)
from research.simple_tech_exit_family.v14_harvest import (
    V14_CACHE,
    load_v14_day_cache,
    replay_v14_day,
    save_v14_day_cache,
)
from research.simple_tech_exit_family.v14_publish import REQUIRED_KEYS, build_markdown, flatten_trade, kv_rows, write_artifacts
from research.simple_tech_exit_family.v14_spec import (
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
    EVENT_IDS,
    EXIT_CERTIFIED,
    EXIT_SPEC_FROZEN,
    GRID_SEARCH,
    ML_USED,
    NEW_INDICATOR,
    PARENT_SPEC_SHA256_EXPECTED,
    PA_RESTORED,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CANDIDATE,
    SIGNAL_SET_HASH_EXPECTED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    UNFILLED_VIRTUAL_POSITION,
    V8_SPEC_SHA256_EXPECTED,
    V10_SPEC_SHA256_EXPECTED,
    V12_SPEC_SHA256_EXPECTED,
    V13_SPEC_SHA256_EXPECTED,
    VOLUME_RESTORED,
    canonical_v14_spec,
    spec_sha256_v14,
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
    "UNFILLED_VIRTUAL_N",
    "PRE_FILL_EVENT_N",
    "FUTURE_QUOTE_N",
    "BAR_INTEG_FAIL_N",
    "ENTRY_RULE_CHANGE_N",
    "EMA_CHANGE_N",
    "BB_CHANGE_N",
    "RCI_CHANGE_N",
    "GRID_SEARCH_N",
    "ML_USE_N",
    "NEW_INDICATOR_N",
    "V13_WRITE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v14_sha: str, extra: dict[str, Any] | None = None) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V14_EXIT_RCA_INVALID",
            "NEXT": msg,
            "TRUE_OOS": False,
            "ENTRY_CERTIFIED": False,
            "EXIT_SPEC_FROZEN": False,
            "NON_INTERFERENCE_PASS": False,
            "PARENT_SPEC_SHA256": parent_sha,
            "V14_SPEC_SHA256": v14_sha,
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
    spec = canonical_v14_spec()
    v14_sha = spec_sha256_v14(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} v14={v14_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
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
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)

    v8_req = dict((_load(V8_OUT / "report.json").get("required") or {}))
    if str(v8_req.get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    v10_req = dict((_load(V10_OUT / "report.json").get("required") or {}))
    if str(v10_req.get("V10_SPEC_SHA256") or "") != V10_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V10 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    v13_body = _load(V13_OUT / "report.json")
    v13_req = dict(v13_body.get("required") or {})
    if str(v13_req.get("V13_SPEC_SHA256") or "") != V13_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V13 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    stack_ok = entry_stack_ok(v13_req)
    if not stack_ok:
        return _stop("STOP. V13 development freeze stack mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps] + [str(V12_CACHE), str(V13_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)

    v8_sigs: list[dict[str, Any]] = []
    v12_rows: list[dict[str, Any]] = []
    v12_by_day: dict[str, list[dict[str, Any]]] = {}
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            if is_b1(rec):
                v8_sigs.append(rec)
        v12_day = load_day_cache(V12_CACHE / f"day_{day}.json", V12_SPEC_SHA256_EXPECTED)
        if not v12_day:
            return _stop(f"STOP. V12 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
        day_rows = list(v12_day.get("rows") or [])
        v12_by_day[day] = day_rows
        v12_rows.extend(day_rows)
    leak["TREND_BIT_MISMATCH_N"] = mismatch
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    if len(v8_sigs) != int(B1_SIGNAL_N_EXPECTED) or len(v12_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop(
            f"STOP. Signal n v8={len(v8_sigs)} v12={len(v12_rows)} expected {B1_SIGNAL_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v14_sha=v14_sha,
        )

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
        return _stop(f"STOP. Unfilled n={unfilled_n}.", pre=pre, leak=leak, parent_sha=parent_sha, v14_sha=v14_sha)
    identity_ok = bool(stack_ok and sig_ok and elig_ok and fill_ok)

    path_rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_fills = [r for r in v12_by_day[day] if r.get("executable_signal") and _e4(r).get("filled")]
        cache_path = V14_CACHE / f"day_{day}.json"
        cached = load_v14_day_cache(cache_path, v14_sha)
        if cached and len(list(cached.get("rows") or [])) == len(day_fills):
            print(f"{day} v14 cache hit fills={len(day_fills)}", flush=True)
            path_rows.extend(list(cached.get("rows") or []))
            for k, v in dict(cached.get("leak") or {}).items():
                if k in leak:
                    leak[k] = int(leak.get(k) or 0) + int(v or 0)
            continue
        body = replay_v14_day(
            {"date": day, "capture_path": cap.get("capture_path"), "fills": day_fills, "spec_sha": v14_sha}
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. V14 path replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v14_sha=v14_sha,
            )
        save_v14_day_cache(cache_path, body)
        path_rows.extend(list(body.get("rows") or []))
        for k, v in dict(body.get("leak") or {}).items():
            if k in leak:
                leak[k] = int(leak.get(k) or 0) + int(v or 0)

    if len(path_rows) != int(E4_FILLED_N_EXPECTED):
        return _stop(
            f"STOP. Path rows {len(path_rows)} != {E4_FILLED_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v14_sha=v14_sha,
        )

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS") and int(leak.get("PRE_FILL_EVENT_N") or 0) == 0)

    tax = taxonomy(path_rows)
    hold = hold_benchmark(path_rows)
    packs = {eid: event_pack(path_rows, eid, integrity_ok=integ_ok) for eid in EVENT_IDS}
    dec = decision_case(identity_ok=identity_ok, integrity_ok=integ_ok, tax=tax, packs=packs)

    t180 = tax.get(180) or {}
    t300 = tax.get(300) or {}
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V13_SPEC_SHA256": V13_SPEC_SHA256_EXPECTED,
        "V14_SPEC_SHA256": v14_sha,
        "ENTRY_STACK_PARITY": bool(stack_ok),
        "E4_FILL_SET_HASH_PARITY": bool(fill_ok),
        "SIGNAL_SET_HASH": sig_hash,
        "ELIGIBLE_SET_HASH": elig_hash,
        "E4_FILL_SET_HASH": fill_hash,
        "FILLED_N": len(path_rows),
        "HOLD_60": hold.get(60),
        "HOLD_180": hold.get(180),
        "HOLD_300": hold.get(300),
        "P1_180": t180.get("P1_NEVER_POSITIVE"),
        "P2_180": t180.get("P2_PROFIT_TO_LOSS"),
        "P3_180": t180.get("P3_LOSS_TO_RECOVERY"),
        "P4_180": t180.get("P4_FINAL_POSITIVE"),
        "P1_300": t300.get("P1_NEVER_POSITIVE"),
        "P2_300": t300.get("P2_PROFIT_TO_LOSS"),
        "P3_300": t300.get("P3_LOSS_TO_RECOVERY"),
        "P4_300": t300.get("P4_FINAL_POSITIVE"),
        "PRIMARY_EXIT_PROBLEM_180": t180.get("PRIMARY_EXIT_PROBLEM"),
        "PRIMARY_EXIT_PROBLEM_300": t300.get("PRIMARY_EXIT_PROBLEM"),
        "PRIMARY_EXIT_PROBLEM": tax.get("PRIMARY_EXIT_PROBLEM"),
        "D1": required_event_block(packs["D1_T3_CONTEXT_LOST"]),
        "D2": required_event_block(packs["D2_RCI_RELAPSE_MINUS80"]),
        "D3": required_event_block(packs["D3_BB_LOWER_CLOSE_BREACH"]),
        "EXIT_MECHANISM_SUPPORTED_LIST": dec.get("EXIT_MECHANISM_SUPPORTED_LIST"),
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
        "events": packs,
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
                "V14_SPEC_SHA256": v14_sha,
                "ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
                "CHALLENGER": DEVELOPMENT_CHALLENGER,
                "EXIT_SPEC_FROZEN": False,
                "NO_THRESHOLD_SEARCH": True,
                "C14": False,
            }
        ),
        "Identity": kv_rows(
            {
                "ENTRY_STACK_PARITY": stack_ok,
                "SIGNAL_PARITY": sig_ok,
                "ELIGIBLE_PARITY": elig_ok,
                "E4_FILL_SET_HASH_PARITY": fill_ok,
                "SIGNAL_SET_HASH": sig_hash,
                "ELIGIBLE_SET_HASH": elig_hash,
                "E4_FILL_SET_HASH": fill_hash,
                "FILLED_N": len(path_rows),
            }
        ),
        "Hold": kv_rows({f"H{h}_{k}": (hold.get(h) or {}).get(k) for h in (60, 180, 300) for k in ("mean", "median", "positive_rate", "POSITIVE_DAY_N", "NEGATIVE_DAY_N", "EX_BEST", "EX_TOP3", "DROP_TOP_SYMBOL", "DROP_TOP3_SYMBOL")}),
        "Taxonomy": kv_rows(
            {
                "PRIMARY_EXIT_PROBLEM": tax.get("PRIMARY_EXIT_PROBLEM"),
                **{f"{k}_{h}": (tax.get(h) or {}).get(k) for h in (180, 300) for k in ("P1_NEVER_POSITIVE", "P2_PROFIT_TO_LOSS", "P3_LOSS_TO_RECOVERY", "P4_FINAL_POSITIVE", "ZERO", "PRIMARY_EXIT_PROBLEM")},
            }
        ),
        "D1": [event_summary_row(packs["D1_T3_CONTEXT_LOST"], 180), event_summary_row(packs["D1_T3_CONTEXT_LOST"], 300)],
        "D2": [event_summary_row(packs["D2_RCI_RELAPSE_MINUS80"], 180), event_summary_row(packs["D2_RCI_RELAPSE_MINUS80"], 300)],
        "D3": [event_summary_row(packs["D3_BB_LOWER_CLOSE_BREACH"], 180), event_summary_row(packs["D3_BB_LOWER_CLOSE_BREACH"], 300)],
        "Trades": [flatten_trade(r) for r in path_rows] or [{"empty": True}],
        "Reporting": kv_rows(reporting),
        "Integrity": kv_rows(leak),
        "Non_Interference": kv_rows(
            {
                **{f"{k}_BEFORE": pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT")},
                **{f"{k}_AFTER": post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT")},
                **reporting,
                "NON_INTERFERENCE_PASS": ni_ok,
            }
        ),
    }
    write_artifacts(report, sheets)
    v13_after = _load(V13_OUT / "report.json")
    if str((v13_after.get("required") or {}).get("DEVELOPMENT_ENTRY_STACK") or "") != DEVELOPMENT_ENTRY_STACK:
        print("STOP. V13 official stack mutated.", flush=True)
        return 2
    print(
        f"DONE verdict={req.get('VERDICT')} problem={req.get('PRIMARY_EXIT_PROBLEM')} "
        f"p2_180={req.get('P2_180')} p1_180={req.get('P1_180')} supported={req.get('EXIT_MECHANISM_SUPPORTED_LIST')} "
        f"ni={ni_ok} out={V14_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
