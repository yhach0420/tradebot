"""Offline VWAP Rejection/Reclaim V1. Duplicate check first. Development first. Freeze before holdout. No Runtime write."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.new_entry_vwap_rejection_reclaim_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FAMILY,
    FORBIDDEN_INPUT_DAYS,
    LABEL_DEV,
    LABEL_HOLDOUT,
    LABEL_STRESS,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    PARAMETER_SEARCH_PERFORMED,
    PROSPECTIVE_HARVEST_SUSPENDED,
    STRESS_DAYS,
    TRUE_OOS,
)
from research.new_entry_vwap_rejection_reclaim_v1.already_executed import already_executed_check
from research.new_entry_vwap_rejection_reclaim_v1.analyze import (
    build_answers,
    decay_ratios,
    decide,
    dev_gates,
    holdout_gates,
    integrity_ok,
    leakage_n,
    pack_screen,
    pack_split,
    screen_pass,
    stress_gates,
)
from research.new_entry_vwap_rejection_reclaim_v1.harvest import (
    AUDIT,
    freeze_path,
    harvest_split,
    holdout_decision_path,
    set_signal_mode,
)
from research.new_entry_vwap_rejection_reclaim_v1.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.new_entry_vwap_rejection_reclaim_v1.publish import build_markdown, kv_rows, write_artifacts
from research.new_entry_vwap_rejection_reclaim_v1.rule import EXACT_RULE_TEXT
from research.new_entry_vwap_rejection_reclaim_v1.spec import canonical_spec, source_sha256, spec_sha256

JST = ZoneInfo("Asia/Tokyo")
CTX: dict[str, Any] = {"already_executed": {}, "FALLBACK_USED": False}


def _slim(p: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in p.items() if k not in ("daily", "rows", "EXACT_RULE_TEXT")}


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    scr = report.get("execution_screen") or {}
    sheets = {
        "answers": kv_rows(report.get("answers") or {}),
        "precommit": kv_rows(report.get("precommit") or {}),
        "already_executed": list((report.get("already_executed") or {}).get("comparisons") or [{"empty": True}]),
        "pin": kv_rows(report.get("pin") or {}),
        "development": kv_rows(_slim(report.get("development") or {})),
        "dev_daily": list((report.get("development") or {}).get("daily") or [{"empty": True}]),
        "holdout": kv_rows(_slim(report.get("holdout") or {})),
        "holdout_daily": list((report.get("holdout") or {}).get("daily") or [{"empty": True}]),
        "stress": kv_rows(_slim(report.get("stress") or {})),
        "stress_daily": list((report.get("stress") or {}).get("daily") or [{"empty": True}]),
        "execution_screen": kv_rows(
            {
                "RAN": report.get("EXECUTION_SCREEN_RAN"),
                "EDGE_MAINTAINED": report.get("EXECUTION_EDGE_MAINTAINED"),
                "DEV": _slim(scr.get("DEV") or {}),
                "HOLDOUT": _slim(scr.get("HOLDOUT") or {}),
                "STRESS": _slim(scr.get("STRESS") or {}),
            }
        ),
        "leakage": kv_rows(report.get("leakage") or {}),
        "decision": kv_rows(report.get("decision") or {}),
        "safety": kv_rows(report.get("safety") or {}),
    }
    write_artifacts(report, sheets)


def _base_report(**kwargs: Any) -> dict[str, Any]:
    spec = canonical_spec()
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "FAMILY": FAMILY,
        "LABEL_DEV": LABEL_DEV,
        "LABEL_HOLDOUT": LABEL_HOLDOUT,
        "LABEL_STRESS": LABEL_STRESS,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "LOCKED_HOLDOUT_DAYS": list(LOCKED_HOLDOUT_DAYS),
        "STRESS_DAYS": list(STRESS_DAYS),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "PARAMETER_SEARCH_PERFORMED": PARAMETER_SEARCH_PERFORMED,
        "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
        "FALLBACK_USED": bool(CTX.get("FALLBACK_USED")),
        "already_executed": dict(CTX.get("already_executed") or {}),
        "precommit": spec,
        "STRESS_OPENED": False,
        "EXECUTION_SCREEN_RAN": False,
        "EXECUTION_EDGE_MAINTAINED": False,
        "HOLDOUT_OPENED": False,
        "development": {},
        "holdout": {},
        "stress": {},
        "execution_screen": {},
        "dev_gates": {},
        "holdout_gates": {},
        "stress_gates": {},
        "decay": {},
        "safety": {
            "SUBMIT_N": 0,
            "CANCEL_N": 0,
            "LIVE_ORDER_N": 0,
            "ENTRY_RUNTIME_CHANGED": False,
            "EXIT_RUNTIME_CHANGED": False,
            "FULL_CAUSAL_ALLOWED": False,
            "SIZING_ALLOWED": False,
            "FUTURE_DATA_USED": False,
        },
        **kwargs,
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE NEW ENTRY VWAP REJECTION RECLAIM V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("HOLDOUT sealed until DEVELOPMENT PASS + rule freeze.", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(before.get("ACTIVE_CAPTURE_PATH") or ""), str(before.get("ACTIVE_PAPER_SESSION") or ""))
    if overlap:
        AUDIT["SPLIT_LEAKAGE_N"] += 1

    out_rep = OUT / "report.json"
    if out_rep.is_file():
        prev = json.loads(out_rep.read_text(encoding="utf-8"))
        if str(prev.get("ANALYSIS_ID") or "") == ANALYSIS_ID:
            print("ALREADY_EXECUTED. Reusing OUT. No re-run.", flush=True)
            print(f"VERDICT {((prev.get('decision') or {}).get('VERDICT'))}", flush=True)
            print("STOP.", flush=True)
            return 0

    ae = already_executed_check()
    print(f"DUPLICATE_ARCHITECTURE {ae.get('DUPLICATE_ARCHITECTURE')}", flush=True)
    print(f"VWAP_STRUCTURALLY_UNAVAILABLE {ae.get('PRIMARY_REQUIRED_VWAP_DATA_STRUCTURALLY_UNAVAILABLE')}", flush=True)
    fallback_used = False
    if bool(ae.get("FALLBACK_ALLOWED")):
        set_signal_mode("FALLBACK")
        fallback_used = True
        ae["FALLBACK_USED"] = True
        print("PRIMARY skipped. PRECOMMITTED_FALLBACK FAILED_BREAKDOWN_RECLAIM", flush=True)
    else:
        set_signal_mode("PRIMARY")
        ae["FALLBACK_USED"] = False
        print("PRIMARY VWAP_REJECTION_RECLAIM. Fallback sealed.", flush=True)
    CTX["already_executed"] = ae
    CTX["FALLBACK_USED"] = bool(ae.get("FALLBACK_USED"))

    spec = canonical_spec()
    sha = spec_sha256(spec)
    src_sha = source_sha256()
    print(f"SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)
    print(f"RULE {EXACT_RULE_TEXT}", flush=True)

    if any(d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE for d in DEVELOPMENT_DAYS):
        AUDIT["FUTURE_DATA_N"] += 1

    print("PHASE DEVELOPMENT - holdout/stress not streamed", flush=True)
    dev_h = harvest_split("DEVELOPMENT", DEVELOPMENT_DAYS)
    if not dev_h.get("ok"):
        decision = decide(
            integrity=False,
            dev_gate=None,
            hold_gate=None,
            stress_gate=None,
            exec_ok=None,
            holdout_opened=False,
            stress_opened=False,
            exec_ran=False,
        )
        report = _base_report(
            pin={"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha, "ENTRY_RULE_FROZEN": False, "HOLDOUT_OPENED_AFTER_FREEZE": False},
            leakage=leakage_n(),
            decision=decision,
            STOP_REASON=str(dev_h.get("blocker")),
            isolation_before=before,
        )
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    dev_pack = pack_split(list(dev_h.get("rows") or []), list(DEVELOPMENT_DAYS))
    dg = dev_gates(dev_pack)
    print(
        f"DEV signal={dev_pack.get('SIGNAL_N')} exe={dev_pack.get('EXECUTION_EVALUABLE_N')} "
        f"mean180={dev_pack.get('MEAN_180')} mean300={dev_pack.get('MEAN_300')} PASS={dg.get('PASS')}",
        flush=True,
    )

    integ = integrity_ok() and overlap == 0
    if not integ or not dg.get("PASS"):
        decision = decide(
            integrity=integ,
            dev_gate=dg,
            hold_gate=None,
            stress_gate=None,
            exec_ok=None,
            holdout_opened=False,
            stress_opened=False,
            exec_ran=False,
        )
        after = snapshot(phase="POST")
        report = _base_report(
            pin={"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha, "ENTRY_RULE_FROZEN": False, "HOLDOUT_OPENED_AFTER_FREEZE": False},
            development=dev_pack,
            dev_gates=dg,
            leakage=leakage_n(),
            decision=decision,
            isolation_before=before,
            isolation_after=after,
            isolation_advanced=advanced(before, after),
        )
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 0

    freeze_ts = datetime.now(JST).isoformat(timespec="seconds")
    freeze_body = {
        "ENTRY_RULE_FROZEN": True,
        "HOLDOUT_OPENED_AFTER_FREEZE": True,
        "FREEZE_TIMESTAMP": freeze_ts,
        "SPEC_SHA256": sha,
        "SOURCE_SHA256": src_sha,
        "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
        "ANALYSIS_ID": ANALYSIS_ID,
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    freeze_path().write_text(json.dumps(freeze_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FREEZE {freeze_ts} spec={sha}", flush=True)
    print("PHASE LOCKED_HOLDOUT - first use after freeze", flush=True)

    hold_h = harvest_split("HOLDOUT", LOCKED_HOLDOUT_DAYS)
    if not hold_h.get("ok"):
        decision = decide(
            integrity=False,
            dev_gate=dg,
            hold_gate=None,
            stress_gate=None,
            exec_ok=None,
            holdout_opened=True,
            stress_opened=False,
            exec_ran=False,
        )
        report = _base_report(
            pin=freeze_body,
            development=dev_pack,
            dev_gates=dg,
            HOLDOUT_OPENED=True,
            leakage=leakage_n(),
            decision=decision,
            STOP_REASON=str(hold_h.get("blocker")),
        )
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    hold_pack = pack_split(list(hold_h.get("rows") or []), list(LOCKED_HOLDOUT_DAYS))
    hg = holdout_gates(hold_pack)
    decay = decay_ratios(dev_pack, hold_pack)
    print(
        f"HOLDOUT exe={hold_pack.get('EXECUTION_EVALUABLE_N')} mean180={hold_pack.get('MEAN_180')} "
        f"mean300={hold_pack.get('MEAN_300')} PASS={hg.get('PASS')} decay={decay}",
        flush=True,
    )
    holdout_decision_path().write_text(
        json.dumps({"HOLDOUT_PASS": bool(hg.get("PASS")), "SPEC_SHA256": sha}, indent=2) + "\n",
        encoding="utf-8",
    )

    if not integrity_ok() or not hg.get("PASS"):
        decision = decide(
            integrity=integrity_ok(),
            dev_gate=dg,
            hold_gate=hg,
            stress_gate=None,
            exec_ok=None,
            holdout_opened=True,
            stress_opened=False,
            exec_ran=False,
        )
        after = snapshot(phase="POST")
        report = _base_report(
            pin=freeze_body,
            development=dev_pack,
            holdout=hold_pack,
            dev_gates=dg,
            holdout_gates=hg,
            decay=decay,
            HOLDOUT_OPENED=True,
            leakage=leakage_n(),
            decision=decision,
            isolation_before=before,
            isolation_after=after,
            isolation_advanced=advanced(before, after),
        )
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 0

    print("PHASE REUSED_HISTORY_STRESS - after holdout PASS", flush=True)
    st_h = harvest_split("STRESS", STRESS_DAYS)
    if not st_h.get("ok"):
        decision = decide(
            integrity=False,
            dev_gate=dg,
            hold_gate=hg,
            stress_gate=None,
            exec_ok=None,
            holdout_opened=True,
            stress_opened=True,
            exec_ran=False,
        )
        report = _base_report(
            pin=freeze_body,
            development=dev_pack,
            holdout=hold_pack,
            HOLDOUT_OPENED=True,
            STRESS_OPENED=True,
            leakage=leakage_n(),
            decision=decision,
            STOP_REASON=str(st_h.get("blocker")),
        )
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    st_pack = pack_split(list(st_h.get("rows") or []), list(STRESS_DAYS))
    sg = stress_gates(st_pack)
    print(
        f"STRESS exe={st_pack.get('EXECUTION_EVALUABLE_N')} mean180={st_pack.get('MEAN_180')} "
        f"mean300={st_pack.get('MEAN_300')} PASS={sg.get('PASS')}",
        flush=True,
    )
    if not integrity_ok() or not sg.get("PASS"):
        decision = decide(
            integrity=integrity_ok(),
            dev_gate=dg,
            hold_gate=hg,
            stress_gate=sg,
            exec_ok=None,
            holdout_opened=True,
            stress_opened=True,
            exec_ran=False,
        )
        after = snapshot(phase="POST")
        report = _base_report(
            pin=freeze_body,
            development=dev_pack,
            holdout=hold_pack,
            stress=st_pack,
            dev_gates=dg,
            holdout_gates=hg,
            stress_gates=sg,
            decay=decay,
            HOLDOUT_OPENED=True,
            STRESS_OPENED=True,
            leakage=leakage_n(),
            decision=decision,
            isolation_before=before,
            isolation_after=after,
            isolation_advanced=advanced(before, after),
        )
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 0

    print("PHASE RESEARCH_IMMEDIATE_ASK_EXECUTION_V1", flush=True)
    scr_dev = pack_screen(list(dev_h.get("rows") or []))
    scr_hold = pack_screen(list(hold_h.get("rows") or []))
    scr_st = pack_screen(list(st_h.get("rows") or []))
    ok_dev = screen_pass(dev_pack, scr_dev, mean_gt=True)
    ok_hold = screen_pass(hold_pack, scr_hold, mean_gt=True)
    ok_st = screen_pass(st_pack, scr_st, mean_gt=False)
    exec_ok = bool(ok_dev and ok_hold and ok_st)
    print(
        f"SCREEN 100sh DEV={scr_dev.get('executable100_n')} HOLDOUT={scr_hold.get('executable100_n')} "
        f"STRESS={scr_st.get('executable100_n')} edge_ok={exec_ok}",
        flush=True,
    )

    decision = decide(
        integrity=integrity_ok(),
        dev_gate=dg,
        hold_gate=hg,
        stress_gate=sg,
        exec_ok=exec_ok,
        holdout_opened=True,
        stress_opened=True,
        exec_ran=True,
    )
    after = snapshot(phase="POST")
    report = _base_report(
        pin=freeze_body,
        development=dev_pack,
        holdout=hold_pack,
        stress=st_pack,
        dev_gates=dg,
        holdout_gates=hg,
        stress_gates=sg,
        decay=decay,
        execution_screen={"DEV": _slim(scr_dev), "HOLDOUT": _slim(scr_hold), "STRESS": _slim(scr_st)},
        HOLDOUT_OPENED=True,
        STRESS_OPENED=True,
        EXECUTION_SCREEN_RAN=True,
        EXECUTION_EDGE_MAINTAINED=exec_ok,
        leakage=leakage_n(),
        decision=decision,
        isolation_before=before,
        isolation_after=after,
        isolation_advanced=advanced(before, after),
        harvest_meta={
            "DEV": dev_h.get("day_meta"),
            "HOLDOUT": hold_h.get("day_meta"),
            "STRESS": st_h.get("day_meta"),
        },
    )
    _publish(report)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
