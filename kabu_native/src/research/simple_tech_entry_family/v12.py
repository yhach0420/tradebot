"""Offline SIMPLE_TECH V12 ENTRY execution architecture. Frozen B1. Ask-cross fill. No EXIT."""
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
    advanced as ni_advanced,
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
from research.simple_tech_entry_family.v11_spec import ANALYSIS_ID as V11_ANALYSIS_ID
from research.simple_tech_entry_family.v12_analyze import (
    _arm_from,
    _remap,
    decide_case,
    edge_repaired,
    family_mechanism,
    policy_metrics,
    primary_deficiency,
    select_policy,
)
from research.simple_tech_entry_family.v12_harvest import V12_CACHE, process_v12_day, save_v12_day_cache
from research.simple_tech_entry_family.v12_publish import (
    REQUIRED_KEYS,
    build_markdown,
    kv_rows,
    policy_sheet_row,
    write_artifacts,
)
from research.simple_tech_entry_family.v12_spec import (
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
    CONTROL_ARM,
    EMA_CHANGED,
    ENTRY_RULE_CHANGED,
    ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH,
    EXIT_IMPLEMENTED,
    EXTRA_WAIT_SEARCH,
    FALLBACK_MARKET,
    INVERSE_BOARD_GATE,
    MIXED_TF_STRATEGY,
    NEW_INDICATOR,
    NEW_TIMEFRAME,
    OPTIMISTIC_TOUCH_FILL,
    PA_RESTORED,
    PERSISTENCE_ADDED,
    POLICY_ARMS,
    PQ3_HARD_GATE,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    SIGNAL_RULE_CHANGED,
    SPREAD_THRESHOLD_GATE,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V8_SPEC_SHA256_EXPECTED,
    V10_SPEC_SHA256_EXPECTED,
    V11_SPEC_SHA256_EXPECTED,
    V11_VERDICT_EXPECTED,
    VOLUME_RESTORED,
    canonical_v12_spec,
    spec_sha256_v12,
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
    "SPREAD_GATE_N",
    "EXTRA_WAIT_ARM_N",
    "REPRICE_N",
    "CHASE_N",
    "FALLBACK_MARKET_N",
    "OPTIMISTIC_FILL_N",
    "TOUCH_FILL_N",
    "QUEUE_FILL_N",
    "ASK_CROSS_LIMIT_N",
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


def _e0_full_mean(exe: list[dict[str, Any]], h: int) -> Any:
    xs = []
    for r in exe:
        ex = (r.get("exec") or {}).get(CONTROL_ARM) or {}
        v = ex.get(f"sec_bid_{int(h)}")
        if v is None:
            v = r.get(f"full_{int(h)}")
        if v is not None:
            xs.append(float(v))
    if not xs:
        return None
    return float(sum(xs) / len(xs))


def _slim_policy(p: dict[str, Any]) -> dict[str, Any]:
    skip = {"daily_uncond", "daily_uncond_bid"}
    return {k: v for k, v in p.items() if k not in skip}


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v12_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_V12_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH": True,
        "E0_PARITY": False,
        "PASSIVE_BID_MECHANISM_SUPPORTED": False,
        "INSIDE1_MECHANISM_SUPPORTED": False,
        "ENTRY_EXECUTION_MECHANISM_SUPPORTED": False,
        "ENTRY_EXECUTION_EDGE_REPAIRED": False,
        "SELECTED_EXECUTION_POLICY": None,
        "PRIMARY_EXECUTION_DEFICIENCY": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V12_SPEC_SHA256": v12_sha,
        "V11_SPEC_SHA256": V11_SPEC_SHA256_EXPECTED,
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
    write_artifacts(
        report,
        {
            "Precommit": kv_rows({"blocker": msg, "V12_SPEC_SHA256": v12_sha}),
            "Integrity": kv_rows(leak),
            "Non_Interference": kv_rows(pre),
        },
    )
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v12_spec()
    v12_sha = spec_sha256_v12(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} parent={parent_sha[:12]} v12={v12_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
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
        or (not ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH)
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)

    v8_req = dict((_load(V8_OUT / "report.json").get("required") or {}))
    if str(v8_req.get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if v8_req.get("PRICE_ACTION_HARD_ROLE_SUPPORTED") is not False or v8_req.get("VOLUME_HARD_ROLE_SUPPORTED") is not False:
        return _stop("STOP. V8 PA/Volume must remain unsupported.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)

    v10_req = dict((_load(V10_OUT / "report.json").get("required") or {}))
    if str(v10_req.get("V10_SPEC_SHA256") or "") != V10_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V10 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if str(v10_req.get("SELECTED_STACK") or "") != "T3_PULLBACK_RCI":
        return _stop("STOP. V10 selected stack must remain T3_PULLBACK_RCI.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if v10_req.get("RCI_INCREMENTAL_ROLE_SUPPORTED") is not True:
        return _stop("STOP. V10 RCI incremental must remain true.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if v10_req.get("BOARD_VETO_HARMFUL") is not True:
        return _stop("STOP. V10 Board veto harmful must remain true.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)

    v11_req = dict((_load(V11_OUT / "report.json").get("required") or {}))
    if str(v11_req.get("V11_SPEC_SHA256") or "") != V11_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V11 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if str(v11_req.get("VERDICT") or "") != V11_VERDICT_EXPECTED:
        return _stop("STOP. V11 verdict lock failed.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if str(v11_req.get("ANALYSIS_ID") or "") != V11_ANALYSIS_ID:
        return _stop("STOP. V11 analysis id lock failed.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)

    V12_CACHE.mkdir(parents=True, exist_ok=True)
    b1_n = 0
    quote_rows: list[dict[str, Any]] = []
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        v8_body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not v8_body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
        sigs = []
        for r in list(v8_body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            if is_b1(rec):
                sigs.append(rec)
        b1_n += len(sigs)
        cache_p = V12_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, v12_sha)
        if not body:
            print(f"{day} v12 execution replay start n={len(sigs)}", flush=True)
            body = process_v12_day(
                {
                    "date": day,
                    "capture_path": cap["capture_path"],
                    "signals": sigs,
                    "spec_sha": v12_sha,
                }
            )
            if body.get("ok"):
                save_v12_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(f"STOP. Execution replay failed {day}: {body.get('blocker')}", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
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
            "SPREAD_GATE_N",
            "EXTRA_WAIT_ARM_N",
            "REPRICE_N",
            "CHASE_N",
            "FALLBACK_MARKET_N",
            "OPTIMISTIC_FILL_N",
            "TOUCH_FILL_N",
            "QUEUE_FILL_N",
            "FUTURE_ASK_USE_N",
            "MIDPOINT_ENTRY_N",
            "FUTURE_BOARD_N",
            "NO_ASK0_N",
            "MID0_MISS_N",
            "FULL_MISS_N",
            "GROSS_MISS_N",
            "DUAL_BID_NE_EXEC_BID_N",
            "NOT_ELIGIBLE_N",
            "INSIDE_COLLAPSE_N",
            "ASK_CROSS_LIMIT_N",
            "E0_FILL_N",
            "ASK_CROSS_FILL_N",
            "INSIDE_ASK_CROSS_FILL_N",
        ):
            leak[k] = int(leak.get(k) or 0) + int(lk.get(k) or 0)

    leak["TREND_BIT_MISMATCH_N"] = mismatch
    leak["SIGNAL_RULE_CHANGE_N"] = 0
    leak["NEW_INDICATOR_N"] = 0
    leak["PQ3_GATE_N"] = 0
    leak["PERSISTENCE_AND_N"] = 0
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)
    if b1_n != int(B1_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. B1 signal n={b1_n} expected {B1_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v12_sha=v12_sha)

    exe = [r for r in quote_rows if r.get("executable_signal")]
    if len(exe) != int(B1_EXECUTABLE_N_EXPECTED):
        return _stop(
            f"STOP. B1 executable n={len(exe)} expected {B1_EXECUTABLE_N_EXPECTED}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v12_sha=v12_sha,
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

    e0_full = {h: _e0_full_mean(exe, h) for h in (60, 180, 300)}
    e0_parity = bool(
        _close(e0_full[60], B1_MARKOUT_60_EXPECTED)
        and _close(e0_full[180], B1_MARKOUT_180_EXPECTED)
        and _close(e0_full[300], B1_MARKOUT_300_EXPECTED)
    )
    if not e0_parity:
        return _stop(
            f"STOP. E0 full executable markout parity failed 60={e0_full[60]} 180={e0_full[180]} 300={e0_full[300]}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v12_sha=v12_sha,
            extra={"e0_full": e0_full},
        )

    e0 = policy_metrics(exe, CONTROL_ARM, signal_n=b1_n, e0_uncond=None, integrity_ok=integ_ok)
    policies: dict[str, Any] = {CONTROL_ARM: e0}
    e0_uncond_full = _arm_from(_remap(exe, CONTROL_ARM, field="prim_mid", uncond=True), CONTROL_ARM, signal_n=b1_n, eligible_n=len(exe))
    for aid, _fam, _wait in POLICY_ARMS:
        policies[aid] = policy_metrics(exe, aid, signal_n=b1_n, e0_uncond=e0_uncond_full, integrity_ok=integ_ok)

    bid_fam = family_mechanism(policies, "PASSIVE_BID")
    inside_fam = family_mechanism(policies, "IMPROVE_1TICK")
    passing = list(bid_fam.get("PASSING_POLICIES") or []) + list(inside_fam.get("PASSING_POLICIES") or [])
    selected = select_policy(passing, policies)
    mechanism = bool(bid_fam.get("SUPPORTED") or inside_fam.get("SUPPORTED"))
    selected_pol = policies.get(selected) if selected else None
    repaired = edge_repaired(selected_pol, coverage_ok=bool(selected_pol and selected_pol.get("COVERAGE_OK")), integrity_ok=integ_ok)
    decision = decide_case(
        integrity_ok=integ_ok,
        optimistic_n=int(leak.get("OPTIMISTIC_FILL_N") or 0) + int(leak.get("TOUCH_FILL_N") or 0) + int(leak.get("QUEUE_FILL_N") or 0),
        bid_fam=bid_fam,
        inside_fam=inside_fam,
        policies={k: v for k, v in policies.items() if k != CONTROL_ARM},
        selected=selected,
        repaired=repaired,
        mechanism=mechanism,
    )
    deficiency = primary_deficiency(
        case=str(decision.get("CASE") or "E"),
        bid_fam=bid_fam,
        inside_fam=inside_fam,
        policies={k: v for k, v in policies.items() if k != CONTROL_ARM},
        repaired=repaired,
    )

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V11_SPEC_SHA256": V11_SPEC_SHA256_EXPECTED,
        "V12_SPEC_SHA256": v12_sha,
        "ENTRY_SIGNAL_STACK_FROZEN_FOR_EXECUTION_RESEARCH": True,
        "E0_PARITY": True,
        "E0_FULL_60": e0_full[60],
        "E0_FULL_180": e0_full[180],
        "E0_FULL_300": e0_full[300],
        "PASSIVE_BID_MECHANISM_SUPPORTED": bool(bid_fam.get("SUPPORTED")),
        "INSIDE1_MECHANISM_SUPPORTED": bool(inside_fam.get("SUPPORTED")),
        "ENTRY_EXECUTION_MECHANISM_SUPPORTED": bool(mechanism),
        "ENTRY_EXECUTION_EDGE_REPAIRED": bool(repaired.get("ENTRY_EXECUTION_EDGE_REPAIRED")),
        "SELECTED_EXECUTION_POLICY": selected,
        "PRIMARY_EXECUTION_DEFICIENCY": deficiency,
        "TRUE_OOS": bool(TRUE_OOS),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    pub_policies = {k: _slim_policy(v) for k, v in policies.items()}
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "b1_signal_n": b1_n,
        "b1_executable_n": len(exe),
        "e0_full": e0_full,
        "policies": pub_policies,
        "passive_bid_family": bid_fam,
        "inside1_family": inside_fam,
        "edge_repair": repaired,
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
    for aid, p in policies.items():
        for rec in p.get("daily_uncond") or []:
            daily.append({"arm": aid, "kind": "UNCOND_MID", **rec})
        for rec in p.get("daily_uncond_bid") or []:
            daily.append({"arm": aid, "kind": "UNCOND_BID", **rec})
    wait_band = [bid_fam, inside_fam]
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "V12_SPEC_SHA256": v12_sha,
                "V11_SPEC_SHA256": V11_SPEC_SHA256_EXPECTED,
                "TF": "TF1",
                "STACK": "T3_PULLBACK_RCI",
                "BOARD_HARD_VETO": False,
                "INVERSE_BOARD_GATE": False,
                "PA_RESTORED": False,
                "VOLUME_RESTORED": False,
                "SIGNAL_RULE_CHANGED": False,
                "EXTRA_WAIT_SEARCH": False,
                "OPTIMISTIC_TOUCH_FILL": False,
                "FALLBACK_MARKET": False,
                "C14": False,
                "EXIT": False,
            }
        ),
        "E0_Parity": kv_rows(
            {
                "E0_PARITY": True,
                "FULL_60": e0_full[60],
                "FULL_180": e0_full[180],
                "FULL_300": e0_full[300],
                "EXPECTED_60": B1_MARKOUT_60_EXPECTED,
                "EXPECTED_180": B1_MARKOUT_180_EXPECTED,
                "EXPECTED_300": B1_MARKOUT_300_EXPECTED,
            }
        ),
        "Policies": [policy_sheet_row(policies[aid]) for aid in (CONTROL_ARM,) + tuple(a[0] for a in POLICY_ARMS)],
        "Conditional": [
            {
                "ARM_ID": aid,
                **(policies[aid].get("CONDITIONAL_FILLED_MARKOUT") or {}),
                **{f"BID_{k}": v for k, v in (policies[aid].get("CONDITIONAL_FILL_TO_BID") or {}).items()},
                **{f"FT_{k}": v for k, v in (policies[aid].get("FILL_TIME_MARKOUT_DIAG") or {}).items()},
            }
            for aid in (CONTROL_ARM,) + tuple(a[0] for a in POLICY_ARMS)
        ],
        "Unconditional": [
            {
                "ARM_ID": aid,
                **(policies[aid].get("UNCONDITIONAL_POLICY_MARKOUT") or {}),
                **{f"BID_{k}": v for k, v in (policies[aid].get("UNCONDITIONAL_FILL_TO_BID") or {}).items()},
            }
            for aid in (CONTROL_ARM,) + tuple(a[0] for a in POLICY_ARMS)
        ],
        "Missed_Opportunity": [
            {
                "ARM_ID": aid,
                "FILLED_N": policies[aid].get("FILLED_N"),
                "UNFILLED_N": policies[aid].get("UNFILLED_N"),
                **{f"FILLED_GROSS_{k}": v for k, v in (policies[aid].get("FILLED_GROSS_MID") or {}).items()},
                **{f"UNFILLED_GROSS_{k}": v for k, v in (policies[aid].get("UNFILLED_GROSS_MID") or {}).items()},
                "ADVERSE": policies[aid].get("EXTREME_ADVERSE_SELECTION"),
            }
            for aid in (CONTROL_ARM,) + tuple(a[0] for a in POLICY_ARMS)
        ],
        "Robustness": [policy_sheet_row(policies[aid]) for aid in (CONTROL_ARM,) + tuple(a[0] for a in POLICY_ARMS)],
        "Wait_Band": wait_band,
        "Daily": daily or [{"empty": True}],
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "E0_PARITY": True}),
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
        f"DONE verdict={req.get('VERDICT')} selected={selected} mech={mechanism} repaired={req.get('ENTRY_EXECUTION_EDGE_REPAIRED')} "
        f"bid={req.get('PASSIVE_BID_MECHANISM_SUPPORTED')} inside={req.get('INSIDE1_MECHANISM_SUPPORTED')} "
        f"e0_parity={e0_parity} ni={ni_ok} out={V12_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
