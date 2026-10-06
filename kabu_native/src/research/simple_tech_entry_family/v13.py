"""Offline SIMPLE_TECH V13 E4 structure verification. Preserve V12 official results. No EXIT."""
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
from research.simple_tech_entry_family.isolation import (
    TODAY,
    V8_OUT,
    V10_OUT,
    V11_OUT,
    V12_OUT,
    V13_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v3_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_entry_family.v8_harvest import V8_CACHE
from research.simple_tech_entry_family.v9_harvest import attach_bits
from research.simple_tech_entry_family.v11_harvest import is_b1
from research.simple_tech_entry_family.v12_harvest import V12_CACHE
from research.simple_tech_entry_family.v13_analyze import (
    audit_aggregate_match,
    build_e4_policy,
    causal_audit,
    eligible_tuples,
    fill_rows,
    fill_tuples,
    freeze_decision,
    horizon_unfilled_audit,
    independent_e4_match,
    numeric_parity,
    read_v12_audit_e4,
    reporting_semantics,
    set_hash,
    signal_tuples,
    v12_official_preserved,
)
from research.simple_tech_entry_family.v13_harvest import (
    V13_CACHE,
    load_v13_day_cache,
    replay_e4_day,
    save_v13_day_cache,
)
from research.simple_tech_entry_family.v13_publish import REQUIRED_KEYS, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v13_spec import (
    ANALYSIS_ID,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    B1_EXECUTABLE_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    BB_CHANGED,
    BOARD_HARD_VETO,
    C14_USED_FOR_SELECTION,
    DEVELOPMENT_CHALLENGER,
    E4_FILLED_N_EXPECTED,
    EMA_CHANGED,
    ENTRY_CERTIFIED,
    ENTRY_RULE_CHANGED,
    ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH,
    EXIT_IMPLEMENTED,
    EXTRA_WAIT_SEARCH,
    FALLBACK_MARKET,
    INVERSE_BOARD_GATE,
    MIXED_TF_STRATEGY,
    NEW_INDICATOR,
    NEW_PERFORMANCE_GATE,
    NEW_TIMEFRAME,
    OPTIMISTIC_TOUCH_FILL,
    PA_RESTORED,
    PERSISTENCE_ADDED,
    PQ3_HARD_GATE,
    PROFIT_RANKING,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CANDIDATE,
    SIGNAL_RULE_CHANGED,
    SPREAD_THRESHOLD_GATE,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V8_SPEC_SHA256_EXPECTED,
    V10_SPEC_SHA256_EXPECTED,
    V11_SPEC_SHA256_EXPECTED,
    V12_SELECTED_POLICY,
    V12_SPEC_SHA256_EXPECTED,
    V12_VERDICT_EXPECTED,
    V12_SELECTION_SEMANTICS_AMBIGUOUS,
    VOLUME_RESTORED,
    canonical_v13_spec,
    spec_sha256_v13,
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
    "EXIT_SIM_N",
    "ENTRY_RULE_CHANGE_N",
    "THRESHOLD_SEARCH_N",
    "EMA_CHANGE_N",
    "BB_CHANGE_N",
    "RCI_CHANGE_N",
    "PROFIT_RANKING_N",
    "NEW_PERFORMANCE_GATE_N",
    "V12_WRITE_N",
    "OPTIMISTIC_FILL_N",
    "TOUCH_FILL_N",
    "QUEUE_FILL_N",
    "REPRICE_N",
    "CHASE_N",
    "FALLBACK_MARKET_N",
    "EXTRA_WAIT_ARM_N",
    "SPREAD_GATE_N",
    "SIGNAL_RECAPTURE_N",
    "SIGNAL_RULE_CHANGE_N",
    "NEW_INDICATOR_N",
    "NEW_TF_N",
    "MIXED_TF_STRATEGY_N",
    "ML_USE_N",
    "PM_ROWS_USED_N",
    "ASK_RUNTIME_ADOPTION_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v13_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_V13_ENTRY_STRUCTURE_VERIFICATION_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "ENTRY_CERTIFIED": False,
        "NON_INTERFERENCE_PASS": False,
        "V12_OFFICIAL_VERDICT_PRESERVED": False,
        "V12_SELECTION_SEMANTICS_AMBIGUOUS": True,
        "ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT": False,
        "ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT": False,
        "DEVELOPMENT_ENTRY_STACK": None,
        "PARENT_SPEC_SHA256": parent_sha,
        "V13_SPEC_SHA256": v13_sha,
        "V12_SPEC_SHA256": V12_SPEC_SHA256_EXPECTED,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "extra": extra or {},
        "_markdown": build_markdown({"required": req}),
    }
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V13_SPEC_SHA256": v13_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v13_spec()
    v13_sha = spec_sha256_v13(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} v13={v13_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or ASK_RUNTIME_ADOPTION_ALLOWED
        or EXIT_IMPLEMENTED
        or ENTRY_RULE_CHANGED
        or THRESHOLD_SEARCH
        or C14_USED_FOR_SELECTION
        or EMA_CHANGED
        or BB_CHANGED
        or RCI_CHANGED
        or PA_RESTORED
        or VOLUME_RESTORED
        or PERSISTENCE_ADDED
        or PQ3_HARD_GATE
        or BOARD_HARD_VETO
        or INVERSE_BOARD_GATE
        or NEW_INDICATOR
        or NEW_TIMEFRAME
        or MIXED_TF_STRATEGY
        or SIGNAL_RULE_CHANGED
        or SPREAD_THRESHOLD_GATE
        or EXTRA_WAIT_SEARCH
        or FALLBACK_MARKET
        or OPTIMISTIC_TOUCH_FILL
        or PROFIT_RANKING
        or NEW_PERFORMANCE_GATE
        or ENTRY_CERTIFIED
        or RUNTIME_CANDIDATE
        or (not ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH)
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)

    v8_req = dict((_load(V8_OUT / "report.json").get("required") or {}))
    if str(v8_req.get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    v10_req = dict((_load(V10_OUT / "report.json").get("required") or {}))
    if str(v10_req.get("V10_SPEC_SHA256") or "") != V10_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V10 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    if str(v10_req.get("SELECTED_STACK") or "") != "T3_PULLBACK_RCI":
        return _stop("STOP. V10 selected stack drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    v11_req = dict((_load(V11_OUT / "report.json").get("required") or {}))
    if str(v11_req.get("V11_SPEC_SHA256") or "") != V11_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V11 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)

    v12_body = _load(V12_OUT / "report.json")
    v12_req = dict(v12_body.get("required") or {})
    v12_dec = dict(v12_body.get("decision") or {})
    preserved = v12_official_preserved(v12_req, v12_dec)
    if not preserved.get("V12_OFFICIAL_VERDICT_PRESERVED"):
        return _stop("STOP. V12 official verdict/selection lock failed.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha, extra=preserved)
    if str(v12_req.get("SELECTED_EXECUTION_POLICY") or "") != V12_SELECTED_POLICY:
        return _stop("STOP. V12 selected policy must remain E1_BID_W5.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    if str(v12_req.get("VERDICT") or "") != V12_VERDICT_EXPECTED:
        return _stop("STOP. V12 verdict must remain CASE B insufficient.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps] + [str(V12_CACHE), str(V12_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)

    v8_sigs: list[dict[str, Any]] = []
    v12_rows: list[dict[str, Any]] = []
    v12_by_day: dict[str, list[dict[str, Any]]] = {}
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            if is_b1(rec):
                v8_sigs.append(rec)
        v12_day = load_day_cache(V12_CACHE / f"day_{day}.json", V12_SPEC_SHA256_EXPECTED)
        if not v12_day:
            return _stop(f"STOP. V12 cache missing or SHA drifted {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
        day_rows = list(v12_day.get("rows") or [])
        v12_by_day[day] = day_rows
        v12_rows.extend(day_rows)
    leak["TREND_BIT_MISMATCH_N"] = mismatch
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v13_sha=v13_sha)
    if len(v8_sigs) != int(B1_SIGNAL_N_EXPECTED) or len(v12_rows) != int(B1_SIGNAL_N_EXPECTED):
        return _stop(
            f"STOP. Signal n v8={len(v8_sigs)} v12={len(v12_rows)} expected {B1_SIGNAL_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v13_sha=v13_sha,
        )

    replay_rows: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        day_rows = v12_by_day[day]
        cache_path = V13_CACHE / f"day_{day}.json"
        cached = load_v13_day_cache(cache_path, v13_sha)
        if cached and len(list(cached.get("rows") or [])) == len(day_rows):
            print(f"{day} v13 e4 cache hit rows={len(day_rows)}", flush=True)
            replay_rows.extend(list(cached.get("rows") or []))
            continue
        body = replay_e4_day(
            {
                "date": day,
                "capture_path": cap.get("capture_path"),
                "signals": day_rows,
                "spec_sha": v13_sha,
            }
        )
        if not body.get("ok"):
            return _stop(
                f"STOP. E4 replay failed {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v13_sha=v13_sha,
            )
        save_v13_day_cache(cache_path, body)
        replay_rows.extend(list(body.get("rows") or []))

    sig_v8 = signal_tuples(v8_sigs)
    sig_v12 = signal_tuples(v12_rows)
    sig_rep = signal_tuples(replay_rows)
    signal_parity = sig_v8 == sig_v12 == sig_rep
    exe_v12 = [r for r in v12_rows if r.get("executable_signal")]
    exe_rep = [r for r in replay_rows if r.get("executable_signal")]
    eligible_parity = len(exe_v12) == int(B1_EXECUTABLE_N_EXPECTED) == len(exe_rep)
    fills_v12 = fill_tuples(exe_v12)
    fills_rep = fill_tuples(exe_rep)
    replay_id = independent_e4_match(v12_rows, replay_rows)
    e4_fill_parity = (
        len(fills_v12) == int(E4_FILLED_N_EXPECTED)
        and fills_v12 == fills_rep
        and bool(replay_id.get("INDEPENDENT_E4_REPLAY_MATCH"))
    )
    sig_hash = set_hash(sig_v12)
    elig_hash = set_hash(eligible_tuples(v12_rows))
    fill_hash = set_hash(fills_v12)
    fill_hash_rep = set_hash(fills_rep)

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS"))

    e4 = build_e4_policy(exe_rep, signal_n=len(replay_rows), integrity_ok=integ_ok)
    causal = causal_audit(exe_rep)
    horizon = horizon_unfilled_audit(e4)
    nums = numeric_parity(e4)
    trades = fill_rows(exe_rep)
    audit_info = read_v12_audit_e4(V12_OUT / "audit.xlsx")
    audit_match = audit_aggregate_match(e4, audit_info)
    identity_vs_audit = bool(audit_match.get("V12_AUDIT_E4_AGGREGATE_MATCH"))

    all_pass = bool(
        preserved.get("V12_OFFICIAL_VERDICT_PRESERVED")
        and signal_parity
        and eligible_parity
        and e4_fill_parity
        and identity_vs_audit
        and causal.get("CAUSAL_FILL_AUDIT_PASS")
        and horizon.get("HORIZON_SEMANTICS_PASS")
        and horizon.get("UNFILLED_ZERO_PASS")
        and nums.get("E4_NUMERIC_PARITY")
        and reporting.get("REPORTING_SEMANTICS_PASS")
        and integ_ok
        and fill_hash == fill_hash_rep
    )
    decision = freeze_decision(all_pass=all_pass)

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V12_SPEC_SHA256": V12_SPEC_SHA256_EXPECTED,
        "V13_SPEC_SHA256": v13_sha,
        "V12_OFFICIAL_VERDICT_PRESERVED": bool(preserved.get("V12_OFFICIAL_VERDICT_PRESERVED")),
        "V12_SELECTION_SEMANTICS_AMBIGUOUS": bool(V12_SELECTION_SEMANTICS_AMBIGUOUS),
        "DEVELOPMENT_CHALLENGER": DEVELOPMENT_CHALLENGER,
        "V12_SELECTED_POLICY": V12_SELECTED_POLICY,
        "SIGNAL_PARITY": bool(signal_parity),
        "ELIGIBLE_PARITY": bool(eligible_parity),
        "E4_FILL_PARITY": bool(e4_fill_parity and identity_vs_audit),
        "SIGNAL_SET_HASH": sig_hash,
        "ELIGIBLE_SET_HASH": elig_hash,
        "E4_FILL_SET_HASH": fill_hash,
        "CAUSAL_FILL_AUDIT_PASS": bool(causal.get("CAUSAL_FILL_AUDIT_PASS")),
        "HORIZON_SEMANTICS_PASS": bool(horizon.get("HORIZON_SEMANTICS_PASS")),
        "UNFILLED_ZERO_PASS": bool(horizon.get("UNFILLED_ZERO_PASS")),
        "E4_NUMERIC_PARITY": bool(nums.get("E4_NUMERIC_PARITY")),
        "REPORTING_SEMANTICS_PASS": bool(reporting.get("REPORTING_SEMANTICS_PASS")),
        "ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT": bool(decision.get("ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT")),
        "ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT": bool(decision.get("ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT")),
        "DEVELOPMENT_ENTRY_STACK": decision.get("DEVELOPMENT_ENTRY_STACK"),
        "TRUE_OOS": bool(TRUE_OOS),
        "ENTRY_CERTIFIED": False,
        "RUNTIME_CANDIDATE": False,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    slim_e4 = {k: v for k, v in e4.items() if k not in {"daily_uncond", "daily_uncond_bid"}}
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "v12_official": preserved,
        "challenger": DEVELOPMENT_CHALLENGER,
        "e4": slim_e4,
        "identity": {
            "signal_n_v8": len(v8_sigs),
            "signal_n_v12": len(v12_rows),
            "signal_n_replay": len(replay_rows),
            "eligible_n": len(exe_v12),
            "eligible_n_replay": len(exe_rep),
            "filled_n": len(fills_v12),
            "filled_n_replay": len(fills_rep),
            "SIGNAL_SET_HASH": sig_hash,
            "ELIGIBLE_SET_HASH": elig_hash,
            "E4_FILL_SET_HASH": fill_hash,
            "E4_FILL_SET_HASH_REPLAY": fill_hash_rep,
            "v12_audit": audit_match,
            "independent_replay": {k: v for k, v in replay_id.items() if k != "details"} | {"details": replay_id.get("details") or []},
        },
        "causal": causal,
        "horizon": horizon,
        "numeric": nums,
        "reporting": reporting,
        "decision": decision,
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
                "V13_SPEC_SHA256": v13_sha,
                "V12_SPEC_SHA256": V12_SPEC_SHA256_EXPECTED,
                "CHALLENGER": DEVELOPMENT_CHALLENGER,
                "V12_SELECTED": V12_SELECTED_POLICY,
                "V12_CASE": "B",
                "V12_SELECTION_SEMANTICS_AMBIGUOUS": True,
                "NO_PROFIT_RANKING": True,
                "EXIT": False,
            }
        ),
        "V12_Preserved": kv_rows(preserved),
        "Identity": kv_rows(
            {
                "SIGNAL_PARITY": signal_parity,
                "ELIGIBLE_PARITY": eligible_parity,
                "E4_FILL_PARITY": e4_fill_parity and identity_vs_audit,
                "SIGNAL_SET_HASH": sig_hash,
                "ELIGIBLE_SET_HASH": elig_hash,
                "E4_FILL_SET_HASH": fill_hash,
                "E4_FILL_SET_HASH_REPLAY": fill_hash_rep,
                "INDEPENDENT_E4_REPLAY_MATCH": replay_id.get("INDEPENDENT_E4_REPLAY_MATCH"),
                "REPLAY_MISMATCH_N": replay_id.get("mismatch_n"),
                **{k: v for k, v in audit_match.items() if k != "checks"},
            }
        ),
        "Replay_Identity": kv_rows({k: v for k, v in replay_id.items() if k != "details"})
        if not (replay_id.get("details") or [])
        else (replay_id.get("details") or [{"empty": True}]),
        "E4_Fills": trades or [{"empty": True}],
        "Causal_Audit": kv_rows({k: v for k, v in causal.items() if k != "details"}),
        "Numeric_Parity": kv_rows(nums.get("checks") or {}),
        "Reporting": kv_rows(reporting),
        "Integrity": kv_rows({**leak, "E4_FILL_PARITY": e4_fill_parity, "V12_WRITE_N": 0}),
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
    v12_after = _load(V12_OUT / "report.json")
    if str((v12_after.get("required") or {}).get("VERDICT") or "") != V12_VERDICT_EXPECTED:
        leak["V12_WRITE_N"] = 1
        print("STOP. V12 official verdict mutated.", flush=True)
        return 2
    print(
        f"DONE verdict={req.get('VERDICT')} freeze_signal={req.get('ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT')} "
        f"freeze_exec={req.get('ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT')} e4_parity={req.get('E4_NUMERIC_PARITY')} "
        f"causal={req.get('CAUSAL_FILL_AUDIT_PASS')} replay={replay_id.get('INDEPENDENT_E4_REPLAY_MATCH')} "
        f"ni={ni_ok} out={V13_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
