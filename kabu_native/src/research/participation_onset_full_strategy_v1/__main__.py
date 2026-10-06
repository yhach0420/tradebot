"""Offline Participation Onset Full Strategy. DEV only. 3 candidates. Recovery closed. Stress sealed."""
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
from research.participation_onset_full_strategy_v1 import (
    ANALYSIS_ID,
    CANDIDATE_IDS,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    PRIOR_RECOVERY,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
)
from research.participation_onset_full_strategy_v1.analyze import (
    build_answers,
    decide,
    evaluate_candidate,
    execution_integrity_ok,
    integrity_ok,
    leakage_n,
    lodo,
    rank_pass,
    ranking_public,
    strip,
)
from research.participation_onset_full_strategy_v1.entries import already_executed_check
from research.participation_onset_full_strategy_v1.harvest import AUDIT, freeze_path, harvest_development
from research.participation_onset_full_strategy_v1.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.participation_onset_full_strategy_v1.publish import build_markdown, kv_rows, write_artifacts
from research.participation_onset_full_strategy_v1.spec import canonical_spec, source_sha256, spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "HOLDOUT_OPENED": False,
        "SIZING": False,
        "RECOVERY_REVIVED": False,
        "R4_ADDED": False,
        "ABOVE_VWAP_ENTRY_FILTER": False,
        "THRESHOLD_RETUNED": False,
        "FOURTH_EXIT_ADDED": False,
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items()}),
        "precommit": kv_rows(report.get("precommit") or {}),
        "pin": kv_rows(report.get("pin") or {}),
        "ranking": list(report.get("ranking") or [{"empty": True}]),
        "winner": kv_rows(strip(report.get("winner") or {})),
        "lodo": list((report.get("lodo") or {}).get("folds") or [{"empty": True}]),
        "prior_recovery": kv_rows(PRIOR_RECOVERY),
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE PARTICIPATION ONSET FULL STRATEGY V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("RECOVERY_SEQUENCE_CLOSED. 3 candidates. Frozen T1. No above-VWAP. Stress sealed.", flush=True)

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

    dup = already_executed_check()
    print(f"DUPLICATE {dup.get('duplicate')}", flush=True)
    spec = canonical_spec()
    sha = spec_sha256(spec)
    src_sha = source_sha256()
    print(f"SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)
    print(f"CANDIDATE_N {len(CANDIDATE_IDS)}", flush=True)

    print("PHASE DEVELOPMENT harvest", flush=True)
    har = harvest_development()
    if not har.get("ok"):
        decision = decide(integrity=False, exec_integ=False, coverage_pass_n=0, pass_n=0, winner_id=None, lodo_pack=None)
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha},
            "already_executed": dup,
            "leakage": leakage_n(),
            "decision": decision,
            "STOP_REASON": str(har.get("blocker")),
            "safety": _safety(),
            "isolation_before": before,
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    rows_by = dict(har.get("rows_by") or {})
    ctrl_by = dict(har.get("ctrl_by") or {})
    evaluated = []
    for cid in CANDIDATE_IDS:
        ev = evaluate_candidate(cid, list(rows_by.get(cid) or []), list(ctrl_by.get("P1_X1") or []), days=list(DEVELOPMENT_DAYS))
        evaluated.append(ev)
        print(
            f"{cid} trades={ev.get('TRADE_N')} pnl={ev.get('TOTAL_PNL')} pf={ev.get('PF')} causal={ev.get('CAUSAL_EX_TOP1_PNL')} gate={ev.get('gate')}",
            flush=True,
        )
    ranking = [ranking_public(r) for r in evaluated]
    cov_n = sum(1 for r in evaluated if r.get("gate") != "COVERAGE_FAIL")
    passed = rank_pass(evaluated)
    winner = strip(dict(passed[0])) if passed else None
    print(f"COVERAGE_PASS_N={cov_n} ECONOMIC_PASS_N={len(passed)}", flush=True)
    lodo_pack = None
    if winner:
        print("PHASE LODO 10 folds fold-local CAUSAL_EX_TOP1", flush=True)
        lodo_pack = lodo(rows_by, ctrl_by, winner.get("candidate_id"))
        print(
            f"LODO FOLD_ECONOMIC_PASS_N={lodo_pack.get('FOLD_ECONOMIC_PASS_N')} WINNER_SELECTED_N={lodo_pack.get('WINNER_SELECTED_N')} stable={lodo_pack.get('stable')}",
            flush=True,
        )
    integ = integrity_ok()
    exec_integ = execution_integrity_ok()
    decision = decide(
        integrity=integ,
        exec_integ=exec_integ,
        coverage_pass_n=cov_n,
        pass_n=len(passed),
        winner_id=None if winner is None else winner.get("candidate_id"),
        lodo_pack=lodo_pack,
    )
    pin = {
        "SPEC_SHA256": sha,
        "SOURCE_SHA256": src_sha,
        "FULL_STRATEGY_DEV_FROZEN": bool(decision.get("FULL_STRATEGY_DEV_FROZEN")),
        "WINNER": None if winner is None else winner.get("candidate_id"),
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(timespec="seconds") if decision.get("FULL_STRATEGY_DEV_FROZEN") else None,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "RECOVERY_SEQUENCE_CLOSED": True,
        "ABOVE_VWAP_ENTRY_FILTER": False,
        "THRESHOLD": 0.6486486486486487,
    }
    if decision.get("FULL_STRATEGY_DEV_FROZEN") and winner is not None:
        CACHE.mkdir(parents=True, exist_ok=True)
        freeze_path().write_text(json.dumps({**pin, "winner": winner}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"FREEZE {pin['FREEZE_TIMESTAMP']} winner={winner.get('candidate_id')}", flush=True)
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": spec,
        "pin": pin,
        "already_executed": dup,
        "prior_recovery_reference": PRIOR_RECOVERY,
        "ranking": ranking,
        "evaluated_public": ranking,
        "winner": winner,
        "coverage_pass_n": cov_n,
        "economic_pass_n": len(passed),
        "lodo": lodo_pack,
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "leakage": leakage_n(),
        "decision": decision,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
    }
    _publish(report)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
