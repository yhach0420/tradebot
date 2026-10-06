"""Offline SIMPLE_TECH V5 Reversal quality RCA. No ENTRY change. No C14. No EXIT. No Persistence add."""
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
    V4_OUT,
    V4_PR_OUT,
    V5_OUT,
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.stages import good_upmove
from research.simple_tech_entry_family.v3_spec import PARENT_SPEC_SHA256_EXPECTED, V1_LOCKED
from research.simple_tech_entry_family.v4_analyze import axis_pack
from research.simple_tech_entry_family.v4_persistence_spec import V4_RCA_SPEC_SHA256_EXPECTED
from research.simple_tech_entry_family.v5_analyze import answers, cross_audit, good_vs_other, mechanism_gates, pick_mechanism, taxonomy_counts
from research.simple_tech_entry_family.v5_harvest import V5_CACHE, attach_rq_day, process_v5_day, save_v5_day_cache
from research.simple_tech_entry_family.v5_publish import REQUIRED_KEYS, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v5_spec import (
    ANALYSIS_ID,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    C14_USED_FOR_SELECTION,
    ENTRY_RULE_CHANGED,
    EXIT_IMPLEMENTED,
    PERSISTENCE_ADDED,
    PRE_REVERSAL_N_EXPECTED,
    RCI_CROSS_PASS_N_EXPECTED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    canonical_v5_spec,
    spec_sha256_v5,
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
    "FUTURE_ASK_USE_N",
    "FUTURE_FEATURE_USE_N",
    "ML_USE_N",
    "PM_ROWS_USED_N",
    "ASK_RUNTIME_ADOPTION_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _posneg(pack: dict[str, Any]) -> str:
    d = pack.get("DAY_180") or {}
    return f"{int(d.get('POSITIVE_RELATION_DAY_N') or 0)}/{int(d.get('NEGATIVE_RELATION_DAY_N') or 0)}"


def _exbest(pack: dict[str, Any]) -> str:
    return f"{pack.get('SPEARMAN_180_EX_BEST')}/{pack.get('SPEARMAN_300_EX_BEST')}"


def _droptop(pack: dict[str, Any]) -> str:
    return f"{pack.get('SPEARMAN_180_DROP_TOP_SYMBOL')}/{pack.get('SPEARMAN_300_DROP_TOP_SYMBOL')}"


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v5_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "STOP",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "SUPPORTED_REVERSAL_MECHANISM": "NONE",
        "PRIMARY_DEFICIENCY": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V5_SPEC_SHA256": v5_sha,
        "V1_PARITY": False,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {"analysis_id": ANALYSIS_ID, "blocker": msg, "required": req, "preflight": pre, "leak": leak, "extra": extra or {}, "_markdown": build_markdown({"required": req})}
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V5_SPEC_SHA256": v5_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v5_spec()
    v5_sha = spec_sha256_v5(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} parent={parent_sha[:12]} v5={v5_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    if RUNTIME_ADOPTION_ALLOWED or ASK_RUNTIME_ADOPTION_ALLOWED or EXIT_IMPLEMENTED or ENTRY_RULE_CHANGED or THRESHOLD_SEARCH or C14_USED_FOR_SELECTION or PERSISTENCE_ADDED:
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)

    v1_req = dict((_load(V1_OUT / "report.json").get("required") or {}))
    if str(v1_req.get("SPEC_SHA256") or "") != parent_sha:
        return _stop("STOP. V1 report SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    v4_req = dict((_load(V4_OUT / "report.json").get("required") or {}))
    if str(v4_req.get("V4_SPEC_SHA256") or "") != V4_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V4 RCA spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    if str(v4_req.get("SUPPORTED_VOLUME_MECHANISM") or "") != "PERSISTENCE":
        return _stop("STOP. V4 RCA mechanism is not PERSISTENCE.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    v4pr = dict((_load(V4_PR_OUT / "report.json").get("required") or {}))
    if v4pr.get("PERSISTENCE_RULE_MECHANISM_SUPPORTED") is not False:
        return _stop("STOP. V4 persistence-rule mechanism must remain false.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _stop("STOP. Sealed Capture incomplete.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)

    V5_CACHE.mkdir(parents=True, exist_ok=True)
    v1_signals: list[dict[str, Any]] = []
    funnel = {"s0": 0, "s1": 0, "s2": 0, "s3": 0, "s4": 0, "s5": 0, "s6": 0}
    rows: list[dict[str, Any]] = []
    day_meta: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        v1_body = load_day_cache(CACHE / f"day_{day}.json", parent_sha)
        if not v1_body:
            return _stop(f"STOP. V1 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
        opps = list(v1_body.get("opps") or [])
        for k in funnel:
            funnel[k] += sum(1 for r in opps if r.get(k))
        sigs = list(v1_body.get("signals") or [])
        v1_signals.extend(sigs)
        pre_rev = attach_rq_day(opps)
        pm = sum(1 for r in pre_rev if str(r.get("session") or "AM") != "AM")
        leak["PM_ROWS_USED_N"] = int(leak.get("PM_ROWS_USED_N") or 0) + int(pm)
        cache_p = V5_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, v5_sha)
        if not body:
            print(f"{day} v5 harvest start pre_reversal={len(pre_rev)}", flush=True)
            body = process_v5_day({"date": day, "capture_path": cap["capture_path"], "candidates": pre_rev, "spec_sha": v5_sha})
            if body.get("ok"):
                save_v5_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(f"STOP. Day harvest failed {day}: {body.get('blocker')}", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha, extra={"day": body})
        rows.extend(list(body.get("rows") or []))
        lk = body.get("leak") or {}
        for k in ("ITAYOSE_SKIP_N", "SPECIAL_SKIP_N", "INVALID_SKIP_N", "C14_REPLAY_N", "EXIT_SIM_N", "ENTRY_RULE_CHANGE_N", "THRESHOLD_SEARCH_N", "PERSISTENCE_AND_N"):
            leak[k] = int(leak.get(k) or 0) + int(lk.get(k) or 0)
        day_meta.append({"date": day, "pre_reversal_n": len(pre_rev), "events_n": body.get("events_n"), "elapsed_sec": body.get("elapsed_sec")})

    if leak["PM_ROWS_USED_N"]:
        return _stop("STOP. PM rows present.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    if len(v1_signals) != int(V1_LOCKED["SIGNAL_N"]) or funnel["s2"] != int(PRE_REVERSAL_N_EXPECTED) or funnel["s3"] != int(RCI_CROSS_PASS_N_EXPECTED) or funnel["s6"] != 29:
        return _stop(
            f"STOP. V1 parity failed signals={len(v1_signals)} s2={funnel['s2']} s3={funnel['s3']} s6={funnel['s6']}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v5_sha=v5_sha,
            extra={"funnel": funnel},
        )
    if len(rows) != funnel["s2"]:
        return _stop(f"STOP. PRE_REVERSAL row n={len(rows)} != s2={funnel['s2']}.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)

    exe = [r for r in rows if r.get("executable_signal")]
    pas = [r for r in rows if r.get("rci_cross")]
    if len(pas) != int(RCI_CROSS_PASS_N_EXPECTED):
        return _stop(f"STOP. RCI cross pass n={len(pas)} != {RCI_CROSS_PASS_N_EXPECTED}.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)

    p1 = axis_pack(exe, "RQ1")
    p2 = axis_pack(exe, "RQ2")
    p3 = axis_pack(exe, "RQ3")
    p4 = axis_pack(exe, "RQ4")
    pass_exe = [r for r in exe if r.get("rci_cross")]
    p1_pass = axis_pack(pass_exe, "RQ1") if len(pass_exe) >= 5 else {}
    p2_pass = axis_pack(pass_exe, "RQ2") if len(pass_exe) >= 5 else {}
    p3_pass = axis_pack(pass_exe, "RQ3") if len(pass_exe) >= 5 else {}
    p4_pass = axis_pack(pass_exe, "RQ4") if len(pass_exe) >= 5 else {}
    cross = cross_audit(exe)

    lost = [r for r in v1_signals if good_upmove(r) and not r.get("WOULD_FILL")]
    gkeys = {(str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0)) for r in lost}
    skeys = {(str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0)) for r in v1_signals}
    g6 = [r for r in rows if (str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0)) in gkeys]
    o23 = [
        r
        for r in rows
        if (str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0)) in skeys
        and (str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0)) not in gkeys
    ]
    if len(g6) != 6 or len(o23) != 23:
        return _stop(f"STOP. GOOD6/OTHER23 match failed n={len(g6)}/{len(o23)}.", pre=pre, leak=leak, parent_sha=parent_sha, v5_sha=v5_sha)
    gvo = good_vs_other(g6, o23)
    tax = taxonomy_counts(exe)
    gates = {
        "RQ1": mechanism_gates(p1, g6, o23, "RQ1"),
        "RQ2": mechanism_gates(p2, g6, o23, "RQ2"),
        "RQ3": mechanism_gates(p3, g6, o23, "RQ3"),
        "RQ4": mechanism_gates(p4, g6, o23, "RQ4"),
    }
    decision = pick_mechanism(gates)
    qa = answers(p1, p2, p3, p4, gvo, gates, cross, str(decision.get("SUPPORTED_REVERSAL_MECHANISM")))

    post = snapshot(phase="POST")
    adv = ni_advanced(pre, post)
    live_before = pre.get("RUNTIME_PID") is not None or pre.get("CAPTURE_PID") is not None
    ni_ok = all(int(leak.get(k) or 0) == 0 for k in (
        "LIVE_PROCESS_CONTROL_CALL_N", "RUNTIME_WRITE_N", "CAPTURE_WRITE_N", "ADDITIONAL_WEBSOCKET_N",
        "ACTIVE_CAPTURE_INPUT_N", "SUBMIT_N", "CANCEL_N", "LIVE_ORDER_N", "C14_REPLAY_N", "EXIT_SIM_N",
        "ENTRY_RULE_CHANGE_N", "THRESHOLD_SEARCH_N", "PERSISTENCE_AND_N",
    )) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    if live_before:
        ni_ok = bool(
            ni_ok
            and adv.get("RUNTIME_PID_UNCHANGED")
            and adv.get("CAPTURE_PID_UNCHANGED")
            and adv.get("RUNTIME_STILL_ALIVE")
            and (adv.get("CAPTURE_STILL_ALIVE") if pre.get("CAPTURE_PID") is not None else True)
        )
    if not ni_ok:
        decision = {
            "SUPPORTED_REVERSAL_MECHANISM": "NONE",
            "VERDICT": "STOP",
            "PRIMARY_DEFICIENCY": "INTEGRITY_FAILURE",
            "NEXT": "NON_INTERFERENCE_FAIL",
        }

    slim_axis = lambda p: {k: p.get(k) for k in p if k not in ("day_rows_180", "quartiles", "HALF_180", "HALF_300")}
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V5_SPEC_SHA256": v5_sha,
        "V1_PARITY": True,
        "PRE_REVERSAL_N": len(rows),
        "RCI_CROSS_PASS_N": len(pas),
        "EXECUTABLE_PRE_REVERSAL_N": len(exe),
        "RQ1_SPEARMAN_180": p1.get("SPEARMAN_180"),
        "RQ1_SPEARMAN_300": p1.get("SPEARMAN_300"),
        "RQ2_SPEARMAN_180": p2.get("SPEARMAN_180"),
        "RQ2_SPEARMAN_300": p2.get("SPEARMAN_300"),
        "RQ3_SPEARMAN_180": p3.get("SPEARMAN_180"),
        "RQ3_SPEARMAN_300": p3.get("SPEARMAN_300"),
        "RQ4_SPEARMAN_180": p4.get("SPEARMAN_180"),
        "RQ4_SPEARMAN_300": p4.get("SPEARMAN_300"),
        "RQ1_POS_NEG_DAYS": _posneg(p1),
        "RQ2_POS_NEG_DAYS": _posneg(p2),
        "RQ3_POS_NEG_DAYS": _posneg(p3),
        "RQ4_POS_NEG_DAYS": _posneg(p4),
        "RQ1_EX_BEST": _exbest(p1),
        "RQ2_EX_BEST": _exbest(p2),
        "RQ3_EX_BEST": _exbest(p3),
        "RQ4_EX_BEST": _exbest(p4),
        "RQ1_DROP_TOP_SYMBOL": _droptop(p1),
        "RQ2_DROP_TOP_SYMBOL": _droptop(p2),
        "RQ3_DROP_TOP_SYMBOL": _droptop(p3),
        "RQ4_DROP_TOP_SYMBOL": _droptop(p4),
        "GOOD6_RQ1": gvo.get("GOOD6_RQ1"),
        "GOOD6_RQ2": gvo.get("GOOD6_RQ2"),
        "GOOD6_RQ3": gvo.get("GOOD6_RQ3"),
        "GOOD6_RQ4": gvo.get("GOOD6_RQ4"),
        "OTHER23_RQ1": gvo.get("OTHER23_RQ1"),
        "OTHER23_RQ2": gvo.get("OTHER23_RQ2"),
        "OTHER23_RQ3": gvo.get("OTHER23_RQ3"),
        "OTHER23_RQ4": gvo.get("OTHER23_RQ4"),
        "SUPPORTED_REVERSAL_MECHANISM": decision.get("SUPPORTED_REVERSAL_MECHANISM"),
        "PRIMARY_DEFICIENCY": decision.get("PRIMARY_DEFICIENCY"),
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
        "questions": qa,
        "cross_audit": cross,
        "rq1": slim_axis(p1),
        "rq2": slim_axis(p2),
        "rq3": slim_axis(p3),
        "rq4": slim_axis(p4),
        "rq1_pass": slim_axis(p1_pass) if p1_pass else {},
        "rq2_pass": slim_axis(p2_pass) if p2_pass else {},
        "rq3_pass": slim_axis(p3_pass) if p3_pass else {},
        "rq4_pass": slim_axis(p4_pass) if p4_pass else {},
        "gates": gates,
        "decision": decision,
        "good_vs_other": gvo,
        "taxonomy": tax,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "non_interference": adv,
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "entry_rule_changed": False,
        "persistence_added": False,
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)

    keep = (
        "date", "symbol", "t0", "executable_signal", "rci_cross", "RQ1", "RQ2", "RQ3", "RQ4",
        "markout_60", "markout_180", "markout_300", "mfe_bps", "mae_bps", "price_follow", "taxonomy_join",
    )
    day_stab = []
    for feat, pack in (("RQ1", p1), ("RQ2", p2), ("RQ3", p3), ("RQ4", p4)):
        for d in pack.get("day_rows_180") or []:
            day_stab.append({"feature": feat, **d})
    qrows = []
    for feat, pack in (("RQ1", p1), ("RQ2", p2), ("RQ3", p3), ("RQ4", p4)):
        qrows.extend(pack.get("quartiles") or [])
    axis_keys = ("COVERAGE", "MISSING", "MEDIAN", "q25", "q50", "q75", "SPEARMAN_60", "SPEARMAN_180", "SPEARMAN_300", "SPEARMAN_180_EX_BEST", "SPEARMAN_180_DROP_TOP_SYMBOL")
    sheets = {
        "Precommit": kv_rows({
            "ANALYSIS_ID": ANALYSIS_ID,
            "PARENT_SPEC_SHA256": parent_sha,
            "V5_SPEC_SHA256": v5_sha,
            "ENTRY_RULE_CHANGED": False,
            "THRESHOLD_SEARCH": False,
            "PERSISTENCE_ADDED": False,
            "C14": False,
        }),
        "Population": [{"section": "funnel", **funnel}] + day_meta,
        "Cross_Audit": kv_rows(cross),
        "RQ1_Level": kv_rows({k: p1.get(k) for k in axis_keys}) + [p1.get("HALF_180") or {"half": "empty"}],
        "RQ2_Delta": kv_rows({k: p2.get(k) for k in axis_keys}) + [p2.get("HALF_180") or {"half": "empty"}],
        "RQ3_MultiBar": kv_rows({k: p3.get(k) for k in axis_keys}) + [p3.get("HALF_180") or {"half": "empty"}],
        "RQ4_Recovery": kv_rows({k: p4.get(k) for k in axis_keys}) + [p4.get("HALF_180") or {"half": "empty"}],
        "Quartiles": qrows or [{"empty": True}],
        "Day_Stability": day_stab or [{"empty": True}],
        "Good6": [{k: r.get(k) for k in keep} for r in g6] or [{"empty": True}],
        "Other23": [{k: r.get(k) for k in keep} for r in o23] or [{"empty": True}],
        "Failure_Taxonomy": tax,
        "Mechanism": kv_rows({**decision, **qa, **{f"{a}_{k}": v for a, g in gates.items() for k, v in g.items()}}),
        "Cross_Pass": [{k: r.get(k) for k in keep} for r in pas] or [{"empty": True}],
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "FAMILY_CLOSED": False, "ENTRY_RULE_CHANGED": False, "PERSISTENCE_ADDED": False}),
        "Non_Interference": kv_rows({
            **{f"{k}_BEFORE": pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT")},
            **{f"{k}_AFTER": post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT")},
            **adv,
            "NON_INTERFERENCE_PASS": ni_ok,
        }),
    }
    write_artifacts(report, sheets)
    print(
        f"DONE verdict={req.get('VERDICT')} mech={req.get('SUPPORTED_REVERSAL_MECHANISM')} "
        f"pre_rev={req.get('PRE_REVERSAL_N')} cross={req.get('RCI_CROSS_PASS_N')} "
        f"def={req.get('PRIMARY_DEFICIENCY')} ni={ni_ok} out={V5_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
