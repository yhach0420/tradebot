"""Offline SIMPLE_TECH V6 Trend vs Pullback stage RCA. No ENTRY change. No C14. No EXIT. No Persistence add."""
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
    V6_OUT,
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
from research.simple_tech_entry_family.v6_analyze import (
    AXIS_HIGHER_BETTER,
    PQ_AXES,
    TQ_AXES,
    answers,
    good_vs_other,
    pick_next,
    quality_gates,
    stage_audit,
)
from research.simple_tech_entry_family.v6_harvest import V6_CACHE, attach_tq_pq_day, process_v6_day, save_v6_day_cache
from research.simple_tech_entry_family.v6_publish import REQUIRED_KEYS, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v6_spec import (
    ANALYSIS_ID,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    BB_CHANGED,
    C14_USED_FOR_SELECTION,
    EMA_CHANGED,
    ENTRY_RULE_CHANGED,
    EXIT_IMPLEMENTED,
    PERSISTENCE_ADDED,
    PRE_TREND_N_EXPECTED,
    PULLBACK_PASS_N_EXPECTED,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    THRESHOLD_SEARCH,
    TREND_PASS_N_EXPECTED,
    TRUE_OOS,
    V5_SPEC_SHA256_EXPECTED,
    canonical_v6_spec,
    spec_sha256_v6,
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


def _scalarize(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if isinstance(v, (dict, list, tuple)):
            out[k] = json.dumps(v, ensure_ascii=False, default=str)
        else:
            out[k] = v
    return out


def _flat_stage(st: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in st.items():
        if k == "day_rows":
            continue
        if k in ("PASS", "FAIL") and isinstance(v, dict):
            for kk, vv in v.items():
                out[f"{k}_{kk}"] = vv
        elif isinstance(v, (dict, list, tuple)):
            out[k] = json.dumps(v, ensure_ascii=False, default=str)
        else:
            out[k] = v
    return out


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v6_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "STOP",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "SUPPORTED_TREND_MECHANISM": "NONE",
        "SUPPORTED_PULLBACK_MECHANISM": "NONE",
        "NEXT_COMPONENT": "NONE",
        "PRIMARY_DEFICIENCY": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V6_SPEC_SHA256": v6_sha,
        "V1_PARITY": False,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {"analysis_id": ANALYSIS_ID, "blocker": msg, "required": req, "preflight": pre, "leak": leak, "extra": extra or {}, "_markdown": build_markdown({"required": req})}
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V6_SPEC_SHA256": v6_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v6_spec()
    v6_sha = spec_sha256_v6(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} parent={parent_sha[:12]} v6={v6_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or ASK_RUNTIME_ADOPTION_ALLOWED
        or EXIT_IMPLEMENTED
        or ENTRY_RULE_CHANGED
        or THRESHOLD_SEARCH
        or C14_USED_FOR_SELECTION
        or PERSISTENCE_ADDED
        or RCI_CHANGED
        or EMA_CHANGED
        or BB_CHANGED
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)

    v1_req = dict((_load(V1_OUT / "report.json").get("required") or {}))
    if str(v1_req.get("SPEC_SHA256") or "") != parent_sha:
        return _stop("STOP. V1 report SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    v4_req = dict((_load(V4_OUT / "report.json").get("required") or {}))
    if str(v4_req.get("V4_SPEC_SHA256") or "") != V4_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V4 RCA spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if str(v4_req.get("SUPPORTED_VOLUME_MECHANISM") or "") != "PERSISTENCE":
        return _stop("STOP. V4 RCA mechanism is not PERSISTENCE.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    v4pr = dict((_load(V4_PR_OUT / "report.json").get("required") or {}))
    if v4pr.get("PERSISTENCE_RULE_MECHANISM_SUPPORTED") is not False:
        return _stop("STOP. V4 persistence-rule mechanism must remain false.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    v5_req = dict((_load(V5_OUT / "report.json").get("required") or {}))
    if str(v5_req.get("V5_SPEC_SHA256") or "") != V5_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V5 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if str(v5_req.get("SUPPORTED_REVERSAL_MECHANISM") or "") != "NONE":
        return _stop("STOP. V5 reversal mechanism must remain NONE.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _stop("STOP. Sealed Capture incomplete.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)

    V6_CACHE.mkdir(parents=True, exist_ok=True)
    v1_signals: list[dict[str, Any]] = []
    funnel = {"s0": 0, "s1": 0, "s2": 0, "s3": 0, "s4": 0, "s5": 0, "s6": 0}
    rows: list[dict[str, Any]] = []
    day_meta: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        v1_body = load_day_cache(CACHE / f"day_{day}.json", parent_sha)
        if not v1_body:
            return _stop(f"STOP. V1 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
        opps = list(v1_body.get("opps") or [])
        for k in funnel:
            funnel[k] += sum(1 for r in opps if r.get(k))
        sigs = list(v1_body.get("signals") or [])
        v1_signals.extend(sigs)
        pm = sum(1 for r in opps if str(r.get("session") or "AM") != "AM")
        leak["PM_ROWS_USED_N"] = int(leak.get("PM_ROWS_USED_N") or 0) + int(pm)
        cache_p = V6_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, v6_sha)
        if not body:
            pre_trend = attach_tq_pq_day(opps)
            print(f"{day} v6 harvest start pre_trend={len(pre_trend)}", flush=True)
            body = process_v6_day({"date": day, "capture_path": cap["capture_path"], "candidates": pre_trend, "spec_sha": v6_sha})
            if body.get("ok"):
                save_v6_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(f"STOP. Day harvest failed {day}: {body.get('blocker')}", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha, extra={"day": body})
        rows.extend(list(body.get("rows") or []))
        lk = body.get("leak") or {}
        for k in ("ITAYOSE_SKIP_N", "SPECIAL_SKIP_N", "INVALID_SKIP_N", "C14_REPLAY_N", "EXIT_SIM_N", "ENTRY_RULE_CHANGE_N", "THRESHOLD_SEARCH_N", "PERSISTENCE_AND_N", "RCI_CHANGE_N"):
            leak[k] = int(leak.get(k) or 0) + int(lk.get(k) or 0)
        day_meta.append({"date": day, "pre_trend_n": len(opps), "events_n": body.get("events_n"), "elapsed_sec": body.get("elapsed_sec")})

    if leak["PM_ROWS_USED_N"]:
        return _stop("STOP. PM rows present.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    if (
        len(v1_signals) != int(V1_LOCKED["SIGNAL_N"])
        or funnel["s0"] != int(PRE_TREND_N_EXPECTED)
        or funnel["s1"] != int(TREND_PASS_N_EXPECTED)
        or funnel["s2"] != int(PULLBACK_PASS_N_EXPECTED)
        or funnel["s6"] != 29
    ):
        return _stop(
            f"STOP. V1 parity failed signals={len(v1_signals)} s0={funnel['s0']} s1={funnel['s1']} s2={funnel['s2']} s6={funnel['s6']}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v6_sha=v6_sha,
            extra={"funnel": funnel},
        )
    if len(rows) != funnel["s0"]:
        return _stop(f"STOP. PRE_TREND row n={len(rows)} != s0={funnel['s0']}.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)

    trend_pass_n = sum(1 for r in rows if r.get("s1"))
    trend_fail_n = len(rows) - trend_pass_n
    pb_pass_n = sum(1 for r in rows if r.get("s1") and r.get("s2"))
    pb_fail_n = sum(1 for r in rows if r.get("s1") and not r.get("s2"))
    if trend_pass_n != int(TREND_PASS_N_EXPECTED) or pb_pass_n != int(PULLBACK_PASS_N_EXPECTED):
        return _stop(
            f"STOP. Nested pass n mismatch trend={trend_pass_n} pullback={pb_pass_n}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v6_sha=v6_sha,
        )

    exe = [r for r in rows if r.get("executable_signal")]
    trend_pass_exe = [r for r in exe if r.get("s1")]
    trend_fail_exe = [r for r in exe if not r.get("s1")]
    pb_pop_exe = [r for r in exe if r.get("s1")]
    pb_pass_exe = [r for r in pb_pop_exe if r.get("s2")]
    pb_fail_exe = [r for r in pb_pop_exe if not r.get("s2")]

    trend_stage = stage_audit(trend_pass_exe, trend_fail_exe, all_n=len(rows), pass_n=trend_pass_n, fail_n=trend_fail_n)
    pb_stage = stage_audit(pb_pass_exe, pb_fail_exe, all_n=trend_pass_n, pass_n=pb_pass_n, fail_n=pb_fail_n)

    tq_packs = {feat: axis_pack(exe, feat) for feat, _ in TQ_AXES}
    pq_packs = {feat: axis_pack(pb_pop_exe, feat) for feat, _ in PQ_AXES}
    tq_gates = {feat: quality_gates(tq_packs[feat], higher_better=bool(AXIS_HIGHER_BETTER[feat])) for feat, _ in TQ_AXES}
    pq_gates = {feat: quality_gates(pq_packs[feat], higher_better=bool(AXIS_HIGHER_BETTER[feat])) for feat, _ in PQ_AXES}

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
        return _stop(f"STOP. GOOD6/OTHER23 match failed n={len(g6)}/{len(o23)}.", pre=pre, leak=leak, parent_sha=parent_sha, v6_sha=v6_sha)
    gvo_tq = good_vs_other(g6, o23, TQ_AXES)
    gvo_pq = good_vs_other(g6, o23, PQ_AXES)
    gvo = {**gvo_tq, **{k: v for k, v in gvo_pq.items() if k not in ("GOOD6_N", "OTHER23_N", "diagnostic_only", "not_mechanism_gate", "good6_aligned_axes")}}
    gvo["good6_aligned_tq"] = gvo_tq.get("good6_aligned_axes")
    gvo["good6_aligned_pq"] = gvo_pq.get("good6_aligned_axes")

    decision = pick_next(trend_stage, pb_stage, tq_gates, pq_gates, tq_packs, pq_packs)
    qa = answers(trend_stage, pb_stage, tq_gates, pq_gates, decision, gvo)

    post = snapshot(phase="POST")
    adv = ni_advanced(pre, post)
    live_before = pre.get("RUNTIME_PID") is not None or pre.get("CAPTURE_PID") is not None
    ni_ok = all(
        int(leak.get(k) or 0) == 0
        for k in (
            "LIVE_PROCESS_CONTROL_CALL_N",
            "RUNTIME_WRITE_N",
            "CAPTURE_WRITE_N",
            "ADDITIONAL_WEBSOCKET_N",
            "ACTIVE_CAPTURE_INPUT_N",
            "SUBMIT_N",
            "CANCEL_N",
            "LIVE_ORDER_N",
            "C14_REPLAY_N",
            "EXIT_SIM_N",
            "ENTRY_RULE_CHANGE_N",
            "THRESHOLD_SEARCH_N",
            "PERSISTENCE_AND_N",
            "RCI_CHANGE_N",
            "EMA_CHANGE_N",
            "BB_CHANGE_N",
        )
    ) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
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
            "NEXT_COMPONENT": "NONE",
            "SUPPORTED_TREND_MECHANISM": "NONE",
            "SUPPORTED_PULLBACK_MECHANISM": "NONE",
            "VERDICT": "STOP",
            "PRIMARY_DEFICIENCY": "INTEGRITY_FAILURE",
            "NEXT": "NON_INTERFERENCE_FAIL",
            "TIEBREAK": "integrity",
        }

    slim_axis = lambda p: {k: p.get(k) for k in p if k not in ("day_rows_180", "quartiles", "HALF_180", "HALF_300")}
    tp = trend_stage.get("PASS") or {}
    tf = trend_stage.get("FAIL") or {}
    pp = pb_stage.get("PASS") or {}
    pf = pb_stage.get("FAIL") or {}
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V6_SPEC_SHA256": v6_sha,
        "V1_PARITY": True,
        "TREND_PASS_N": trend_pass_n,
        "TREND_FAIL_N": trend_fail_n,
        "TREND_PASS_MARKOUT_180": tp.get("MARKOUT_180_MEAN"),
        "TREND_PASS_MARKOUT_300": tp.get("MARKOUT_300_MEAN"),
        "TREND_FAIL_MARKOUT_180": tf.get("MARKOUT_180_MEAN"),
        "TREND_FAIL_MARKOUT_300": tf.get("MARKOUT_300_MEAN"),
        "PULLBACK_PASS_N": pb_pass_n,
        "PULLBACK_FAIL_N": pb_fail_n,
        "PULLBACK_PASS_MARKOUT_180": pp.get("MARKOUT_180_MEAN"),
        "PULLBACK_PASS_MARKOUT_300": pp.get("MARKOUT_300_MEAN"),
        "PULLBACK_FAIL_MARKOUT_180": pf.get("MARKOUT_180_MEAN"),
        "PULLBACK_FAIL_MARKOUT_300": pf.get("MARKOUT_300_MEAN"),
        "TQ1_SPEARMAN_180": tq_packs["TQ1"].get("SPEARMAN_180"),
        "TQ1_SPEARMAN_300": tq_packs["TQ1"].get("SPEARMAN_300"),
        "TQ2_SPEARMAN_180": tq_packs["TQ2"].get("SPEARMAN_180"),
        "TQ2_SPEARMAN_300": tq_packs["TQ2"].get("SPEARMAN_300"),
        "TQ3_SPEARMAN_180": tq_packs["TQ3"].get("SPEARMAN_180"),
        "TQ3_SPEARMAN_300": tq_packs["TQ3"].get("SPEARMAN_300"),
        "TQ4_SPEARMAN_180": tq_packs["TQ4"].get("SPEARMAN_180"),
        "TQ4_SPEARMAN_300": tq_packs["TQ4"].get("SPEARMAN_300"),
        "TQ1_POS_NEG_DAYS": _posneg(tq_packs["TQ1"]),
        "TQ2_POS_NEG_DAYS": _posneg(tq_packs["TQ2"]),
        "TQ3_POS_NEG_DAYS": _posneg(tq_packs["TQ3"]),
        "TQ4_POS_NEG_DAYS": _posneg(tq_packs["TQ4"]),
        "TQ1_EX_BEST": _exbest(tq_packs["TQ1"]),
        "TQ2_EX_BEST": _exbest(tq_packs["TQ2"]),
        "TQ3_EX_BEST": _exbest(tq_packs["TQ3"]),
        "TQ4_EX_BEST": _exbest(tq_packs["TQ4"]),
        "TQ1_DROP_TOP_SYMBOL": _droptop(tq_packs["TQ1"]),
        "TQ2_DROP_TOP_SYMBOL": _droptop(tq_packs["TQ2"]),
        "TQ3_DROP_TOP_SYMBOL": _droptop(tq_packs["TQ3"]),
        "TQ4_DROP_TOP_SYMBOL": _droptop(tq_packs["TQ4"]),
        "PQ1_SPEARMAN_180": pq_packs["PQ1"].get("SPEARMAN_180"),
        "PQ1_SPEARMAN_300": pq_packs["PQ1"].get("SPEARMAN_300"),
        "PQ2_SPEARMAN_180": pq_packs["PQ2"].get("SPEARMAN_180"),
        "PQ2_SPEARMAN_300": pq_packs["PQ2"].get("SPEARMAN_300"),
        "PQ3_SPEARMAN_180": pq_packs["PQ3"].get("SPEARMAN_180"),
        "PQ3_SPEARMAN_300": pq_packs["PQ3"].get("SPEARMAN_300"),
        "PQ4_SPEARMAN_180": pq_packs["PQ4"].get("SPEARMAN_180"),
        "PQ4_SPEARMAN_300": pq_packs["PQ4"].get("SPEARMAN_300"),
        "PQ1_POS_NEG_DAYS": _posneg(pq_packs["PQ1"]),
        "PQ2_POS_NEG_DAYS": _posneg(pq_packs["PQ2"]),
        "PQ3_POS_NEG_DAYS": _posneg(pq_packs["PQ3"]),
        "PQ4_POS_NEG_DAYS": _posneg(pq_packs["PQ4"]),
        "PQ1_EX_BEST": _exbest(pq_packs["PQ1"]),
        "PQ2_EX_BEST": _exbest(pq_packs["PQ2"]),
        "PQ3_EX_BEST": _exbest(pq_packs["PQ3"]),
        "PQ4_EX_BEST": _exbest(pq_packs["PQ4"]),
        "PQ1_DROP_TOP_SYMBOL": _droptop(pq_packs["PQ1"]),
        "PQ2_DROP_TOP_SYMBOL": _droptop(pq_packs["PQ2"]),
        "PQ3_DROP_TOP_SYMBOL": _droptop(pq_packs["PQ3"]),
        "PQ4_DROP_TOP_SYMBOL": _droptop(pq_packs["PQ4"]),
        "SUPPORTED_TREND_MECHANISM": decision.get("SUPPORTED_TREND_MECHANISM"),
        "SUPPORTED_PULLBACK_MECHANISM": decision.get("SUPPORTED_PULLBACK_MECHANISM"),
        "NEXT_COMPONENT": decision.get("NEXT_COMPONENT"),
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
        "trend_stage": {k: v for k, v in trend_stage.items() if k != "day_rows"},
        "pullback_stage": {k: v for k, v in pb_stage.items() if k != "day_rows"},
        "tq1": slim_axis(tq_packs["TQ1"]),
        "tq2": slim_axis(tq_packs["TQ2"]),
        "tq3": slim_axis(tq_packs["TQ3"]),
        "tq4": slim_axis(tq_packs["TQ4"]),
        "pq1": slim_axis(pq_packs["PQ1"]),
        "pq2": slim_axis(pq_packs["PQ2"]),
        "pq3": slim_axis(pq_packs["PQ3"]),
        "pq4": slim_axis(pq_packs["PQ4"]),
        "gates": {"tq": tq_gates, "pq": pq_gates},
        "decision": decision,
        "good_vs_other": gvo,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "non_interference": adv,
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "entry_rule_changed": False,
        "persistence_added": False,
        "rci_changed": False,
        "ema_changed": False,
        "bb_changed": False,
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)

    keep = (
        "date",
        "symbol",
        "t0",
        "executable_signal",
        "s1",
        "s2",
        "TQ1",
        "TQ2",
        "TQ3",
        "TQ4",
        "PQ1",
        "PQ2",
        "PQ3",
        "PQ4",
        "markout_60",
        "markout_180",
        "markout_300",
        "mfe_bps",
        "mae_bps",
    )
    day_stab = []
    for feat, pack in list(tq_packs.items()) + list(pq_packs.items()):
        for d in pack.get("day_rows_180") or []:
            day_stab.append({"feature": feat, **d})
    for rec in trend_stage.get("day_rows") or []:
        day_stab.append({"feature": "TREND_STAGE", **rec})
    for rec in pb_stage.get("day_rows") or []:
        day_stab.append({"feature": "PULLBACK_STAGE", **rec})
    qrows = []
    for feat, pack in list(tq_packs.items()) + list(pq_packs.items()):
        qrows.extend(pack.get("quartiles") or [])
    axis_keys = (
        "COVERAGE",
        "MISSING",
        "MEDIAN",
        "q25",
        "q50",
        "q75",
        "SPEARMAN_60",
        "SPEARMAN_180",
        "SPEARMAN_300",
        "SPEARMAN_180_EX_BEST",
        "SPEARMAN_300_EX_BEST",
        "SPEARMAN_180_DROP_TOP_SYMBOL",
        "SPEARMAN_300_DROP_TOP_SYMBOL",
    )
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "PARENT_SPEC_SHA256": parent_sha,
                "V6_SPEC_SHA256": v6_sha,
                "ENTRY_RULE_CHANGED": False,
                "THRESHOLD_SEARCH": False,
                "PERSISTENCE_ADDED": False,
                "RCI_CHANGED": False,
                "EMA_CHANGED": False,
                "BB_CHANGED": False,
                "C14": False,
                "GOOD6_DIAGNOSTIC_ONLY": True,
            }
        ),
        "Population": [{"section": "funnel", **funnel, "executable_n": len(exe)}] + day_meta,
        "Trend_Stage": kv_rows(_flat_stage(trend_stage)) + (trend_stage.get("day_rows") or []),
        "Pullback_Stage": kv_rows(_flat_stage(pb_stage)) + (pb_stage.get("day_rows") or []),
        "TQ1": kv_rows({k: tq_packs["TQ1"].get(k) for k in axis_keys}) + [tq_packs["TQ1"].get("HALF_180") or {"half": "empty"}] + [tq_gates["TQ1"]],
        "TQ2": kv_rows({k: tq_packs["TQ2"].get(k) for k in axis_keys}) + [tq_packs["TQ2"].get("HALF_180") or {"half": "empty"}] + [tq_gates["TQ2"]],
        "TQ3": kv_rows({k: tq_packs["TQ3"].get(k) for k in axis_keys}) + [tq_packs["TQ3"].get("HALF_180") or {"half": "empty"}] + [tq_gates["TQ3"]],
        "TQ4": kv_rows({k: tq_packs["TQ4"].get(k) for k in axis_keys}) + [tq_packs["TQ4"].get("HALF_180") or {"half": "empty"}] + [tq_gates["TQ4"]],
        "PQ1": kv_rows({k: pq_packs["PQ1"].get(k) for k in axis_keys}) + [pq_packs["PQ1"].get("HALF_180") or {"half": "empty"}] + [pq_gates["PQ1"]],
        "PQ2": kv_rows({k: pq_packs["PQ2"].get(k) for k in axis_keys}) + [pq_packs["PQ2"].get("HALF_180") or {"half": "empty"}] + [pq_gates["PQ2"]],
        "PQ3": kv_rows({k: pq_packs["PQ3"].get(k) for k in axis_keys}) + [pq_packs["PQ3"].get("HALF_180") or {"half": "empty"}] + [pq_gates["PQ3"]],
        "PQ4": kv_rows({k: pq_packs["PQ4"].get(k) for k in axis_keys}) + [pq_packs["PQ4"].get("HALF_180") or {"half": "empty"}] + [pq_gates["PQ4"]],
        "Quartiles": qrows or [{"empty": True}],
        "Day_Stability": day_stab or [{"empty": True}],
        "Good6": [{k: r.get(k) for k in keep} for r in g6] or [{"empty": True}],
        "Other23": [{k: r.get(k) for k in keep} for r in o23] or [{"empty": True}],
        "Mechanism": kv_rows(
            _scalarize(
                {
                    **decision,
                    **qa,
                    **{f"TQ_{a}_{k}": v for a, g in tq_gates.items() for k, v in g.items()},
                    **{f"PQ_{a}_{k}": v for a, g in pq_gates.items() for k, v in g.items()},
                }
            )
        ),
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "FAMILY_CLOSED": False, "ENTRY_RULE_CHANGED": False, "PERSISTENCE_ADDED": False, "RCI_CHANGED": False}),
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
        f"DONE verdict={req.get('VERDICT')} next={req.get('NEXT_COMPONENT')} "
        f"tq={req.get('SUPPORTED_TREND_MECHANISM')} pq={req.get('SUPPORTED_PULLBACK_MECHANISM')} "
        f"s0={funnel['s0']} s1={funnel['s1']} s2={funnel['s2']} "
        f"def={req.get('PRIMARY_DEFICIENCY')} ni={ni_ok} out={V6_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
