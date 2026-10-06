"""Offline SIMPLE_TECH V4 Volume persistence rule. Replace V1 1.5x with P60/P80/P90. No C14. No EXIT."""
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
    V3_OUT,
    V4_OUT,
    V4_PR_OUT,
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v3_spec import PARENT_SPEC_SHA256_EXPECTED, V1_LOCKED
from research.simple_tech_entry_family.v4_persistence_analyze import (
    arm_metrics,
    band_stability,
    decide_case,
    edge_gate_arm,
    filter_arm,
    slim_arm,
    vs_control_gates,
)
from research.simple_tech_entry_family.v4_persistence_harvest import PR_CACHE, V4_CACHE, join_pre_volume, save_pr_day_cache
from research.simple_tech_entry_family.v4_persistence_publish import (
    REQUIRED_KEYS,
    arm_summary as pub_arm_summary,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.simple_tech_entry_family.v4_persistence_spec import (
    ANALYSIS_ID,
    ARM_ORDER,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    C14_USED_FOR_SELECTION,
    CONTROL_ID,
    ENTRY_RULE_CHANGED,
    EXIT_IMPLEMENTED,
    MAGNITUDE_AND_FORBIDDEN,
    PRE_VOLUME_N_EXPECTED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    V3_LOCKED_CONTROL,
    V4_RCA_SPEC_SHA256_EXPECTED,
    canonical_v4_pr_spec,
    spec_sha256_v4_pr,
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
    "THRESHOLD_SEARCH_N",
    "MAGNITUDE_AND_N",
    "FUTURE_ASK_USE_N",
    "FUTURE_FEATURE_USE_N",
    "ML_USE_N",
    "PM_ROWS_USED_N",
    "ASK_RUNTIME_ADOPTION_N",
    "RECAPTURE_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _close_enough(a: Any, b: Any, tol: float = 1e-6) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= float(tol)


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, pr_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_V4_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "PERSISTENCE_RULE_MECHANISM_SUPPORTED": False,
        "ENTRY_SIGNAL_EDGE_REPAIRED": False,
        "SELECTED_PERSISTENCE_BAND": "NONE",
        "PRIMARY_DEFICIENCY_AFTER_V4": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V4_PR_SPEC_SHA256": pr_sha,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {"analysis_id": ANALYSIS_ID, "blocker": msg, "required": req, "preflight": pre, "leak": leak, "extra": extra or {}, "_markdown": build_markdown({"required": req})}
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V4_PR_SPEC_SHA256": pr_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v4_pr_spec()
    pr_sha = spec_sha256_v4_pr(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(
        f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} "
        f"parent={parent_sha[:12]} pr={pr_sha[:12]}",
        flush=True,
    )
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if RUNTIME_ADOPTION_ALLOWED or ASK_RUNTIME_ADOPTION_ALLOWED or EXIT_IMPLEMENTED or ENTRY_RULE_CHANGED or THRESHOLD_SEARCH or C14_USED_FOR_SELECTION:
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if not MAGNITUDE_AND_FORBIDDEN:
        return _stop("STOP. Magnitude AND must stay forbidden.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)

    v1_req = dict((_load(V1_OUT / "report.json").get("required") or {}))
    if str(v1_req.get("SPEC_SHA256") or "") != parent_sha:
        return _stop("STOP. V1 report SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    v4_req = dict((_load(V4_OUT / "report.json").get("required") or {}))
    if str(v4_req.get("V4_SPEC_SHA256") or "") != V4_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V4 RCA spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if str(v4_req.get("SUPPORTED_VOLUME_MECHANISM") or "") != "PERSISTENCE":
        return _stop("STOP. V4 RCA mechanism is not PERSISTENCE.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _stop("STOP. Sealed Capture incomplete.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)

    PR_CACHE.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    funnel = {"s0": 0, "s1": 0, "s2": 0, "s3": 0, "s4": 0, "s5": 0, "s6": 0}
    day_meta: list[dict[str, Any]] = []
    v1_signals: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        v1_body = load_day_cache(CACHE / f"day_{day}.json", parent_sha)
        if not v1_body:
            return _stop(f"STOP. V1 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
        opps = list(v1_body.get("opps") or [])
        for k in funnel:
            funnel[k] += sum(1 for r in opps if r.get(k))
        v1_signals.extend(list(v1_body.get("signals") or []))
        pm = sum(1 for r in opps if r.get("s4") and str(r.get("session") or "AM") != "AM")
        leak["PM_ROWS_USED_N"] = int(leak.get("PM_ROWS_USED_N") or 0) + int(pm)
        v4_body = load_day_cache(V4_CACHE / f"day_{day}.json", V4_RCA_SPEC_SHA256_EXPECTED)
        if not v4_body:
            return _stop(f"STOP. V4 RCA cache missing {day}. Recapture forbidden this run.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
        cache_p = PR_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, pr_sha)
        if not body:
            joined = join_pre_volume(opps, list(v4_body.get("rows") or []))
            body = {
                "ok": bool(joined.get("ok")),
                "date": day,
                "spec_sha": pr_sha,
                "v4_rca_spec_sha": V4_RCA_SPEC_SHA256_EXPECTED,
                "parent_spec_sha": parent_sha,
                "joined_n": joined.get("joined_n"),
                "pre_n": joined.get("pre_n"),
                "missing_n": joined.get("missing_n"),
                "rows": joined.get("rows"),
                "blocker": None if joined.get("ok") else f"JOIN_FAIL missing={joined.get('missing_n')}",
            }
            if body.get("ok"):
                save_pr_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(f"STOP. Join failed {day}: {body.get('blocker')}", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
        rows.extend(list(body.get("rows") or []))
        day_meta.append({"date": day, "pre_n": body.get("pre_n"), "joined_n": body.get("joined_n")})

    if leak["PM_ROWS_USED_N"]:
        return _stop("STOP. PM rows present.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)
    if len(v1_signals) != int(V1_LOCKED["SIGNAL_N"]) or funnel["s4"] != int(PRE_VOLUME_N_EXPECTED) or funnel["s6"] != 29:
        return _stop(
            f"STOP. V1 parity failed signals={len(v1_signals)} s4={funnel['s4']} s6={funnel['s6']}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            pr_sha=pr_sha,
            extra={"funnel": funnel},
        )
    if len(rows) != int(PRE_VOLUME_N_EXPECTED):
        return _stop(f"STOP. PRE_VOLUME row n={len(rows)} != {PRE_VOLUME_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, pr_sha=pr_sha)

    funnels = {aid: filter_arm(rows, aid) for aid in ARM_ORDER}
    ctrl_f = funnels[CONTROL_ID]
    s5_n = sum(1 for r in rows if r.get("s5_v1"))
    s6_n = sum(1 for r in rows if r.get("s6_v1"))
    if int(ctrl_f["VOLUME_PASS_N"]) != int(s5_n) or int(s5_n) != 80:
        return _stop(
            f"STOP. CONTROL volume parity failed pass={ctrl_f['VOLUME_PASS_N']} s5={s5_n}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            pr_sha=pr_sha,
        )
    if int(ctrl_f["BOARD_PASS_N"]) != int(s6_n) or int(s6_n) != 29:
        return _stop(
            f"STOP. CONTROL board parity failed pass={ctrl_f['BOARD_PASS_N']} s6={s6_n}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            pr_sha=pr_sha,
        )

    arms = {aid: arm_metrics(funnels[aid], list(ELIGIBLE_DAYS)) for aid in ARM_ORDER}
    ctrl = arms[CONTROL_ID]
    v3_ok = (
        int(ctrl.get("EXECUTABLE_SIGNAL_N") or 0) == int(V3_LOCKED_CONTROL["EXECUTABLE_SIGNAL_N"])
        and _close_enough(ctrl.get("MARKOUT_60_MEAN"), V3_LOCKED_CONTROL["MARKOUT_60_MEAN"])
        and _close_enough(ctrl.get("MARKOUT_180_MEAN"), V3_LOCKED_CONTROL["MARKOUT_180_MEAN"])
        and _close_enough(ctrl.get("MARKOUT_300_MEAN"), V3_LOCKED_CONTROL["MARKOUT_300_MEAN"])
    )
    if not v3_ok:
        return _stop(
            "STOP. CONTROL_M15 does not reproduce V3 29-signal markouts.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            pr_sha=pr_sha,
            extra={"control": slim_arm(ctrl), "v3": V3_LOCKED_CONTROL},
        )

    post = snapshot(phase="POST")
    adv = ni_advanced(pre, post)
    live_before = pre.get("RUNTIME_PID") is not None or pre.get("CAPTURE_PID") is not None
    ni_ok = all(int(leak.get(k) or 0) == 0 for k in (
        "LIVE_PROCESS_CONTROL_CALL_N", "RUNTIME_WRITE_N", "CAPTURE_WRITE_N", "ADDITIONAL_WEBSOCKET_N",
        "ACTIVE_CAPTURE_INPUT_N", "SUBMIT_N", "CANCEL_N", "LIVE_ORDER_N", "C14_REPLAY_N", "EXIT_SIM_N",
        "THRESHOLD_SEARCH_N", "MAGNITUDE_AND_N", "RECAPTURE_N",
    )) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    if live_before:
        ni_ok = bool(
            ni_ok
            and adv.get("RUNTIME_PID_UNCHANGED")
            and adv.get("CAPTURE_PID_UNCHANGED")
            and adv.get("RUNTIME_STILL_ALIVE")
            and (adv.get("CAPTURE_STILL_ALIVE") if pre.get("CAPTURE_PID") is not None else True)
        )
    integ_ok = bool(ni_ok and int(leak.get("PM_ROWS_USED_N") or 0) == 0 and int(leak.get("ASK_RUNTIME_ADOPTION_N") or 0) == 0)

    vs = {aid: vs_control_gates(arms[aid], ctrl, integrity_ok=integ_ok) for aid in ("P60", "P80", "P90")}
    edges = {aid: edge_gate_arm(arms[aid], integrity_ok=integ_ok) for aid in ARM_ORDER}
    stability = band_stability(arms, vs)
    decision = decide_case(
        vs=vs,
        edges=edges,
        stability=stability,
        integrity_ok=integ_ok,
        pre_n=len(rows),
        expected_pre_n=int(PRE_VOLUME_N_EXPECTED),
    )
    if not ni_ok:
        decision = {
            "CASE": "F",
            "VERDICT": "SIMPLE_TECH_V4_INTEGRITY_FAILED",
            "PERSISTENCE_RULE_MECHANISM_SUPPORTED": False,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PERSISTENCE_BAND": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V4": "INTEGRITY_FAILURE",
            "NEXT": "NON_INTERFERENCE_FAIL",
        }

    summaries = {aid: pub_arm_summary(arms[aid]) for aid in ARM_ORDER}
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V4_RCA_SPEC_SHA256": V4_RCA_SPEC_SHA256_EXPECTED,
        "V4_PR_SPEC_SHA256": pr_sha,
        "V1_PARITY": True,
        "CONTROL_PARITY_V3": True,
        "PRE_VOLUME_N": len(rows),
        "CONTROL_M15": summaries[CONTROL_ID],
        "P60": summaries["P60"],
        "P80": summaries["P80"],
        "P90": summaries["P90"],
        "BAND_STABILITY": {
            "isolated_p80_optimum": stability.get("isolated_p80_optimum"),
            "mean_improve_180_300_bands": stability.get("mean_improve_180_300_bands"),
            "adjacent": stability.get("adjacent"),
            "monotonic_180": stability.get("monotonic_180"),
            "monotonic_300": stability.get("monotonic_300"),
        },
        "PERSISTENCE_RULE_MECHANISM_SUPPORTED": decision.get("PERSISTENCE_RULE_MECHANISM_SUPPORTED"),
        "ENTRY_SIGNAL_EDGE_REPAIRED": decision.get("ENTRY_SIGNAL_EDGE_REPAIRED"),
        "SELECTED_PERSISTENCE_BAND": decision.get("SELECTED_PERSISTENCE_BAND"),
        "PRIMARY_DEFICIENCY_AFTER_V4": decision.get("PRIMARY_DEFICIENCY_AFTER_V4"),
        "TRUE_OOS": bool(TRUE_OOS),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "v1_funnel": funnel,
        "arms": {aid: slim_arm(arms[aid]) for aid in ARM_ORDER},
        "vs_control": vs,
        "edge_gates": edges,
        "band_stability": stability,
        "decision": decision,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "non_interference": adv,
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "entry_rule_changed": False,
        "entry_signal_spec_frozen": False,
        "entry_execution_spec_frozen": False,
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)

    sig_keep = (
        "date", "symbol", "t0", "vol_accel", "VQ2", "board_ok", "executable_signal",
        "markout_60", "markout_180", "markout_300", "mfe_bps", "mae_bps", "s5_v1", "s6_v1",
    )
    sig_rows = []
    daily_rows = []
    for aid in ARM_ORDER:
        for r in funnels[aid]["exe_rows"]:
            sig_rows.append({"ARM_ID": aid, **{k: r.get(k) for k in sig_keep}})
        for d in arms[aid].get("daily") or []:
            daily_rows.append({"ARM_ID": aid, **d})
    sheets = {
        "Precommit": kv_rows({
            "ANALYSIS_ID": ANALYSIS_ID,
            "PARENT_SPEC_SHA256": parent_sha,
            "V4_RCA_SPEC_SHA256": V4_RCA_SPEC_SHA256_EXPECTED,
            "V4_PR_SPEC_SHA256": pr_sha,
            "THRESHOLD_SEARCH": False,
            "MAGNITUDE_AND_FORBIDDEN": True,
            "C14": False,
            "bands": "P60=0.60 P80=0.80 P90=0.90",
        }),
        "Population": [{"section": "funnel", **funnel}] + day_meta,
        "Arms": [summaries[aid] for aid in ARM_ORDER],
        "Daily": daily_rows or [{"empty": True}],
        "Symbols": [
            {"ARM_ID": aid, "horizon": h, **((arms[aid].get("symbol") or {}).get(h) or {})}
            for aid in ARM_ORDER
            for h in (180, 300)
        ],
        "Band_Stability": kv_rows(stability),
        "Gates": kv_rows({
            **{f"{aid}_{k}": v for aid, g in vs.items() for k, v in g.items() if k not in ("day_improve_180", "day_improve_300")},
            **{f"{aid}_EDGE_{k}": v for aid, g in edges.items() for k, v in g.items()},
            **{k: decision.get(k) for k in ("CASE", "VERDICT", "PERSISTENCE_RULE_MECHANISM_SUPPORTED", "ENTRY_SIGNAL_EDGE_REPAIRED", "SELECTED_PERSISTENCE_BAND", "PRIMARY_DEFICIENCY_AFTER_V4", "NEXT")},
        }),
        "Signals": sig_rows or [{"empty": True}],
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "FAMILY_CLOSED": False, "ENTRY_RULE_CHANGED": False, "CONTROL_PARITY_V3": True}),
        "Non_Interference": kv_rows({
            **{f"{k}_BEFORE": pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT")},
            **{f"{k}_AFTER": post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT")},
            **adv,
            "NON_INTERFERENCE_PASS": ni_ok,
        }),
    }
    write_artifacts(report, sheets)
    print(
        f"DONE verdict={req.get('VERDICT')} mech={req.get('PERSISTENCE_RULE_MECHANISM_SUPPORTED')} "
        f"edge={req.get('ENTRY_SIGNAL_EDGE_REPAIRED')} band={req.get('SELECTED_PERSISTENCE_BAND')} "
        f"pre_vol={req.get('PRE_VOLUME_N')} ni={ni_ok} out={V4_PR_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
