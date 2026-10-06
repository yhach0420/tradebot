"""Offline SIMPLE_TECH V11 TF1 signal vs execution-cost RCA. Frozen B1. Quote replay. No EXIT."""
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
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v3_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_entry_family.v8_analyze import slim_arm
from research.simple_tech_entry_family.v8_harvest import V8_CACHE
from research.simple_tech_entry_family.v8_publish import arm_row
from research.simple_tech_entry_family.v9_harvest import attach_bits
from research.simple_tech_entry_family.v11_analyze import (
    all_means_positive,
    decomp_horizon,
    decide_case,
    family_metrics,
    means_180_300_negative,
    q5_not_only_one_state,
    robust_edge,
    robust_negative,
    spread_distribution,
    spread_quartile_means,
    tod_kind,
)
from research.simple_tech_entry_family.v11_harvest import V11_CACHE, is_b1, process_v11_day, save_v11_day_cache
from research.simple_tech_entry_family.v11_publish import REQUIRED_KEYS, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v11_spec import (
    ANALYSIS_ID,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    B1_EXECUTABLE_N_EXPECTED,
    B1_MARKOUT_180_EXPECTED,
    B1_MARKOUT_300_EXPECTED,
    B1_MARKOUT_60_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    BB_CHANGED,
    BOARD_HARD_VETO,
    C14_USED_FOR_SELECTION,
    EMA_CHANGED,
    ENTRY_RULE_CHANGED,
    EXIT_IMPLEMENTED,
    INVERSE_BOARD_GATE,
    MARKOUT_KINDS,
    MIXED_TF_STRATEGY,
    NEW_INDICATOR,
    NEW_TIMEFRAME,
    PA_RESTORED,
    PERSISTENCE_ADDED,
    PQ3_HARD_GATE,
    RESEARCH_PARALLELISM,
    RCI_CHANGED,
    RUNTIME_ADOPTION_ALLOWED,
    SIGNAL_RULE_CHANGED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V8_SPEC_SHA256_EXPECTED,
    V10_SPEC_SHA256_EXPECTED,
    VOLUME_RESTORED,
    canonical_v11_spec,
    spec_sha256_v11,
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
    "PA_RESTORE_N",
    "VOLUME_RESTORE_N",
    "PERSISTENCE_AND_N",
    "PQ3_GATE_N",
    "BOARD_HARD_VETO_N",
    "INVERSE_BOARD_GATE_N",
    "SIGNAL_RECAPTURE_N",
    "SIGNAL_RULE_CHANGE_N",
    "NEW_INDICATOR_N",
    "NEW_TF_N",
    "MIXED_TF_STRATEGY_N",
    "FUTURE_ASK_USE_N",
    "MIDPOINT_ENTRY_N",
    "ML_USE_N",
    "PM_ROWS_USED_N",
    "ASK_RUNTIME_ADOPTION_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a) - float(b)) <= float(tol)
    except (TypeError, ValueError):
        return False


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v11_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_V11_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "Q1_GROSS_MID_POSITIVE_60_180_300": False,
        "Q2_EXECUTION_COST_PRIMARY": False,
        "Q3_ENTRY_CROSS_ALREADY_NEGATIVE": False,
        "Q4_SIGNAL_ARCHITECTURE_STILL_INSUFFICIENT": False,
        "Q5_RESIDUAL_NOT_ONLY_ONE_TOD_OR_SPREAD": False,
        "GROSS_MID_EDGE_ROBUST": False,
        "ENTRY_SIGNAL_EDGE_SUPPORTED": False,
        "PRIMARY_INTERPRETATION": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V11_SPEC_SHA256": v11_sha,
        "B1_PARITY": False,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {"analysis_id": ANALYSIS_ID, "blocker": msg, "required": req, "preflight": pre, "leak": leak, "extra": extra or {}, "_markdown": build_markdown({"required": req})}
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V11_SPEC_SHA256": v11_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v11_spec()
    v11_sha = spec_sha256_v11(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} parent={parent_sha[:12]} v11={v11_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
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
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)

    v8_req = dict((_load(V8_OUT / "report.json").get("required") or {}))
    if str(v8_req.get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if v8_req.get("PRICE_ACTION_HARD_ROLE_SUPPORTED") is not False or v8_req.get("VOLUME_HARD_ROLE_SUPPORTED") is not False:
        return _stop("STOP. V8 PA/Volume must remain unsupported.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)

    v10_req = dict((_load(V10_OUT / "report.json").get("required") or {}))
    if str(v10_req.get("V10_SPEC_SHA256") or "") != V10_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V10 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if str(v10_req.get("SELECTED_STACK") or "") != "T3_PULLBACK_RCI":
        return _stop("STOP. V10 selected stack must remain T3_PULLBACK_RCI.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if v10_req.get("RCI_INCREMENTAL_ROLE_SUPPORTED") is not True:
        return _stop("STOP. V10 RCI incremental must remain true.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if v10_req.get("BOARD_VETO_HARMFUL") is not True:
        return _stop("STOP. V10 Board veto harmful must remain true.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)

    V11_CACHE.mkdir(parents=True, exist_ok=True)
    b1_n = 0
    quote_rows: list[dict[str, Any]] = []
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
        sigs = []
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            if is_b1(rec):
                sigs.append(rec)
        b1_n += len(sigs)
        cache_p = V11_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, v11_sha)
        if not body:
            print(f"{day} v11 quote replay start n={len(sigs)}", flush=True)
            body = process_v11_day(
                {
                    "date": day,
                    "capture_path": cap["capture_path"],
                    "signals": sigs,
                    "spec_sha": v11_sha,
                }
            )
            if body.get("ok"):
                save_v11_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(f"STOP. Quote replay failed {day}: {body.get('blocker')}", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
        quote_rows.extend(list(body.get("rows") or []))
        lk = body.get("leak") or {}
        for k in (
            "C14_REPLAY_N",
            "EXIT_SIM_N",
            "SIGNAL_RECAPTURE_N",
            "ENTRY_RULE_CHANGE_N",
            "THRESHOLD_SEARCH_N",
            "INVERSE_BOARD_GATE_N",
            "BOARD_HARD_VETO_N",
            "PA_RESTORE_N",
            "VOLUME_RESTORE_N",
            "FUTURE_ASK_USE_N",
            "MIDPOINT_ENTRY_N",
            "FUTURE_BOARD_N",
            "NO_ASK0_N",
            "MID0_MISS_N",
            "FULL_MISS_N",
            "GROSS_MISS_N",
            "DUAL_BID_NE_EXEC_BID_N",
        ):
            leak[k] = int(leak.get(k) or 0) + int(lk.get(k) or 0)

    leak["TREND_BIT_MISMATCH_N"] = mismatch
    leak["SIGNAL_RULE_CHANGE_N"] = 0
    leak["NEW_INDICATOR_N"] = 0
    leak["PQ3_GATE_N"] = 0
    leak["PERSISTENCE_AND_N"] = 0
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)
    if b1_n != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. B1 signal n={b1_n} expected {B1_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v11_sha=v11_sha)

    exe = [r for r in quote_rows if r.get("executable_signal")]
    if len(exe) != int(B1_EXECUTABLE_N_EXPECTED):
        return _stop(
            f"STOP. B1 executable n={len(exe)} expected {B1_EXECUTABLE_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v11_sha=v11_sha,
        )

    post = snapshot(phase="POST")
    adv = ni_advanced(pre, post)
    live_before = pre.get("RUNTIME_PID") is not None or pre.get("CAPTURE_PID") is not None
    ni_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    if live_before:
        ni_ok = bool(
            ni_ok
            and adv.get("RUNTIME_PID_UNCHANGED")
            and adv.get("CAPTURE_PID_UNCHANGED")
            and (adv.get("RUNTIME_STILL_ALIVE") if pre.get("RUNTIME_PID") is not None else True)
            and (adv.get("CAPTURE_STILL_ALIVE") if pre.get("CAPTURE_PID") is not None else True)
        )
    integ_ok = bool(ni_ok)

    fam = {kind: family_metrics(exe, kind) for kind in MARKOUT_KINDS}
    full = fam["FULL_EXECUTABLE"]
    mid = fam["GROSS_MID"]
    cross = fam["ENTRY_CROSS"]
    if not (
        _close(full.get("MARKOUT60_MEAN"), B1_MARKOUT_60_EXPECTED)
        and _close(full.get("MARKOUT180_MEAN"), B1_MARKOUT_180_EXPECTED)
        and _close(full.get("MARKOUT300_MEAN"), B1_MARKOUT_300_EXPECTED)
    ):
        return _stop(
            f"STOP. B1 full executable markout parity failed 60={full.get('MARKOUT60_MEAN')} 180={full.get('MARKOUT180_MEAN')} 300={full.get('MARKOUT300_MEAN')}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v11_sha=v11_sha,
            extra={"full": slim_arm(full)},
        )

    mid_gate = robust_edge(mid, integrity_ok=integ_ok)
    full_gate = robust_edge(full, integrity_ok=integ_ok)
    q1 = all_means_positive(mid)
    q3 = means_180_300_negative(cross)
    q4 = robust_negative(mid)
    q2 = bool((bool(mid_gate.get("CORE_ENTRY_EDGE_SUPPORTED")) or q1) and means_180_300_negative(full) and (not q4))
    decomp = {str(int(h)): decomp_horizon(exe, int(h)) for h in (60, 180, 300)}
    spr = spread_distribution(exe)
    spr_q = spread_quartile_means(exe)
    tod = {kind: tod_kind(exe, kind) for kind in MARKOUT_KINDS}
    q5p = q5_not_only_one_state(tod.get("GROSS_MID") or [], spr_q)
    decision = decide_case(mid=mid, cross=cross, full=full, mid_robust=mid_gate, integrity_ok=integ_ok)

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V11_SPEC_SHA256": v11_sha,
        "B1_PARITY": True,
        "Q1_GROSS_MID_POSITIVE_60_180_300": q1,
        "Q2_EXECUTION_COST_PRIMARY": q2,
        "Q3_ENTRY_CROSS_ALREADY_NEGATIVE": q3,
        "Q4_SIGNAL_ARCHITECTURE_STILL_INSUFFICIENT": q4,
        "Q5_RESIDUAL_NOT_ONLY_ONE_TOD_OR_SPREAD": bool(q5p.get("Q5_RESIDUAL_NOT_ONLY_ONE_TOD_OR_SPREAD")),
        "GROSS_MID_EDGE_ROBUST": bool(mid_gate.get("CORE_ENTRY_EDGE_SUPPORTED")),
        "ENTRY_SIGNAL_EDGE_SUPPORTED": bool(full_gate.get("CORE_ENTRY_EDGE_SUPPORTED")),
        "PRIMARY_INTERPRETATION": decision.get("PRIMARY_INTERPRETATION"),
        "TRUE_OOS": bool(TRUE_OOS),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    pub_fam = {k: slim_arm(v) for k, v in fam.items()}
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "b1_signal_n": b1_n,
        "b1_executable_n": len(exe),
        "families": pub_fam,
        "decomposition": decomp,
        "gross_robust": mid_gate,
        "full_edge": full_gate,
        "spread": spr,
        "spread_quartiles": spr_q,
        "tod": tod,
        "q5": q5p,
        "decision": decision,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "non_interference": adv,
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)

    daily = []
    for kind in MARKOUT_KINDS:
        for rec in fam[kind].get("daily") or []:
            daily.append({"kind": kind, **rec})
    tod_rows = []
    for kind, xs in tod.items():
        for rec in xs:
            tod_rows.append(rec)
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "V11_SPEC_SHA256": v11_sha,
                "V10_SPEC_SHA256": V10_SPEC_SHA256_EXPECTED,
                "TF": "TF1",
                "STACK": "T3_PULLBACK_RCI",
                "BOARD_HARD_VETO": False,
                "INVERSE_BOARD_GATE": False,
                "PA_RESTORED": False,
                "VOLUME_RESTORED": False,
                "SIGNAL_RULE_CHANGED": False,
                "QUOTE_REPLAY": True,
                "SIGNAL_RECAPTURE": False,
                "C14": False,
                "EXIT": False,
            }
        ),
        "Markouts": [arm_row(fam[k]) | {"KIND": k} for k in MARKOUT_KINDS],
        "Decomposition": [decomp[str(h)] for h in (60, 180, 300)],
        "Robustness_Mid": kv_rows(mid_gate) + [arm_row(mid) | {"KIND": "GROSS_MID"}],
        "TOD": tod_rows or [{"empty": True}],
        "Spread": [spr] + list(spr_q),
        "Daily": daily or [{"empty": True}],
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "B1_PARITY": True}),
        "Non_Interference": kv_rows(
            {
                **{f"{k}_BEFORE": pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT")},
                **{f"{k}_AFTER": post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT")},
                **adv,
                "NON_INTERFERENCE_PASS": ni_ok,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(
        f"DONE verdict={req.get('VERDICT')} q1={q1} q2={q2} q3={q3} q4={q4} mid_robust={req.get('GROSS_MID_EDGE_ROBUST')} "
        f"full_edge={req.get('ENTRY_SIGNAL_EDGE_SUPPORTED')} b1_exe={len(exe)} ni={ni_ok} out={V11_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
