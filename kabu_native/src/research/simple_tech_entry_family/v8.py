"""Offline SIMPLE_TECH V8 TF1 architecture role RCA. Join-only. No ENTRY change. No C14. No EXIT."""
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
from research.simple_tech_entry_family.harvest import CACHE, load_day_cache, sealed_day_caps
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.isolation import (
    TODAY,
    V1_OUT,
    V6_OUT,
    V6_PR_OUT,
    V7_OUT,
    V8_OUT,
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v3_spec import PARENT_SPEC_SHA256_EXPECTED, V1_LOCKED
from research.simple_tech_entry_family.v4_persistence_spec import V3_LOCKED_CONTROL
from research.simple_tech_entry_family.v6_harvest import V6_CACHE
from research.simple_tech_entry_family.v6_pullback_spec import V6_RCA_SPEC_SHA256_EXPECTED
from research.simple_tech_entry_family.v6_spec import PRE_TREND_N_EXPECTED
from research.simple_tech_entry_family.v7_harvest import V7_CACHE
from research.simple_tech_entry_family.v8_analyze import (
    DAYS,
    a4_diagnostics,
    arm_metrics,
    core_edge_gate,
    decide_case,
    slim_arm,
    vs_arm,
)
from research.simple_tech_entry_family.v8_harvest import V8_CACHE, filter_arm, join_v8_day, save_v8_day_cache
from research.simple_tech_entry_family.v8_publish import REQUIRED_KEYS, arm_row, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v8_spec import (
    A0_SIGNAL_N_EXPECTED,
    ANALYSIS_ID,
    ARM_ORDER,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    BB_CHANGED,
    C14_USED_FOR_SELECTION,
    EMA_CHANGED,
    ENTRY_RULE_CHANGED,
    EXIT_IMPLEMENTED,
    MIXED_TF_STRATEGY,
    NEW_TIMEFRAME,
    PERSISTENCE_ADDED,
    PQ3_HARD_GATE,
    PULLBACK_REMOVED,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V7_SPEC_SHA256_EXPECTED,
    canonical_v8_spec,
    spec_sha256_v8,
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
    "PERSISTENCE_AND_N",
    "RCI_CHANGE_N",
    "EMA_CHANGE_N",
    "BB_CHANGE_N",
    "PQ3_GATE_N",
    "NEW_TF_N",
    "MIXED_TF_STRATEGY_N",
    "EXTRA_ARM_N",
    "RECAPTURE_N",
    "FUTURE_ASK_USE_N",
    "FUTURE_FEATURE_USE_N",
    "ML_USE_N",
    "PM_ROWS_USED_N",
    "ASK_RUNTIME_ADOPTION_N",
    "PULLBACK_REMOVE_N",
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


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v8_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_V8_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "PRICE_ACTION_HARD_ROLE_SUPPORTED": False,
        "VOLUME_HARD_ROLE_SUPPORTED": False,
        "TREND_HARD_ROLE_SUPPORTED": False,
        "RCI_CONFIRM_ROLE_SUPPORTED": False,
        "BOARD_VETO_ROLE_SUPPORTED": False,
        "CORE_ENTRY_EDGE_SUPPORTED": False,
        "PRIMARY_ARCHITECTURE_DEFICIENCY": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V8_SPEC_SHA256": v8_sha,
        "V1_PARITY": False,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {"analysis_id": ANALYSIS_ID, "blocker": msg, "required": req, "preflight": pre, "leak": leak, "extra": extra or {}, "_markdown": build_markdown({"required": req})}
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V8_SPEC_SHA256": v8_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v8_spec()
    v8_sha = spec_sha256_v8(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} parent={parent_sha[:12]} v8={v8_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or ASK_RUNTIME_ADOPTION_ALLOWED
        or EXIT_IMPLEMENTED
        or ENTRY_RULE_CHANGED
        or THRESHOLD_SEARCH
        or C14_USED_FOR_SELECTION
        or RCI_CHANGED
        or EMA_CHANGED
        or BB_CHANGED
        or MIXED_TF_STRATEGY
        or NEW_TIMEFRAME
        or PQ3_HARD_GATE
        or PERSISTENCE_ADDED
        or PULLBACK_REMOVED
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)

    v1_req = dict((_load(V1_OUT / "report.json").get("required") or {}))
    if str(v1_req.get("SPEC_SHA256") or "") != parent_sha:
        return _stop("STOP. V1 report SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    v6_req = dict((_load(V6_OUT / "report.json").get("required") or {}))
    if str(v6_req.get("V6_SPEC_SHA256") or "") != V6_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V6 RCA spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    v6pr = dict((_load(V6_PR_OUT / "report.json").get("required") or {}))
    if v6pr.get("PULLBACK_DEPTH_MECHANISM_SUPPORTED") is not False:
        return _stop("STOP. V6 pullback-rule mechanism must remain false.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    v7_req = dict((_load(V7_OUT / "report.json").get("required") or {}))
    if str(v7_req.get("V7_SPEC_SHA256") or "") != V7_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V7 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if v7_req.get("MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED") is not False:
        return _stop("STOP. V7 multi-timeframe must remain false.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if str(v7_req.get("PULLBACK_PREFERRED_SCALE") or "") != "TF1" or str(v7_req.get("RCI_PREFERRED_SCALE") or "") != "TF1":
        return _stop("STOP. V7 preferred scales drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _stop("STOP. Sealed Capture incomplete.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)

    V8_CACHE.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    tf1_s0 = 0
    s6_n = 0
    for cap in caps:
        day = str(cap["date"])
        v7_body = load_day_cache(V7_CACHE / f"day_{day}.json", V7_SPEC_SHA256_EXPECTED)
        if not v7_body:
            return _stop(f"STOP. V7 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
        v6_body = load_day_cache(V6_CACHE / f"day_{day}.json", V6_RCA_SPEC_SHA256_EXPECTED)
        if not v6_body:
            return _stop(f"STOP. V6 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
        cache_p = V8_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, v8_sha)
        if not body:
            body = join_v8_day(v7_body=v7_body, v6_body=v6_body, spec_sha=v8_sha)
            if body.get("ok"):
                save_v8_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(f"STOP. Day join failed {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
        day_rows = list(body.get("rows") or [])
        rows.extend(day_rows)
        tf1_s0 += len(day_rows)
        s6_n += sum(1 for r in day_rows if r.get("s6"))
        lk = body.get("leak") or {}
        for k in ("JOIN_MISS_N", "RECAPTURE_N", "C14_REPLAY_N", "EXIT_SIM_N", "PQ3_GATE_N", "PERSISTENCE_AND_N", "NEW_TF_N", "MIXED_TF_STRATEGY_N", "EXTRA_ARM_N"):
            leak[k] = int(leak.get(k) or 0) + int(lk.get(k) or 0)
        pm = sum(1 for r in day_rows if str(r.get("session") or "AM") != "AM")
        leak["PM_ROWS_USED_N"] = int(leak.get("PM_ROWS_USED_N") or 0) + int(pm)

    if leak["PM_ROWS_USED_N"]:
        return _stop("STOP. PM rows present.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)
    if tf1_s0 != int(PRE_TREND_N_EXPECTED) or s6_n != int(A0_SIGNAL_N_EXPECTED):
        return _stop(f"STOP. TF1 parity failed s0={tf1_s0} s6={s6_n}.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)

    funnels = {aid: filter_arm(rows, aid) for aid in ARM_ORDER}
    a0 = funnels["A0_V1"]
    if int(a0["SIGNAL_N"]) != int(A0_SIGNAL_N_EXPECTED) or int(a0["SIGNAL_N"]) != s6_n:
        return _stop(f"STOP. A0 signal n={a0['SIGNAL_N']} expected {A0_SIGNAL_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v8_sha=v8_sha)

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
    a0m = arms["A0_V1"]
    if int(a0m.get("EXECUTABLE_SIGNAL_N") or 0) != int(V3_LOCKED_CONTROL["EXECUTABLE_SIGNAL_N"]):
        return _stop(
            f"STOP. A0 executable n={a0m.get('EXECUTABLE_SIGNAL_N')} != V3 {V3_LOCKED_CONTROL['EXECUTABLE_SIGNAL_N']}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v8_sha=v8_sha,
        )
    if not (
        _close(a0m.get("MARKOUT60_MEAN"), V3_LOCKED_CONTROL["MARKOUT_60_MEAN"])
        and _close(a0m.get("MARKOUT180_MEAN"), V3_LOCKED_CONTROL["MARKOUT_180_MEAN"])
        and _close(a0m.get("MARKOUT300_MEAN"), V3_LOCKED_CONTROL["MARKOUT_300_MEAN"])
    ):
        return _stop(
            "STOP. A0 markouts do not reproduce V3 locked control.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v8_sha=v8_sha,
            extra={"a0": slim_arm(a0m), "v3": V3_LOCKED_CONTROL},
        )

    vs = {
        "A0_vs_A1": vs_arm(arms["A0_V1"], arms["A1_NO_PRICE_ACTION"], integrity_ok=integ_ok),
        "A0_vs_A2": vs_arm(arms["A0_V1"], arms["A2_NO_VOLUME"], integrity_ok=integ_ok),
        "A3_vs_A0": vs_arm(arms["A3_NO_PA_NO_VOLUME"], arms["A0_V1"], integrity_ok=integ_ok),
        "A3_vs_A4": vs_arm(arms["A3_NO_PA_NO_VOLUME"], arms["A4_CORE"], integrity_ok=integ_ok),
        "A4_vs_A5": vs_arm(arms["A4_CORE"], arms["A5_CORE_NO_RCI"], integrity_ok=integ_ok),
        "A4_vs_A6": vs_arm(arms["A4_CORE"], arms["A6_CORE_NO_BOARD"], integrity_ok=integ_ok),
        "A4_vs_A0": vs_arm(arms["A4_CORE"], arms["A0_V1"], integrity_ok=integ_ok),
        "A5_vs_A4": vs_arm(arms["A5_CORE_NO_RCI"], arms["A4_CORE"], integrity_ok=integ_ok),
        "A6_vs_A4": vs_arm(arms["A6_CORE_NO_BOARD"], arms["A4_CORE"], integrity_ok=integ_ok),
    }
    roles = {
        "PRICE_ACTION_HARD_ROLE_SUPPORTED": bool(vs["A0_vs_A1"].get("ROBUST_BETTER")),
        "VOLUME_HARD_ROLE_SUPPORTED": bool(vs["A0_vs_A2"].get("ROBUST_BETTER")),
        "TREND_HARD_ROLE_SUPPORTED": bool(vs["A3_vs_A4"].get("ROBUST_BETTER")),
        "RCI_CONFIRM_ROLE_SUPPORTED": bool(vs["A4_vs_A5"].get("ROBUST_BETTER")),
        "BOARD_VETO_ROLE_SUPPORTED": bool(vs["A4_vs_A6"].get("ROBUST_BETTER")),
    }
    board_neutral = (not roles["BOARD_VETO_ROLE_SUPPORTED"]) and (not bool(vs["A6_vs_A4"].get("ROBUST_BETTER")))
    edge_pack = core_edge_gate(arms["A4_CORE"], integrity_ok=integ_ok)
    edge = bool(edge_pack.get("CORE_ENTRY_EDGE_SUPPORTED"))
    decision = decide_case(
        roles=roles,
        board_neutral=board_neutral,
        edge=edge,
        a4_vs_a0=vs["A4_vs_A0"],
        a3_vs_a0=vs["A3_vs_A0"],
        a3_vs_a4=vs["A3_vs_A4"],
        a5_vs_a4=vs["A5_vs_A4"],
        a6_vs_a4=vs["A6_vs_A4"],
        integrity_ok=integ_ok,
    )
    if not integ_ok:
        decision = {
            "CASE": "INTEGRITY",
            "VERDICT": "SIMPLE_TECH_V8_INTEGRITY_FAILED",
            "PRIMARY_ARCHITECTURE_DEFICIENCY": "INTEGRITY_FAILURE",
            "NEXT": "NON_INTERFERENCE_FAIL",
        }

    diag = a4_diagnostics(list(arms["A4_CORE"].get("exe_rows") or []))
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V8_SPEC_SHA256": v8_sha,
        "V1_PARITY": True,
        "PRICE_ACTION_HARD_ROLE_SUPPORTED": roles["PRICE_ACTION_HARD_ROLE_SUPPORTED"],
        "VOLUME_HARD_ROLE_SUPPORTED": roles["VOLUME_HARD_ROLE_SUPPORTED"],
        "TREND_HARD_ROLE_SUPPORTED": roles["TREND_HARD_ROLE_SUPPORTED"],
        "RCI_CONFIRM_ROLE_SUPPORTED": roles["RCI_CONFIRM_ROLE_SUPPORTED"],
        "BOARD_VETO_ROLE_SUPPORTED": roles["BOARD_VETO_ROLE_SUPPORTED"],
        "CORE_ENTRY_EDGE_SUPPORTED": edge,
        "PRIMARY_ARCHITECTURE_DEFICIENCY": decision.get("PRIMARY_ARCHITECTURE_DEFICIENCY"),
        "TRUE_OOS": bool(TRUE_OOS),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    pub_arms = {aid: slim_arm(arms[aid]) for aid in ARM_ORDER}
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "arms": pub_arms,
        "pairwise": vs,
        "roles": roles,
        "board_neutral": board_neutral,
        "core_edge": edge_pack,
        "decision": decision,
        "a4_diagnostics": diag,
        "join_miss_n": leak.get("JOIN_MISS_N"),
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
            daily.append({"ARM_ID": aid, **rec})
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "PARENT_SPEC_SHA256": parent_sha,
                "V8_SPEC_SHA256": v8_sha,
                "TF": "TF1",
                "JOIN_ONLY": True,
                "ENTRY_RULE_CHANGED": False,
                "THRESHOLD_SEARCH": False,
                "PQ3_HARD_GATE": False,
                "PERSISTENCE_ADDED": False,
                "NEW_TIMEFRAME": False,
                "C14": False,
            }
        ),
        "Population": [{"tf1_s0": tf1_s0, "s6": s6_n, "join_miss": leak.get("JOIN_MISS_N")}]
        + [{"ARM_ID": aid, "SIGNAL_N": funnels[aid]["SIGNAL_N"], "EXECUTABLE_SIGNAL_N": funnels[aid]["EXECUTABLE_SIGNAL_N"]} for aid in ARM_ORDER],
        "Arms": [arm_row(arms[aid]) for aid in ARM_ORDER],
        "Pairwise": [{"pair": k, **{kk: vv for kk, vv in v.items() if kk not in ("day_improve_180", "day_improve_300")}} for k, v in vs.items()],
        "A4_Diagnostics": kv_rows({k: v for k, v in diag.items() if not isinstance(v, dict)})
        + [diag.get("PQ3") or {"empty": True}, diag.get("VQ2") or {"empty": True}, diag.get("TQ1_EMA_SEPARATION") or {"empty": True}, diag.get("TQ2_EMA21_SLOPE") or {"empty": True}],
        "Daily": daily or [{"empty": True}],
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "ENTRY_RULE_CHANGED": False}),
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
        f"DONE verdict={req.get('VERDICT')} edge={edge} pa={roles['PRICE_ACTION_HARD_ROLE_SUPPORTED']} "
        f"vol={roles['VOLUME_HARD_ROLE_SUPPORTED']} trend={roles['TREND_HARD_ROLE_SUPPORTED']} "
        f"rci={roles['RCI_CONFIRM_ROLE_SUPPORTED']} board={roles['BOARD_VETO_ROLE_SUPPORTED']} "
        f"a0={a0m.get('EXECUTABLE_SIGNAL_N')} a4={arms['A4_CORE'].get('EXECUTABLE_SIGNAL_N')} ni={ni_ok} out={V8_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
