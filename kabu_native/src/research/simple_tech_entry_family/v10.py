"""Offline SIMPLE_TECH V10 TF1 RCI/Board incremental RCA. Join-only from V8. T3 reference. No EXIT."""
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
    V9_OUT,
    V10_OUT,
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v3_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_entry_family.v8_analyze import DAYS, arm_metrics, slim_arm, vs_arm
from research.simple_tech_entry_family.v8_harvest import V8_CACHE
from research.simple_tech_entry_family.v9_analyze import concentration_extra, tod_pack
from research.simple_tech_entry_family.v9_harvest import attach_bits
from research.simple_tech_entry_family.v10_analyze import (
    STACK,
    decide_case,
    edge_arm,
    pq3_diagnostic,
    selected_arm_id,
)
from research.simple_tech_entry_family.v10_harvest import ARM_ORDER, STATE_ORDER, base_ok, filter_arm, filter_state
from research.simple_tech_entry_family.v10_publish import REQUIRED_KEYS, arm_row, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v10_spec import (
    ANALYSIS_ID,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    BB_CHANGED,
    B2_EXECUTABLE_N_EXPECTED,
    B2_MARKOUT_180_EXPECTED,
    B2_MARKOUT_300_EXPECTED,
    C14_USED_FOR_SELECTION,
    CROSS_INDEPENDENT_NECESSITY_UNCONFIRMED,
    EMA_CHANGED,
    ENTRY_RULE_CHANGED,
    EXIT_IMPLEMENTED,
    MIXED_TF_STRATEGY,
    NEW_TIMEFRAME,
    PA_RESTORED,
    PERSISTENCE_ADDED,
    PQ3_HARD_GATE,
    RESEARCH_PARALLELISM,
    RCI_CHANGED,
    RUNTIME_ADOPTION_ALLOWED,
    T3_IS_REFERENCE_CONTEXT,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V8_SPEC_SHA256_EXPECTED,
    V8_TREND_OFF_RCI_BOARD_REUSED,
    V9_SPEC_SHA256_EXPECTED,
    VOLUME_RESTORED,
    canonical_v10_spec,
    spec_sha256_v10,
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
    "NEW_TF_N",
    "MIXED_TF_STRATEGY_N",
    "RECAPTURE_N",
    "FUTURE_ASK_USE_N",
    "ML_USE_N",
    "PM_ROWS_USED_N",
    "ASK_RUNTIME_ADOPTION_N",
    "V8_TREND_OFF_RCI_BOARD_REUSED_N",
    "EXTRA_ARM_N",
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


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v10_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_V10_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "RCI_INCREMENTAL_ROLE_SUPPORTED": False,
        "BOARD_INCREMENTAL_ROLE_SUPPORTED": False,
        "RCI_HARD_CONFIRM_HARMFUL": False,
        "BOARD_VETO_HARMFUL": False,
        "ENTRY_SIGNAL_EDGE_SUPPORTED": False,
        "SELECTED_STACK": None,
        "PRIMARY_INTERPRETATION": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V10_SPEC_SHA256": v10_sha,
        "B2_PARITY": False,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {"analysis_id": ANALYSIS_ID, "blocker": msg, "required": req, "preflight": pre, "leak": leak, "extra": extra or {}, "_markdown": build_markdown({"required": req})}
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V10_SPEC_SHA256": v10_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v10_spec()
    v10_sha = spec_sha256_v10(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} parent={parent_sha[:12]} v10={v10_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
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
        or NEW_TIMEFRAME
        or MIXED_TF_STRATEGY
        or V8_TREND_OFF_RCI_BOARD_REUSED
        or (not T3_IS_REFERENCE_CONTEXT)
        or (not CROSS_INDEPENDENT_NECESSITY_UNCONFIRMED)
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)

    v8_req = dict((_load(V8_OUT / "report.json").get("required") or {}))
    if str(v8_req.get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    if v8_req.get("TREND_HARD_ROLE_SUPPORTED") is not True:
        return _stop("STOP. V8 TREND_HARD_ROLE_SUPPORTED must remain true.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    if v8_req.get("PRICE_ACTION_HARD_ROLE_SUPPORTED") is not False or v8_req.get("VOLUME_HARD_ROLE_SUPPORTED") is not False:
        return _stop("STOP. V8 PA/Volume must remain unsupported.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)

    v9_req = dict((_load(V9_OUT / "report.json").get("required") or {}))
    if str(v9_req.get("V9_SPEC_SHA256") or "") != V9_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V9 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    if v9_req.get("JOINT_TREND_ROLE_SUPPORTED") is not True:
        return _stop("STOP. V9 JOINT_TREND_ROLE_SUPPORTED must remain true.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    if v9_req.get("T3_PARITY") is not True:
        return _stop("STOP. V9 T3_PARITY must remain true.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)

    rows: list[dict[str, Any]] = []
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)
        for r in list(body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            rows.append(rec)

    leak["TREND_BIT_MISMATCH_N"] = mismatch
    leak["RECAPTURE_N"] = 0
    leak["V8_TREND_OFF_RCI_BOARD_REUSED_N"] = 0
    leak["EXTRA_ARM_N"] = 0
    if mismatch:
        return _stop(f"STOP. Trend bit mismatch n={mismatch}.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)

    base = [r for r in rows if base_ok(r)]
    funnels = {aid: filter_arm(rows, aid) for aid in ARM_ORDER}
    states_f = {sid: filter_state(base, sid) for sid in STATE_ORDER}

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

    arms = {aid: arm_metrics(funnels[aid], list(DAYS)) for aid in ARM_ORDER}
    states = {sid: arm_metrics(states_f[sid], list(DAYS)) for sid in STATE_ORDER}
    for aid, arm in list(arms.items()) + list(states.items()):
        extra = concentration_extra(arm)
        arm.update(extra)

    b2 = arms["B2_RCI_BOARD"]
    b2_ok = int(b2.get("EXECUTABLE_SIGNAL_N") or 0) == int(B2_EXECUTABLE_N_EXPECTED) and _close(
        b2.get("MARKOUT180_MEAN"), B2_MARKOUT_180_EXPECTED
    ) and _close(b2.get("MARKOUT300_MEAN"), B2_MARKOUT_300_EXPECTED)
    if not b2_ok:
        return _stop(
            f"STOP. B2 does not reproduce V8 A3 / V9 T3 n={b2.get('EXECUTABLE_SIGNAL_N')} 180={b2.get('MARKOUT180_MEAN')} 300={b2.get('MARKOUT300_MEAN')}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v10_sha=v10_sha,
            extra={"b2": slim_arm(b2)},
        )

    r11_n = int(states["R1B1"].get("EXECUTABLE_SIGNAL_N") or 0)
    b2_n = int(b2.get("EXECUTABLE_SIGNAL_N") or 0)
    leak["R1B1_B2_N_GAP"] = abs(r11_n - b2_n)
    if r11_n != b2_n:
        return _stop(
            f"STOP. R1B1 executable n={r11_n} != B2 n={b2_n}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v10_sha=v10_sha,
        )

    vs = {
        "B1_vs_B0": vs_arm(arms["B1_RCI"], arms["B0_T3_PULLBACK"], integrity_ok=integ_ok),
        "B2_vs_B1": vs_arm(arms["B2_RCI_BOARD"], arms["B1_RCI"], integrity_ok=integ_ok),
        "B0_vs_B1": vs_arm(arms["B0_T3_PULLBACK"], arms["B1_RCI"], integrity_ok=integ_ok),
        "B1_vs_B2": vs_arm(arms["B1_RCI"], arms["B2_RCI_BOARD"], integrity_ok=integ_ok),
    }
    rci = bool(vs["B1_vs_B0"].get("ROBUST_BETTER"))
    board = bool(vs["B2_vs_B1"].get("ROBUST_BETTER"))
    rci_harm = bool(vs["B0_vs_B1"].get("ROBUST_BETTER"))
    board_harm = bool(vs["B1_vs_B2"].get("ROBUST_BETTER"))
    sel_id = selected_arm_id(rci, board, rci_harm, board_harm)
    edge_pack = edge_arm(arms[sel_id], integrity_ok=integ_ok)
    edge_b2 = edge_arm(b2, integrity_ok=integ_ok)
    edge = bool(edge_pack.get("CORE_ENTRY_EDGE_SUPPORTED"))
    decision = decide_case(rci=rci, board=board, rci_harm=rci_harm, board_harm=board_harm, integrity_ok=integ_ok)
    if decision.get("SELECTED_STACK") != STACK[sel_id]:
        return _stop("STOP. Selected stack mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v10_sha=v10_sha)

    answers = {
        "Q1_RCI_BEATS_B0": rci,
        "Q2_BOARD_BEATS_B1": board,
        "Q3_RCI_HARMFUL": rci_harm,
        "Q4_BOARD_HARMFUL": board_harm,
        "Q5_NO_ABSOLUTE_ENTRY_EDGE": (not edge),
        "B1_vs_B0_SUBSTANTIAL": bool(vs["B1_vs_B0"].get("SUBSTANTIAL")),
        "B2_vs_B1_SUBSTANTIAL": bool(vs["B2_vs_B1"].get("SUBSTANTIAL")),
    }

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V10_SPEC_SHA256": v10_sha,
        "B2_PARITY": True,
        "RCI_INCREMENTAL_ROLE_SUPPORTED": rci,
        "BOARD_INCREMENTAL_ROLE_SUPPORTED": board,
        "RCI_HARD_CONFIRM_HARMFUL": rci_harm,
        "BOARD_VETO_HARMFUL": board_harm,
        "ENTRY_SIGNAL_EDGE_SUPPORTED": edge,
        "SELECTED_STACK": decision.get("SELECTED_STACK"),
        "PRIMARY_INTERPRETATION": decision.get("PRIMARY_INTERPRETATION"),
        "TRUE_OOS": bool(TRUE_OOS),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    pub_arms = {aid: slim_arm(arms[aid]) for aid in ARM_ORDER}
    pub_states = {sid: slim_arm(states[sid]) for sid in STATE_ORDER}
    tod = {aid: tod_pack(list(funnels[aid].get("exe_rows") or [])) for aid in ARM_ORDER}
    for sid in STATE_ORDER:
        tod[sid] = tod_pack(list(states_f[sid].get("exe_rows") or []))
    pq3 = {aid: pq3_diagnostic(arms[aid]) for aid in ARM_ORDER}
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "arms": pub_arms,
        "states": pub_states,
        "pairwise": vs,
        "questions": answers,
        "tod": tod,
        "pq3_diagnostic": pq3,
        "core_edge_selected": edge_pack,
        "core_edge_b2": edge_b2,
        "decision": decision,
        "selected_arm_id": sel_id,
        "trend_bit_mismatch_n": mismatch,
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
    for aid in ARM_ORDER:
        for rec in arms[aid].get("daily") or []:
            daily.append({"group": aid, **rec})
    for sid in STATE_ORDER:
        for rec in states[sid].get("daily") or []:
            daily.append({"group": sid, **rec})
    tod_rows = []
    for k, xs in tod.items():
        for rec in xs:
            tod_rows.append({"group": k, **rec})
    conc_rows = [
        arm_row(arms[aid])
        | {"group": aid, **{k: arms[aid].get(k) for k in ("TOP_SYMBOL_SHARE", "TOP3_SYMBOL_SHARE", "DROP_TOP3_180", "DROP_TOP3_300")}}
        for aid in ARM_ORDER
    ]
    pq3_rows = []
    for aid in ARM_ORDER:
        d = pq3[aid]
        pq3_rows.append({"group": aid, "kind": "summary", "A4_EXECUTABLE_N": d.get("A4_EXECUTABLE_N"), "diagnostic_only": True, "not_a_gate": True})
        for feat in ("PQ3", "VQ2", "TQ1_EMA_SEPARATION", "TQ2_EMA21_SLOPE"):
            rec = dict(d.get(feat) or {})
            rec["group"] = aid
            rec["kind"] = feat
            pq3_rows.append(rec)
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "V10_SPEC_SHA256": v10_sha,
                "V9_SPEC_SHA256": V9_SPEC_SHA256_EXPECTED,
                "V8_SPEC_SHA256": V8_SPEC_SHA256_EXPECTED,
                "TF": "TF1",
                "JOIN_ONLY": True,
                "T3_IS_REFERENCE_CONTEXT": True,
                "CROSS_INDEPENDENT_NECESSITY_UNCONFIRMED": True,
                "PA_RESTORED": False,
                "VOLUME_RESTORED": False,
                "PERSISTENCE_ADDED": False,
                "PQ3_HARD_GATE": False,
                "V8_TREND_OFF_RCI_BOARD_REUSED": False,
                "C14": False,
            }
        ),
        "Arms": [arm_row(arms[aid]) for aid in ARM_ORDER],
        "States": [arm_row(states[sid]) for sid in STATE_ORDER],
        "Pairwise": [{"pair": k, **{kk: vv for kk, vv in v.items() if kk not in ("day_improve_180", "day_improve_300")}} for k, v in vs.items()],
        "TOD": tod_rows or [{"empty": True}],
        "Concentration": conc_rows,
        "PQ3_Diagnostic": pq3_rows or [{"empty": True}],
        "Daily": daily or [{"empty": True}],
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "B2_PARITY": True}),
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
        f"DONE verdict={req.get('VERDICT')} rci={rci} board={board} rci_harm={rci_harm} board_harm={board_harm} "
        f"edge={edge} sel={sel_id} b2={b2_n} r11={r11_n} mismatch={mismatch} ni={ni_ok} out={V10_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
