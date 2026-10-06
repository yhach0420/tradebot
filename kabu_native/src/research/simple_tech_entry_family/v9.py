"""Offline SIMPLE_TECH V9 TF1 trend context RCA. Join-only from V8. No EMA retune. No EXIT."""
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
from research.simple_tech_entry_family.v9_analyze import (
    concentration_extra,
    decide_case,
    edge_t3,
    joint_not_small_sample,
    state_exclusion,
    tod_pack,
)
from research.simple_tech_entry_family.v9_harvest import (
    ARM_ORDER,
    STATE_ORDER,
    attach_bits,
    base_ok,
    filter_arm,
    filter_state,
)
from research.simple_tech_entry_family.v9_publish import REQUIRED_KEYS, arm_row, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v9_spec import (
    ANALYSIS_ID,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    BB_CHANGED,
    C14_USED_FOR_SELECTION,
    EMA_CHANGED,
    ENTRY_RULE_CHANGED,
    EXIT_IMPLEMENTED,
    MIXED_TF_STRATEGY,
    NEW_TIMEFRAME,
    PA_RESTORED,
    PERSISTENCE_ADDED,
    PQ3_HARD_GATE,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    T3_EXECUTABLE_N_EXPECTED,
    T3_MARKOUT_180_EXPECTED,
    T3_MARKOUT_300_EXPECTED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V8_SPEC_SHA256_EXPECTED,
    VOLUME_RESTORED,
    canonical_v9_spec,
    spec_sha256_v9,
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


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v9_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_V9_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "CROSS_ROLE_SUPPORTED": False,
        "SLOPE_ROLE_SUPPORTED": False,
        "JOINT_TREND_ROLE_SUPPORTED": False,
        "TREND_INTERACTION_SUPPORTED": False,
        "ENTRY_SIGNAL_EDGE_SUPPORTED": False,
        "PRIMARY_TREND_INTERPRETATION": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V9_SPEC_SHA256": v9_sha,
        "T3_PARITY": False,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {"analysis_id": ANALYSIS_ID, "blocker": msg, "required": req, "preflight": pre, "leak": leak, "extra": extra or {}, "_markdown": build_markdown({"required": req})}
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V9_SPEC_SHA256": v9_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v9_spec()
    v9_sha = spec_sha256_v9(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} parent={parent_sha[:12]} v9={v9_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or ASK_RUNTIME_ADOPTION_ALLOWED
        or EXIT_IMPLEMENTED
        or ENTRY_RULE_CHANGED
        or THRESHOLD_SEARCH
        or C14_USED_FOR_SELECTION
        or EMA_CHANGED
        or BB_CHANGED
        or PA_RESTORED
        or VOLUME_RESTORED
        or PERSISTENCE_ADDED
        or PQ3_HARD_GATE
        or NEW_TIMEFRAME
        or MIXED_TF_STRATEGY
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)

    v8_req = dict((_load(V8_OUT / "report.json").get("required") or {}))
    if str(v8_req.get("V8_SPEC_SHA256") or "") != V8_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V8 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
    if v8_req.get("TREND_HARD_ROLE_SUPPORTED") is not True:
        return _stop("STOP. V8 TREND_HARD_ROLE_SUPPORTED must remain true.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
    if v8_req.get("PRICE_ACTION_HARD_ROLE_SUPPORTED") is not False or v8_req.get("VOLUME_HARD_ROLE_SUPPORTED") is not False:
        return _stop("STOP. V8 PA/Volume must remain unsupported.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)

    rows: list[dict[str, Any]] = []
    mismatch = 0
    for cap in caps:
        day = str(cap["date"])
        body = load_day_cache(V8_CACHE / f"day_{day}.json", V8_SPEC_SHA256_EXPECTED)
        if not body:
            return _stop(f"STOP. V8 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v9_sha=v9_sha)
        for r in list(body.get("rows") or []):
            rec = attach_bits(r)
            if rec.get("trend_bit_mismatch"):
                mismatch += 1
            rows.append(rec)

    leak["TREND_BIT_MISMATCH_N"] = mismatch
    leak["RECAPTURE_N"] = 0
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

    t3 = arms["T3_BOTH_CURRENT"]
    t3_ok = int(t3.get("EXECUTABLE_SIGNAL_N") or 0) == int(T3_EXECUTABLE_N_EXPECTED) and _close(
        t3.get("MARKOUT180_MEAN"), T3_MARKOUT_180_EXPECTED
    ) and _close(t3.get("MARKOUT300_MEAN"), T3_MARKOUT_300_EXPECTED)
    if not t3_ok:
        return _stop(
            f"STOP. T3 does not reproduce V8 A3 n={t3.get('EXECUTABLE_SIGNAL_N')} 180={t3.get('MARKOUT180_MEAN')} 300={t3.get('MARKOUT300_MEAN')}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v9_sha=v9_sha,
            extra={"t3": slim_arm(t3)},
        )

    vs = {
        "T1_vs_T0": vs_arm(arms["T1_CROSS_ONLY"], arms["T0_NO_TREND"], integrity_ok=integ_ok),
        "T2_vs_T0": vs_arm(arms["T2_SLOPE_ONLY"], arms["T0_NO_TREND"], integrity_ok=integ_ok),
        "T3_vs_T0": vs_arm(arms["T3_BOTH_CURRENT"], arms["T0_NO_TREND"], integrity_ok=integ_ok),
        "T3_vs_T1": vs_arm(arms["T3_BOTH_CURRENT"], arms["T1_CROSS_ONLY"], integrity_ok=integ_ok),
        "T3_vs_T2": vs_arm(arms["T3_BOTH_CURRENT"], arms["T2_SLOPE_ONLY"], integrity_ok=integ_ok),
    }
    cross = bool(vs["T1_vs_T0"].get("ROBUST_BETTER"))
    slope = bool(vs["T2_vs_T0"].get("ROBUST_BETTER"))
    joint_raw = bool(vs["T3_vs_T0"].get("ROBUST_BETTER"))
    joint = bool(joint_raw and joint_not_small_sample(t3, vs["T3_vs_T0"]))
    interaction = bool(joint and (not cross) and (not slope))
    edge_pack = edge_t3(t3, integrity_ok=integ_ok)
    edge = bool(edge_pack.get("CORE_ENTRY_EDGE_SUPPORTED"))
    excl = state_exclusion(states)
    decision = decide_case(cross=cross, slope=slope, joint=joint, interaction=interaction, integrity_ok=integ_ok)

    s11_n = int(states["S11"].get("EXECUTABLE_SIGNAL_N") or 0)
    t3_n = int(t3.get("EXECUTABLE_SIGNAL_N") or 0)
    leak["S11_T3_N_GAP"] = abs(s11_n - t3_n)

    answers = {
        "Q1_CROSS_BEATS_T0": cross,
        "Q2_SLOPE_BEATS_T0": slope,
        "Q3_T3_MORE_STABLE_THAN_T1_T2": bool(joint and (vs["T3_vs_T1"].get("ROBUST_BETTER") or vs["T3_vs_T2"].get("ROBUST_BETTER") or interaction)),
        "Q4_EXCLUSION_OF_BAD_STATES": bool(excl.get("EXCLUSION_OF_BAD_STATES")),
        "Q5_CONTEXT_FILTER_NOT_DIRECTION_PREDICTOR": bool(joint and (not edge)),
    }

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V9_SPEC_SHA256": v9_sha,
        "T3_PARITY": True,
        "CROSS_ROLE_SUPPORTED": cross,
        "SLOPE_ROLE_SUPPORTED": slope,
        "JOINT_TREND_ROLE_SUPPORTED": joint,
        "TREND_INTERACTION_SUPPORTED": interaction,
        "ENTRY_SIGNAL_EDGE_SUPPORTED": edge,
        "PRIMARY_TREND_INTERPRETATION": decision.get("PRIMARY_TREND_INTERPRETATION"),
        "TRUE_OOS": bool(TRUE_OOS),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    pub_arms = {aid: slim_arm(arms[aid]) for aid in ARM_ORDER}
    pub_states = {sid: slim_arm(states[sid]) for sid in STATE_ORDER}
    tod = {sid: tod_pack(list(states_f[sid].get("exe_rows") or [])) for sid in STATE_ORDER}
    tod["T3"] = tod_pack(list(funnels["T3_BOTH_CURRENT"].get("exe_rows") or []))
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "arms": pub_arms,
        "states": pub_states,
        "pairwise": vs,
        "questions": answers,
        "exclusion": excl,
        "tod": tod,
        "core_edge": edge_pack,
        "decision": decision,
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
    conc_rows = [arm_row(arms[aid]) | {"group": aid, **{k: arms[aid].get(k) for k in ("TOP_SYMBOL_SHARE", "TOP3_SYMBOL_SHARE", "DROP_TOP3_180", "DROP_TOP3_300")}} for aid in ("T3_BOTH_CURRENT",)]
    conc_rows.append({"group": "S11", **arm_row(states["S11"]), **{k: states["S11"].get(k) for k in ("TOP_SYMBOL_SHARE", "TOP3_SYMBOL_SHARE", "DROP_TOP3_180", "DROP_TOP3_300")}})
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "V9_SPEC_SHA256": v9_sha,
                "V8_SPEC_SHA256": V8_SPEC_SHA256_EXPECTED,
                "TF": "TF1",
                "JOIN_ONLY": True,
                "EMA_CHANGED": False,
                "PA_RESTORED": False,
                "VOLUME_RESTORED": False,
                "C14": False,
            }
        ),
        "Arms": [arm_row(arms[aid]) for aid in ARM_ORDER],
        "States": [arm_row(states[sid]) for sid in STATE_ORDER],
        "Pairwise": [{"pair": k, **{kk: vv for kk, vv in v.items() if kk not in ("day_improve_180", "day_improve_300")}} for k, v in vs.items()],
        "TOD": tod_rows or [{"empty": True}],
        "Concentration": conc_rows,
        "Daily": daily or [{"empty": True}],
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "T3_PARITY": True}),
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
        f"DONE verdict={req.get('VERDICT')} cross={cross} slope={slope} joint={joint} interact={interaction} "
        f"edge={edge} t3={t3_n} s11={s11_n} mismatch={mismatch} ni={ni_ok} out={V9_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
