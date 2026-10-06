"""Offline SIMPLE_TECH V7 native timeframe role RCA. No ENTRY change. No C14. No EXIT. No mixed-TF strategy."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
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
from research.anchor_timing_robustness.grid import hm_epoch
from research.simple_tech_entry_family.harvest import CACHE, load_day_cache, sealed_day_caps
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.isolation import (
    TODAY,
    V1_OUT,
    V4_OUT,
    V4_PR_OUT,
    V5_OUT,
    V6_OUT,
    V6_PR_OUT,
    V7_OUT,
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.stages import good_upmove
from research.simple_tech_entry_family.v3_spec import PARENT_SPEC_SHA256_EXPECTED, V1_LOCKED
from research.simple_tech_entry_family.v4_persistence_spec import V4_RCA_SPEC_SHA256_EXPECTED
from research.simple_tech_entry_family.v6_spec import (
    PULLBACK_PASS_N_EXPECTED,
    PRE_TREND_N_EXPECTED,
    TREND_PASS_N_EXPECTED,
    V5_SPEC_SHA256_EXPECTED,
)
from research.simple_tech_entry_family.v6_pullback_spec import V6_RCA_SPEC_SHA256_EXPECTED
from research.simple_tech_entry_family.v7_analyze import (
    architecture_decision,
    pick_scale,
    role_audit,
    slim_stage,
)
from research.simple_tech_entry_family.v7_bars import self_check_agg
from research.simple_tech_entry_family.v7_harvest import V7_CACHE, process_v7_day, save_v7_day_cache
from research.simple_tech_entry_family.v7_publish import (
    REQUIRED_KEYS,
    ROLE_SHEET,
    _fmt_pack,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.simple_tech_entry_family.v7_spec import (
    ALL_ROLES,
    ANALYSIS_ID,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    BB_CHANGED,
    C14_USED_FOR_SELECTION,
    EMA_CHANGED,
    ENTRY_RULE_CHANGED,
    EXIT_IMPLEMENTED,
    MIXED_TF_STRATEGY,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    TF_IDS,
    TF_WIDTH_SEC,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    canonical_v7_spec,
    spec_sha256_v7,
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
    "FUTURE_BOARD_N",
    "FUTURE_BAR_N",
    "ML_USE_N",
    "PM_ROWS_USED_N",
    "ASK_RUNTIME_ADOPTION_N",
    "MIXED_TF_STRATEGY_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v7_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "VERDICT": "SIMPLE_TECH_V7_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "TREND_PREFERRED_SCALE": "NONE",
        "PULLBACK_PREFERRED_SCALE": "NONE",
        "RCI_PREFERRED_SCALE": "NONE",
        "PRICE_ACTION_PREFERRED_SCALE": "NONE",
        "VOLUME_PREFERRED_SCALE": "NONE",
        "ROLE_SCALE_CONFLICT": False,
        "MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED": False,
        "PARENT_SPEC_SHA256": parent_sha,
        "V7_SPEC_SHA256": v7_sha,
        "V1_PARITY": False,
    }
    for k in REQUIRED_KEYS:
        req.setdefault(k, None)
    report = {"analysis_id": ANALYSIS_ID, "blocker": msg, "required": req, "preflight": pre, "leak": leak, "extra": extra or {}, "_markdown": build_markdown({"required": req})}
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg, "V7_SPEC_SHA256": v7_sha}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def _good6_map(rows_by_tf: dict[str, list[dict[str, Any]]], lost: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index: dict[tuple[str, str, str], dict[float, dict[str, Any]]] = defaultdict(dict)
    for tf, xs in rows_by_tf.items():
        for r in xs:
            index[(tf, str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""))][float(r.get("bar_start") or 0.0)] = r
    out = []
    for g in lost:
        day = str(g.get("date"))
        sym = str(g.get("symbol") or "").replace(".T", "")
        bar_m = float(g.get("bar_minute") or 0.0)
        t0_1m = float(g.get("t0") or 0.0)
        rec: dict[str, Any] = {"date": day, "symbol": sym, "v1_t0": t0_1m, "v1_bar_minute": bar_m}
        am_start = float(hm_epoch(day, 9, 0))
        for tf in TF_IDS:
            width = float(TF_WIDTH_SEC[tf])
            off = bar_m - am_start
            k = int((off / width) + 1e-12) if off >= -1e-12 else -1
            start = am_start + float(k) * width if k >= 0 else None
            hit = None
            if start is not None:
                bucket = index.get((tf, day, sym)) or {}
                for bs, row in bucket.items():
                    if abs(float(bs) - float(start)) <= 1e-6:
                        hit = row
                        break
            rec[f"{tf}_found"] = hit is not None
            rec[f"{tf}_t0"] = None if hit is None else hit.get("t0")
            rec[f"{tf}_bar_start"] = None if hit is None else hit.get("bar_start")
            rec[f"{tf}_s1"] = None if hit is None else hit.get("s1")
            rec[f"{tf}_s2"] = None if hit is None else hit.get("s2")
            rec[f"{tf}_s3"] = None if hit is None else hit.get("s3")
            rec[f"{tf}_s4"] = None if hit is None else hit.get("s4")
            rec[f"{tf}_s5"] = None if hit is None else hit.get("s5")
            rec[f"{tf}_latency_sec"] = None if hit is None or hit.get("t0") is None else float(hit["t0"]) - t0_1m
        out.append(rec)
    return out


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v7_spec()
    v7_sha = spec_sha256_v7(spec)
    chk = self_check()
    agg_chk = self_check_agg()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} parent={parent_sha[:12]} v7={v7_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    if not chk.get("ok") or not agg_chk.get("ok"):
        return _stop("STOP. Indicator/bar aggregation self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha, extra={"agg": agg_chk})
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
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
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)

    v1_req = dict((_load(V1_OUT / "report.json").get("required") or {}))
    if str(v1_req.get("SPEC_SHA256") or "") != parent_sha:
        return _stop("STOP. V1 report SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    v4_req = dict((_load(V4_OUT / "report.json").get("required") or {}))
    if str(v4_req.get("V4_SPEC_SHA256") or "") != V4_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V4 RCA spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    v4pr = dict((_load(V4_PR_OUT / "report.json").get("required") or {}))
    if v4pr.get("PERSISTENCE_RULE_MECHANISM_SUPPORTED") is not False:
        return _stop("STOP. V4 persistence-rule mechanism must remain false.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    v5_req = dict((_load(V5_OUT / "report.json").get("required") or {}))
    if str(v5_req.get("V5_SPEC_SHA256") or "") != V5_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V5 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    v6_req = dict((_load(V6_OUT / "report.json").get("required") or {}))
    if str(v6_req.get("V6_SPEC_SHA256") or "") != V6_RCA_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V6 RCA spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    if str(v6_req.get("NEXT_COMPONENT") or "") != "PULLBACK":
        return _stop("STOP. V6 RCA NEXT_COMPONENT is not PULLBACK.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    v6pr = dict((_load(V6_PR_OUT / "report.json").get("required") or {}))
    if v6pr.get("PULLBACK_DEPTH_MECHANISM_SUPPORTED") is not False:
        return _stop("STOP. V6 pullback-rule mechanism must remain false.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    if str(v6pr.get("SELECTED_PULLBACK_RULE") or "") != "NONE":
        return _stop("STOP. V6 pullback selected rule must remain NONE.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [str(c.get("capture_path") or "") for c in caps],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _stop("STOP. Sealed Capture incomplete.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)

    V7_CACHE.mkdir(parents=True, exist_ok=True)
    v1_funnel = {"s0": 0, "s1": 0, "s2": 0, "s3": 0, "s4": 0, "s5": 0, "s6": 0}
    v1_signals: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    bar_rows: list[dict[str, Any]] = []
    day_meta: list[dict[str, Any]] = []
    rebuild_funnel = {tf: {k: 0 for k in ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7")} for tf in TF_IDS}
    v1_bar_n = 0
    tf1_bar_n = 0
    for cap in caps:
        day = str(cap["date"])
        v1_body = load_day_cache(CACHE / f"day_{day}.json", parent_sha)
        if not v1_body:
            return _stop(f"STOP. V1 cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
        opps = list(v1_body.get("opps") or [])
        for k in v1_funnel:
            v1_funnel[k] += sum(1 for r in opps if r.get(k))
        v1_signals.extend(list(v1_body.get("signals") or []))
        pm = sum(1 for r in opps if str(r.get("session") or "AM") != "AM")
        leak["PM_ROWS_USED_N"] = int(leak.get("PM_ROWS_USED_N") or 0) + int(pm)
        v1_bar_n += sum(int(r.get("bar_n") or 0) for r in list(v1_body.get("bar_rows") or []))
        cache_p = V7_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, v7_sha)
        if not body:
            print(f"{day} v7 harvest start", flush=True)
            body = process_v7_day(
                {
                    "date": day,
                    "capture_path": cap["capture_path"],
                    "universe": list(cap.get("universe_symbols") or []),
                    "spec_sha": v7_sha,
                }
            )
            if body.get("ok"):
                save_v7_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(f"STOP. Day harvest failed {day}: {body.get('blocker')}", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha, extra={"day": body})
        day_rows = list(body.get("rows") or [])
        rows.extend(day_rows)
        br = list(body.get("bar_rows") or [])
        bar_rows.extend(br)
        tf1_bar_n += sum(int(r.get("bar_n") or 0) for r in br if str(r.get("tf") or "") == "TF1")
        fn = dict(body.get("funnels") or {})
        for tf in TF_IDS:
            src = dict(fn.get(tf) or {})
            for k in rebuild_funnel[tf]:
                rebuild_funnel[tf][k] += int(src.get(k) or 0)
        lk = body.get("leak") or {}
        for k in (
            "ITAYOSE_SKIP_N",
            "SPECIAL_SKIP_N",
            "INVALID_SKIP_N",
            "C14_REPLAY_N",
            "EXIT_SIM_N",
            "ENTRY_RULE_CHANGE_N",
            "THRESHOLD_SEARCH_N",
            "PERSISTENCE_AND_N",
            "RCI_CHANGE_N",
            "EMA_CHANGE_N",
            "BB_CHANGE_N",
            "MIXED_TF_STRATEGY_N",
            "FUTURE_BOARD_N",
            "FUTURE_BAR_N",
            "FUTURE_FEATURE_USE_N",
            "PARTIAL_BUCKET_N",
            "GAP_BUCKET_N",
            "LATE_FINALIZE_N",
        ):
            leak[k] = int(leak.get(k) or 0) + int(lk.get(k) or 0)
        day_meta.append(
            {
                "date": day,
                "events_n": body.get("events_n"),
                "elapsed_sec": body.get("elapsed_sec"),
                "tf1_s0": (fn.get("TF1") or {}).get("s0"),
                "tf3_s0": (fn.get("TF3") or {}).get("s0"),
                "tf5_s0": (fn.get("TF5") or {}).get("s0"),
            }
        )

    if leak["PM_ROWS_USED_N"]:
        return _stop("STOP. PM rows present.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)
    tf1 = rebuild_funnel["TF1"]
    v1_ok = (
        len(v1_signals) == int(V1_LOCKED["SIGNAL_N"])
        and v1_funnel["s0"] == int(PRE_TREND_N_EXPECTED)
        and v1_funnel["s1"] == int(TREND_PASS_N_EXPECTED)
        and v1_funnel["s2"] == int(PULLBACK_PASS_N_EXPECTED)
        and v1_funnel["s6"] == 29
        and tf1["s0"] == int(PRE_TREND_N_EXPECTED)
        and tf1["s1"] == int(TREND_PASS_N_EXPECTED)
        and tf1["s2"] == int(PULLBACK_PASS_N_EXPECTED)
        and tf1["s3"] == int(v1_funnel["s3"])
        and tf1["s4"] == int(v1_funnel["s4"])
        and tf1["s5"] == int(v1_funnel["s5"])
        and tf1["s6"] == 29
        and tf1_bar_n == v1_bar_n
    )
    if not v1_ok:
        return _stop(
            f"STOP. 1m V1 parity failed tf1={tf1} v1={v1_funnel} bars={tf1_bar_n}/{v1_bar_n} signals={len(v1_signals)}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v7_sha=v7_sha,
            extra={"tf1": tf1, "v1": v1_funnel},
        )
    if int(leak.get("FUTURE_BAR_N") or 0) or int(leak.get("FUTURE_BOARD_N") or 0):
        return _stop("STOP. Causal bar/board alignment failed.", pre=pre, leak=leak, parent_sha=parent_sha, v7_sha=v7_sha)

    by_tf: dict[str, list[dict[str, Any]]] = {tf: [] for tf in TF_IDS}
    for r in rows:
        tf = str(r.get("tf") or "")
        if tf in by_tf:
            by_tf[tf].append(r)

    post = snapshot(phase="POST")
    adv = ni_advanced(pre, post)
    live_before = pre.get("RUNTIME_PID") is not None or pre.get("CAPTURE_PID") is not None
    ni_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO if k not in ("ITAYOSE_SKIP_N",)) and int(
        leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0
    ) == 0
    ni_ok = bool(
        ni_ok
        and int(leak.get("C14_REPLAY_N") or 0) == 0
        and int(leak.get("EXIT_SIM_N") or 0) == 0
        and int(leak.get("MIXED_TF_STRATEGY_N") or 0) == 0
        and int(leak.get("FUTURE_BAR_N") or 0) == 0
        and int(leak.get("FUTURE_BOARD_N") or 0) == 0
        and int(leak.get("PM_ROWS_USED_N") or 0) == 0
    )
    if live_before:
        ni_ok = bool(
            ni_ok
            and adv.get("RUNTIME_PID_UNCHANGED")
            and adv.get("CAPTURE_PID_UNCHANGED")
            and (adv.get("RUNTIME_STILL_ALIVE") if pre.get("RUNTIME_PID") is not None else True)
            and (adv.get("CAPTURE_STILL_ALIVE") if pre.get("CAPTURE_PID") is not None else True)
        )
    integ_ok = bool(ni_ok and v1_ok)

    roles: dict[str, dict[str, Any]] = {}
    for role in list(ALL_ROLES) + ["VOLUME_NESTED"]:
        roles[role] = {}
        for tf in TF_IDS:
            roles[role][tf] = role_audit(by_tf[tf], role, integrity_ok=integ_ok)

    pref = {
        "TREND": pick_scale(roles["TREND"]),
        "PULLBACK": pick_scale(roles["PULLBACK"]),
        "RCI": pick_scale(roles["RCI"]),
        "PRICE_ACTION": pick_scale(roles["PRICE_ACTION"]),
        "VOLUME": pick_scale(roles["VOLUME"]),
    }
    supported_any = {role: any(bool((roles[role][tf]).get("ROLE_SUPPORTED")) for tf in TF_IDS) for role in ALL_ROLES}
    decision = architecture_decision(pref, supported_any)
    if not integ_ok:
        decision = {
            "ROLE_SCALE_CONFLICT": False,
            "MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED": False,
            "SINGLE_TF_SCALE": None,
            "VERDICT": "SIMPLE_TECH_V7_INTEGRITY_FAILED",
            "NEXT": "NON_INTERFERENCE_FAIL",
        }

    lost = [r for r in v1_signals if good_upmove(r) and not r.get("WOULD_FILL")]
    g6 = _good6_map(by_tf, lost)

    board_audit = []
    for tf in TF_IDS:
        xs = by_tf[tf]
        board_audit.append(
            {
                "tf": tf,
                "n": len(xs),
                "board_ok_n": sum(1 for r in xs if r.get("board_ok")),
                "executable_n": sum(1 for r in xs if r.get("executable_signal")),
                "future_board_n": 0,
            }
        )

    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V7_SPEC_SHA256": v7_sha,
        "V1_PARITY": True,
        "TREND_PREFERRED_SCALE": pref["TREND"],
        "PULLBACK_PREFERRED_SCALE": pref["PULLBACK"],
        "RCI_PREFERRED_SCALE": pref["RCI"],
        "PRICE_ACTION_PREFERRED_SCALE": pref["PRICE_ACTION"],
        "VOLUME_PREFERRED_SCALE": pref["VOLUME"],
        "ROLE_SCALE_CONFLICT": decision.get("ROLE_SCALE_CONFLICT"),
        "MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED": decision.get("MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED"),
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
        "agg_self_check": agg_chk,
        "v1_funnel": v1_funnel,
        "rebuild_funnel": rebuild_funnel,
        "roles": {role: {tf: slim_stage(roles[role][tf]) for tf in TF_IDS} for role in roles},
        "preferred": pref,
        "decision": decision,
        "good6": g6,
        "board_audit": board_audit,
        "bar_alignment": {
            "v1_bar_n": v1_bar_n,
            "tf1_bar_n": tf1_bar_n,
            "future_bar_n": leak.get("FUTURE_BAR_N"),
            "partial_bucket_n": leak.get("PARTIAL_BUCKET_N"),
            "gap_bucket_n": leak.get("GAP_BUCKET_N"),
            "late_finalize_n": leak.get("LATE_FINALIZE_N"),
        },
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "non_interference": adv,
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "entry_rule_changed": False,
        "mixed_tf_strategy": False,
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)

    day_stab = []
    role_sheets: dict[str, list[dict[str, Any]]] = {name: [] for name in ROLE_SHEET.values()}
    for role, sheet in ROLE_SHEET.items():
        for tf in TF_IDS:
            st = roles[role][tf]
            role_sheets[sheet].append(_fmt_pack(tf, role, st))
            for rec in st.get("day_rows") or []:
                day_stab.append({"role": role, "tf": tf, **rec})

    pop_rows = [{"section": "v1", **v1_funnel}] + [{"section": tf, **rebuild_funnel[tf]} for tf in TF_IDS] + day_meta
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "PARENT_SPEC_SHA256": parent_sha,
                "V7_SPEC_SHA256": v7_sha,
                "ENTRY_RULE_CHANGED": False,
                "THRESHOLD_SEARCH": False,
                "MIXED_TF_STRATEGY": False,
                "RCI_CHANGED": False,
                "EMA_CHANGED": False,
                "BB_CHANGED": False,
                "C14": False,
                "GOOD6_DIAGNOSTIC_ONLY": True,
                "HORIZONS": "60/180/300",
                "NATIVE_PERIODS": "EMA9/EMA21/BB20/RCI9",
            }
        ),
        "Population": pop_rows,
        "Bar_Alignment": [
            {"v1_bar_n": v1_bar_n, "tf1_bar_n": tf1_bar_n, "match": tf1_bar_n == v1_bar_n, **{k: leak.get(k) for k in ("FUTURE_BAR_N", "PARTIAL_BUCKET_N", "GAP_BUCKET_N", "LATE_FINALIZE_N")}}
        ],
        "Trend": role_sheets["Trend"],
        "Pullback": role_sheets["Pullback"],
        "RCI": role_sheets["RCI"],
        "Price_Action": role_sheets["Price_Action"],
        "Volume": role_sheets["Volume"],
        "Volume_Nested": role_sheets["Volume_Nested"],
        "Preferred_Scale": kv_rows({**pref, **decision, **req}),
        "Good6": g6 or [{"empty": True}],
        "Board_Audit": board_audit,
        "Day_Stability": day_stab or [{"empty": True}],
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "ENTRY_RULE_CHANGED": False, "MIXED_TF_STRATEGY": False}),
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
        f"DONE verdict={req.get('VERDICT')} "
        f"trend={pref['TREND']} pb={pref['PULLBACK']} rci={pref['RCI']} pa={pref['PRICE_ACTION']} vol={pref['VOLUME']} "
        f"multi={req.get('MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED')} ni={ni_ok} out={V7_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
