"""Offline SIMPLE_TECH V3 exit-neutral Ask markout. No C14. No EXIT. No Runtime/Capture writes."""
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
    advanced as ni_advanced,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v3_analyze import (
    component_rca,
    concentration,
    day_rows,
    day_sign_counts,
    decide_case,
    entry_edge_gate,
    good6_pack,
    horizon_pack,
    passive_incompat_gate,
    passive_split,
)
from research.simple_tech_entry_family.v3_harvest import V3_CACHE, process_v3_day, save_v3_day_cache
from research.simple_tech_entry_family.v3_publish import REQUIRED_KEYS, build_markdown, kv_rows, write_artifacts
from research.simple_tech_entry_family.v3_spec import (
    ANALYSIS_ID,
    ASK_RUNTIME_ADOPTION_ALLOWED,
    C14_USED_FOR_SELECTION,
    EXIT_IMPLEMENTED,
    PARENT_SPEC_SHA256_EXPECTED,
    PARENT_STRATEGY_ID,
    RESEARCH_PARALLELISM,
    RUNTIME_ADOPTION_ALLOWED,
    STRATEGY_ID,
    TRUE_OOS,
    V1_LOCKED,
    canonical_v3_spec,
    spec_sha256_v3,
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
    "CONT_EXIT_600_N",
    "CONT_EXTEND_750_N",
    "FUTURE_ASK_USE_N",
    "MIDPOINT_ENTRY_N",
    "HORIZON_SELECTION_N",
    "ML_USE_N",
    "SCORE_USE_N",
    "TOPK_RANKING_N",
    "FUTURE_FEATURE_USE_N",
    "FUTURE_LABEL_AS_FEATURE_N",
    "PM_ROWS_USED_N",
    "ASK_RUNTIME_ADOPTION_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v3_sha: str, extra: dict[str, Any] | None = None) -> int:
    leak = dict(leak)
    leak.setdefault("LIVE_PROCESS_CONTROL_CALL_N", 0)
    leak.setdefault("RUNTIME_WRITE_N", 0)
    leak.setdefault("CAPTURE_WRITE_N", 0)
    leak.setdefault("ADDITIONAL_WEBSOCKET_N", 0)
    leak.setdefault("ACTIVE_CAPTURE_INPUT_N", leak.get("RESEARCH_INPUT_ACTIVE_FILE_N") or 0)
    req = {
        "STRATEGY_ID": STRATEGY_ID,
        "VERDICT": "SIMPLE_TECH_V3_INTEGRITY_FAILED",
        "NEXT": msg,
        "TRUE_OOS": False,
        "NON_INTERFERENCE_PASS": False,
        "ENTRY_SIGNAL_EDGE_SUPPORTED": False,
        "PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED": False,
        "PRIMARY_DEFICIENCY_AFTER_V3": "INTEGRITY_FAILURE",
        "PARENT_SPEC_SHA256": parent_sha,
        "V3_SPEC_SHA256": v3_sha,
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
    sheets = {
        "Precommit": kv_rows({"PARENT_SPEC_SHA256": parent_sha, "V3_SPEC_SHA256": v3_sha, "STRATEGY_ID": STRATEGY_ID}),
        "Non_Interference": kv_rows(pre),
        "Integrity": kv_rows({"blocker": msg, **leak}),
    }
    write_artifacts(report, sheets)
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v3_spec()
    v3_sha = spec_sha256_v3(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
    )
    print(
        f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} "
        f"parent={parent_sha[:12]} v3={v3_sha[:12]}",
        flush=True,
    )
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if spec.get("PARENT_SPEC_SHA256") != parent_sha:
        return _stop("STOP. V3 spec parent SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha, extra={"self_check": chk})
    if int(RESEARCH_PARALLELISM) != 1:
        return _stop("STOP. RESEARCH_PARALLELISM drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if SESSION != "AM":
        return _stop("STOP. SESSION not AM.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if RUNTIME_ADOPTION_ALLOWED or ASK_RUNTIME_ADOPTION_ALLOWED or EXIT_IMPLEMENTED or C14_USED_FOR_SELECTION:
        return _stop("STOP. Adoption/EXIT/C14 flags must stay false.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today active session.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live Runtime/Capture.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)

    v1_report = _load(V1_OUT / "report.json")
    v1_req = dict(v1_report.get("required") or {})
    if str(v1_req.get("SPEC_SHA256") or "") != parent_sha:
        return _stop("STOP. V1 report SHA does not match parent.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if int(v1_req.get("BOARD_PASS_N") or -1) != int(V1_LOCKED["SIGNAL_N"]):
        return _stop("STOP. V1 locked signal funnel drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _stop(f"STOP. Sealed inventory failed: {exc}", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    input_paths = [str(c.get("capture_path") or "") for c in caps]
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        input_paths,
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Research input references active Capture/Paper files.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _stop("STOP. Sealed Capture days incomplete.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha, extra={"caps": caps})

    V3_CACHE.mkdir(parents=True, exist_ok=True)
    v1_signals: list[dict[str, Any]] = []
    v1_funnel = {"s0": 0, "s1": 0, "s2": 0, "s3": 0, "s5": 0, "s6": 0}
    rows: list[dict[str, Any]] = []
    day_meta: list[dict[str, Any]] = []
    for cap in caps:
        day = str(cap["date"])
        v1_body = load_day_cache(CACHE / f"day_{day}.json", parent_sha)
        if not v1_body:
            return _stop(f"STOP. V1 day cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
        opps = list(v1_body.get("opps") or [])
        for k in v1_funnel:
            v1_funnel[k] += sum(1 for r in opps if r.get(k))
        day_sigs = list(v1_body.get("signals") or [])
        pm = sum(1 for r in day_sigs if str(r.get("session") or "AM") != "AM")
        leak["PM_ROWS_USED_N"] = int(leak.get("PM_ROWS_USED_N") or 0) + int(pm)
        v1_signals.extend(day_sigs)
        cache_p = V3_CACHE / f"day_{day}.json"
        body = load_day_cache(cache_p, v3_sha)
        if not body:
            print(f"{day} v3 harvest start signals={len(day_sigs)}", flush=True)
            body = process_v3_day(
                {
                    "date": day,
                    "capture_path": cap["capture_path"],
                    "signals": day_sigs,
                    "spec_sha": v3_sha,
                }
            )
            if body.get("ok"):
                save_v3_day_cache(cache_p, body)
        if not body.get("ok"):
            return _stop(
                f"STOP. Day harvest failed {day}: {body.get('blocker')}",
                pre=pre,
                leak=leak,
                parent_sha=parent_sha,
                v3_sha=v3_sha,
                extra={"day": body},
            )
        rows.extend(list(body.get("rows") or []))
        lk = body.get("leak") or {}
        for k in ("ITAYOSE_SKIP_N", "SPECIAL_SKIP_N", "INVALID_SKIP_N", "C14_REPLAY_N", "EXIT_SIM_N", "FUTURE_ASK_USE_N", "MIDPOINT_ENTRY_N"):
            leak[k] = int(leak.get(k) or 0) + int(lk.get(k) or 0)
        day_meta.append(
            {
                "date": day,
                "capture_path": cap["capture_path"],
                "v1_signal_n": len(day_sigs),
                "events_n": body.get("events_n"),
                "elapsed_sec": body.get("elapsed_sec"),
            }
        )

    if leak["PM_ROWS_USED_N"]:
        return _stop("STOP. PM opportunities present.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if len(v1_signals) != int(V1_LOCKED["SIGNAL_N"]):
        return _stop(
            f"STOP. V1 signal n={len(v1_signals)} != locked {V1_LOCKED['SIGNAL_N']}.",
            pre=pre,
            leak=leak,
            parent_sha=parent_sha,
            v3_sha=v3_sha,
        )
    fill_n = sum(1 for r in v1_signals if r.get("WOULD_FILL"))
    if fill_n != int(V1_LOCKED["PASSIVE_FILL_N"]):
        return _stop(f"STOP. V1 fill n={fill_n} != locked 3.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)
    if len(rows) != 29:
        return _stop(f"STOP. V3 row n={len(rows)} != 29.", pre=pre, leak=leak, parent_sha=parent_sha, v3_sha=v3_sha)

    exe = [r for r in rows if r.get("executable_signal")]
    pack = horizon_pack(exe)
    daily = day_rows(exe, list(ELIGIBLE_DAYS))
    d180 = day_sign_counts(daily, "MARKOUT180_MEAN")
    d300 = day_sign_counts(daily, "MARKOUT300_MEAN")
    d60 = day_sign_counts(daily, "MARKOUT60_MEAN")
    d30 = day_sign_counts(daily, "MARKOUT30_MEAN")
    c180 = concentration(exe, "markout_180")
    c300 = concentration(exe, "markout_300")
    c60 = concentration(exe, "markout_60")
    split = passive_split(exe)
    g6 = good6_pack(v1_signals, exe)
    rca = component_rca(exe)

    post = snapshot(phase="POST")
    adv = ni_advanced(pre, post)
    live_before = pre.get("RUNTIME_PID") is not None or pre.get("CAPTURE_PID") is not None
    ni_ok = (
        int(leak["LIVE_PROCESS_CONTROL_CALL_N"]) == 0
        and int(leak["RUNTIME_WRITE_N"]) == 0
        and int(leak["CAPTURE_WRITE_N"]) == 0
        and int(leak["ADDITIONAL_WEBSOCKET_N"]) == 0
        and int(leak["ACTIVE_CAPTURE_INPUT_N"]) == 0
        and int(leak["SUBMIT_N"]) == 0
        and int(leak["CANCEL_N"]) == 0
        and int(leak["LIVE_ORDER_N"]) == 0
        and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
        and int(leak.get("C14_REPLAY_N") or 0) == 0
        and int(leak.get("EXIT_SIM_N") or 0) == 0
    )
    if live_before:
        ni_ok = bool(
            ni_ok
            and adv.get("RUNTIME_PID_UNCHANGED")
            and adv.get("CAPTURE_PID_UNCHANGED")
            and adv.get("RUNTIME_STILL_ALIVE")
            and (adv.get("CAPTURE_STILL_ALIVE") if pre.get("CAPTURE_PID") is not None else True)
        )
    integ_ok = bool(
        ni_ok
        and int(leak.get("FUTURE_ASK_USE_N") or 0) == 0
        and int(leak.get("MIDPOINT_ENTRY_N") or 0) == 0
        and int(leak.get("HORIZON_SELECTION_N") or 0) == 0
        and int(leak.get("PM_ROWS_USED_N") or 0) == 0
        and int(leak.get("ASK_RUNTIME_ADOPTION_N") or 0) == 0
    )
    edge = entry_edge_gate(
        pack,
        d180,
        d300,
        c180,
        c300,
        executable_n=len(exe),
        signal_n=len(rows),
        integrity_ok=integ_ok,
    )
    pas = passive_incompat_gate(split)
    decision = decide_case(edge=edge, passive=pas, c180=c180, integrity_ok=integ_ok, rca=rca)
    if not ni_ok:
        decision["VERDICT"] = "SIMPLE_TECH_V3_INTEGRITY_FAILED"
        decision["NEXT"] = "NON_INTERFERENCE_FAIL"

    v1_parity = {
        "SIGNAL_N": len(v1_signals),
        "PASSIVE_FILL_N": fill_n,
        "SPEC_SHA256": parent_sha,
        "RAW_OPPORTUNITY_N": v1_funnel["s0"],
        "TREND_PASS_N": v1_funnel["s1"],
        "PULLBACK_PASS_N": v1_funnel["s2"],
        "RCI_PASS_N": v1_funnel["s3"],
        "VOLUME_PASS_N": v1_funnel["s5"],
        "BOARD_PASS_N": v1_funnel["s6"],
        "ok": (
            len(v1_signals) == 29
            and fill_n == 3
            and v1_funnel["s6"] == 29
            and parent_sha == PARENT_SPEC_SHA256_EXPECTED
        ),
    }
    req = {
        "STRATEGY_ID": STRATEGY_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V3_SPEC_SHA256": v3_sha,
        "V1_PARITY": v1_parity,
        "SIGNAL_N": len(rows),
        "EXECUTABLE_SIGNAL_N": len(exe),
        "MARKOUT_30_MEAN": pack.get("MARKOUT_30_MEAN"),
        "MARKOUT_30_MEDIAN": pack.get("MARKOUT_30_MEDIAN"),
        "MARKOUT_30_POS_RATE": pack.get("MARKOUT_30_POS_RATE"),
        "MARKOUT_60_MEAN": pack.get("MARKOUT_60_MEAN"),
        "MARKOUT_60_MEDIAN": pack.get("MARKOUT_60_MEDIAN"),
        "MARKOUT_60_POS_RATE": pack.get("MARKOUT_60_POS_RATE"),
        "MARKOUT_180_MEAN": pack.get("MARKOUT_180_MEAN"),
        "MARKOUT_180_MEDIAN": pack.get("MARKOUT_180_MEDIAN"),
        "MARKOUT_180_POS_RATE": pack.get("MARKOUT_180_POS_RATE"),
        "MARKOUT_300_MEAN": pack.get("MARKOUT_300_MEAN"),
        "MARKOUT_300_MEDIAN": pack.get("MARKOUT_300_MEDIAN"),
        "MARKOUT_300_POS_RATE": pack.get("MARKOUT_300_POS_RATE"),
        "COST_RECOVERY_RATE_300": pack.get("COST_RECOVERY_RATE_300"),
        "MFE_MEDIAN": pack.get("MFE_MEDIAN"),
        "MAE_MEDIAN": pack.get("MAE_MEDIAN"),
        "PASSIVE_FILLED_MARKOUT_180": split.get("PASSIVE_FILLED_MARKOUT_180"),
        "PASSIVE_NONFILLED_MARKOUT_180": split.get("PASSIVE_NONFILLED_MARKOUT_180"),
        "PASSIVE_FILLED_MARKOUT_300": split.get("PASSIVE_FILLED_MARKOUT_300"),
        "PASSIVE_NONFILLED_MARKOUT_300": split.get("PASSIVE_NONFILLED_MARKOUT_300"),
        "POSITIVE_DAY_N_180": d180.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N_180": d180.get("NEGATIVE_DAY_N"),
        "POSITIVE_DAY_N_300": d300.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N_300": d300.get("NEGATIVE_DAY_N"),
        "EX_BEST_DAY_MARKOUT_180": c180.get("EX_BEST_DAY_MARKOUT"),
        "EX_BEST_DAY_MARKOUT_300": c300.get("EX_BEST_DAY_MARKOUT"),
        "BEST_DAY_CONTRIBUTION_180": c180.get("BEST_DAY_CONTRIBUTION"),
        "TOP3_DAY_CONTRIBUTION_180": c180.get("TOP3_DAY_CONTRIBUTION"),
        "TOP_SYMBOL_CONTRIBUTION_180": c180.get("TOP_SYMBOL_CONTRIBUTION"),
        "GOOD_UPMOVE_6_ASK_EDGE": g6.get("GOOD_UPMOVE_6_ASK_EDGE"),
        "ENTRY_SIGNAL_EDGE_SUPPORTED": edge.get("ENTRY_SIGNAL_EDGE_SUPPORTED"),
        "PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED": pas.get("PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED"),
        "PRIMARY_DEFICIENCY_AFTER_V3": decision.get("PRIMARY_DEFICIENCY_AFTER_V3"),
        "TRUE_OOS": bool(TRUE_OOS),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "strategy_id": STRATEGY_ID,
        "parent_strategy_id": PARENT_STRATEGY_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "v1_parity": v1_parity,
        "v1_locked": V1_LOCKED,
        "horizon": pack,
        "edge_gate": edge,
        "passive_gate": pas,
        "passive_split": {k: split[k] for k in split if k not in ("filled", "nonfilled")},
        "passive_filled_pack": split.get("filled"),
        "passive_nonfilled_pack": split.get("nonfilled"),
        "good6": {k: g6[k] for k in g6 if k != "rows"},
        "day_sign_30": d30,
        "day_sign_60": d60,
        "day_sign_180": d180,
        "day_sign_300": d300,
        "concentration_60": c60,
        "concentration_180": c180,
        "concentration_300": c300,
        "component_rca": rca,
        "decision": decision,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "non_interference": adv,
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "c14_used_for_selection": False,
        "exit_implemented": False,
        "ask_runtime_adoption_allowed": False,
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    sig_keep = (
        "date",
        "symbol",
        "t0",
        "executable_signal",
        "ask_t0",
        "passive_filled",
        "markout_30",
        "markout_60",
        "markout_180",
        "markout_300",
        "cost_recovered_30",
        "cost_recovered_60",
        "cost_recovered_180",
        "cost_recovered_300",
        "mfe_bps",
        "mae_bps",
        "ask_reason",
        "volume",
        "vol_accel",
        "rci9",
    )
    sheets = {
        "Precommit": kv_rows(
            {
                "STRATEGY_ID": STRATEGY_ID,
                "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
                "PARENT_SPEC_SHA256": parent_sha,
                "V3_SPEC_SHA256": v3_sha,
                "C14_USED_FOR_SELECTION": False,
                "EXIT_IMPLEMENTED": False,
                "ASK_RUNTIME_ADOPTION_ALLOWED": False,
                "RESEARCH_PARALLELISM": RESEARCH_PARALLELISM,
                "TRUE_OOS": TRUE_OOS,
                "indicator_self_check": chk,
            }
        ),
        "V1_Parity": kv_rows(v1_parity),
        "Signals": [{k: r.get(k) for k in sig_keep} for r in rows] or [{"empty": True}],
        "Good6": [{k: r.get(k) for k in sig_keep} for r in (g6.get("rows") or [])] or [{"empty": True}],
        "Passive_Split": kv_rows({k: split[k] for k in split if k not in ("filled", "nonfilled")}),
        "Daily": daily,
        "Concentration": kv_rows({**{f"H180_{k}": v for k, v in c180.items()}, **{f"H300_{k}": v for k, v in c300.items()}}),
        "Component_RCA": kv_rows({k: rca.get(k) for k in rca if k != "splits"}),
        "Gates": kv_rows({**edge, **pas, **{k: decision.get(k) for k in ("CASE", "VERDICT", "PRIMARY_DEFICIENCY_AFTER_V3", "NEXT")}}),
        "Non_Interference": kv_rows(
            {
                **{f"{k}_BEFORE": pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
                **{f"{k}_AFTER": post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
                **adv,
                "NON_INTERFERENCE_PASS": ni_ok,
            }
        ),
        "Integrity": kv_rows({**leak, "RUNTIME_CHANGED": RUNTIME_CHANGED, "C14_CHANGED": False, "FAMILY_CLOSED": False}),
    }
    write_artifacts(report, sheets)
    print(
        f"DONE verdict={req.get('VERDICT')} edge={req.get('ENTRY_SIGNAL_EDGE_SUPPORTED')} "
        f"passive={req.get('PASSIVE_EXECUTION_INCOMPATIBILITY_SUPPORTED')} "
        f"exe={req.get('EXECUTABLE_SIGNAL_N')} m180={req.get('MARKOUT_180_MEAN')} "
        f"def={req.get('PRIMARY_DEFICIENCY_AFTER_V3')} ni={ni_ok} out={V3_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
