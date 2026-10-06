"""Offline measurement decomposition. DEVELOPMENT only. No Holdout/Stress. No Runtime write."""
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

from research.am_c0_indicator_exit.isolation import advanced
from research.entry_edge_measurement_decomposition_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    EXPECTED_BREAKOUT_EXECUTABLE_N,
    EXPECTED_BREAKOUT_SIGNAL_N,
    EXPECTED_VWAP_EXECUTABLE_N,
    EXPECTED_VWAP_SIGNAL_N,
    LABEL_DEV,
    MAX_RESEARCH_DATE,
    NEXT_FAMILY_EXECUTED_THIS_RUN,
    NEXT_FAMILY_PRECOMMITTED,
    PARAMETER_SEARCH_PERFORMED,
    PROSPECTIVE_HARVEST_SUSPENDED,
    STRATEGY_SEARCH,
    TRUE_OOS,
)
from research.entry_edge_measurement_decomposition_v1.analyze import (
    build_answers,
    decide,
    integrity_ok,
    interpretation,
    leakage_n,
    lift,
    pack_pop,
    spread_quartiles,
)
from research.entry_edge_measurement_decomposition_v1.harvest import AUDIT, harvest_development
from research.entry_edge_measurement_decomposition_v1.isolation import (
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.entry_edge_measurement_decomposition_v1.publish import build_markdown, kv_rows, write_artifacts


def _slim(p: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in p.items() if k not in ("daily",)}


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    q = report.get("quartiles") or {}
    q_rows = []
    for fam, rows in q.items():
        for r in list(rows or []):
            q_rows.append({"family": fam, **r})
    if not q_rows:
        q_rows = [{"empty": True}]
    sheets = {
        "answers": kv_rows(report.get("answers") or {}),
        "precommit": kv_rows(report.get("precommit") or {}),
        "breakout": kv_rows(_slim(report.get("breakout") or {})),
        "breakout_daily": list((report.get("breakout") or {}).get("daily") or [{"empty": True}]),
        "vwap": kv_rows(_slim(report.get("vwap") or {})),
        "vwap_daily": list((report.get("vwap") or {}).get("daily") or [{"empty": True}]),
        "unconditional": kv_rows(_slim(report.get("unconditional") or {})),
        "uncond_daily": list((report.get("unconditional") or {}).get("daily") or [{"empty": True}]),
        "lift": kv_rows(
            {
                "BREAKOUT": report.get("lift_breakout") or {},
                "VWAP": report.get("lift_vwap") or {},
            }
        ),
        "quartiles": q_rows,
        "decomposition": kv_rows((report.get("answers") or {}).get("24_decomposition_residual") or {}),
        "interpretation": kv_rows(report.get("interpretation") or {}),
        "leakage": kv_rows(report.get("leakage") or {}),
        "decision": kv_rows(report.get("decision") or {}),
        "safety": kv_rows(report.get("safety") or {}),
    }
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE ENTRY EDGE MEASUREMENT DECOMPOSITION V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("Holdout/Stress sealed. DEVELOPMENT only. No family retune. Family #3 not executed.", flush=True)

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

    spec = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PURPOSE": "Separate MID predictive alpha from Ask->Bid execution cost on reused DEVELOPMENT signals.",
        "STRATEGY_SEARCH": STRATEGY_SEARCH,
        "PARAMETER_SEARCH_PERFORMED": PARAMETER_SEARCH_PERFORMED,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "NEXT_FAMILY_PRECOMMITTED": NEXT_FAMILY_PRECOMMITTED,
        "NEXT_FAMILY_EXECUTED_THIS_RUN": NEXT_FAMILY_EXECUTED_THIS_RUN,
        "LABEL_DEV": LABEL_DEV,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
        "NO_RETUNE_BREAKOUT": True,
        "NO_RETUNE_VWAP_RECLAIM": True,
        "NO_SPREAD_THRESHOLD": True,
        "MID_NOT_TRADABLE_PNL": True,
    }

    print("PHASE DEVELOPMENT quote decomposition - holdout/stress not streamed", flush=True)
    h = harvest_development()
    extra_n = {
        "COUNT_MISMATCH_N": 0,
    }
    if not h.get("ok"):
        decision = decide(integrity=False, breakout={}, vwap={}, lift_b={}, lift_v={})
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "leakage": leakage_n(),
            "decision": decision,
            "STOP_REASON": str(h.get("blocker")),
            "safety": {"SUBMIT_N": 0, "CANCEL_N": 0, "LIVE_ORDER_N": 0, "ENTRY_RUNTIME_CHANGED": False, "FUTURE_DATA_USED": False},
            "isolation_before": before,
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    brk_rows = list(h.get("breakout") or [])
    vwp_rows = list(h.get("vwap") or [])
    unc_rows = list(h.get("unconditional") or [])
    brk_sig = int(h.get("breakout_signal_n") or 0)
    vwp_sig = int(h.get("vwap_signal_n") or 0)
    brk_exe = sum(1 for r in brk_rows if r.get("eligible"))
    vwp_exe = sum(1 for r in vwp_rows if r.get("eligible"))
    if brk_sig != EXPECTED_BREAKOUT_SIGNAL_N or brk_exe != EXPECTED_BREAKOUT_EXECUTABLE_N:
        extra_n["COUNT_MISMATCH_N"] += 1
    if vwp_sig != EXPECTED_VWAP_SIGNAL_N or vwp_exe != EXPECTED_VWAP_EXECUTABLE_N:
        extra_n["COUNT_MISMATCH_N"] += 1

    brk = pack_pop(brk_rows, days=list(DEVELOPMENT_DAYS), label="BREAKOUT_CONTINUATION", signal_n=brk_sig)
    vwp = pack_pop(vwp_rows, days=list(DEVELOPMENT_DAYS), label="VWAP_REJECTION_RECLAIM", signal_n=vwp_sig)
    unc = pack_pop(unc_rows, days=list(DEVELOPMENT_DAYS), label="UNCONDITIONAL_ELIGIBLE_BASELINE")
    lb = lift(brk, unc)
    lv = lift(vwp, unc)
    quart = {
        "BREAKOUT": spread_quartiles(brk_rows),
        "VWAP": spread_quartiles(vwp_rows),
    }
    interp = interpretation(brk, vwp, lb, lv)
    integ = integrity_ok(extra_n) and overlap == 0
    decision = decide(integrity=integ, breakout=brk, vwap=vwp, lift_b=lb, lift_v=lv)
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": spec,
        "breakout": brk,
        "vwap": vwp,
        "unconditional": unc,
        "lift_breakout": lb,
        "lift_vwap": lv,
        "quartiles": quart,
        "interpretation": interp,
        "leakage": {**leakage_n(), **extra_n},
        "decision": decision,
        "safety": {
            "SUBMIT_N": 0,
            "CANCEL_N": 0,
            "LIVE_ORDER_N": 0,
            "ENTRY_RUNTIME_CHANGED": False,
            "EXIT_RUNTIME_CHANGED": False,
            "FUTURE_DATA_USED": False,
            "HOLDOUT_OPENED": False,
            "STRESS_OPENED": False,
            "FAMILY3_EXECUTED": False,
        },
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
    }
    _publish(report)
    print(
        f"N breakout_sig={brk_sig} exe={brk_exe} vwap_sig={vwp_sig} exe={vwp_exe} uncond={unc.get('N')} "
        f"MID180_b={brk.get('MEAN_MID_180')} ASK_BID180_b={brk.get('MEAN_ASK_BID_180')}",
        flush=True,
    )
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
