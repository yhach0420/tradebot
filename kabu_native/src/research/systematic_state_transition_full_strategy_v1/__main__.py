"""Offline systematic state-transition Full Strategy. Canary first. Stress sealed."""
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
from research.systematic_state_transition_full_strategy_v1 import (
    ANALYSIS_ID,
    CANARY_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
)
from research.systematic_state_transition_full_strategy_v1.analyze import (
    blocked_stability,
    build_answers,
    canary_parity,
    decide,
    evaluate_candidate,
    execution_integrity_ok,
    frozen_strategy,
    integrity_ok,
    leakage_n,
    rank_pass,
    ranking_public,
    strip,
    winner_block_pnls,
)
from research.systematic_state_transition_full_strategy_v1.harvest import AUDIT, CANARY_ROW_ID, freeze_path, harvest_development
from research.systematic_state_transition_full_strategy_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.systematic_state_transition_full_strategy_v1.publish import SHEET_ORDER, build_markdown, kv_rows, write_artifacts
from research.systematic_state_transition_full_strategy_v1.spec import canonical_spec, candidate_ids, source_sha256, spec_sha256

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
        "LEGACY_E4_X3_CANARY": False,
        "SELECTED_STRATEGY_SYMBOL_FILTER": False,
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items() if k not in {"51_complete_ranking_table", "9_exact_state_registry"}}),
        "canary": kv_rows(report.get("canary") or {}),
        "ranking": list(report.get("ranking") or [{"empty": True}]),
        "winner": kv_rows(strip(report.get("winner") or {})),
        "folds": list((report.get("stability") or {}).get("folds") or [{"empty": True}]),
        "winner_blocks": list((report.get("winner_blocks") or {}).get("blocks") or [{"empty": True}]),
        "frozen_strategy": kv_rows(report.get("frozen_strategy") or {}),
        "leakage": kv_rows(report.get("leakage") or {}),
        "hashes": kv_rows(report.get("pin") or {}),
        "decision": kv_rows(report.get("decision") or {}),
        "safety": kv_rows(report.get("safety") or {}),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE STATE TRANSITION FULL STRATEGY V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"CANARY {CANARY_ID}  LEGACY_E4_X3=false", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(before.get("ACTIVE_CAPTURE_PATH") or ""), str(before.get("ACTIVE_PAPER_SESSION") or ""))
    if overlap:
        AUDIT["SPLIT_LEAKAGE_N"] += 1

    spec = canonical_spec()
    sha = spec_sha256(spec)
    src_sha = source_sha256()
    print(f"SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)

    print("PHASE DEVELOPMENT harvest (canary + 25)", flush=True)
    har = harvest_development()
    integ = integrity_ok()
    exec_integ = execution_integrity_ok()
    canary_pack: dict[str, Any] = {"CANARY_ID": CANARY_ID, "HARD_PASS": False, "LEGACY_E4_X3_CANARY": False}
    ranking: list[dict[str, Any]] = []
    winner = None
    cov_n = 0
    passed: list[dict[str, Any]] = []
    stab = None
    wblocks = None
    economics_opened = False

    if not har.get("ok"):
        decision = decide(
            integrity=False,
            exec_integ=False,
            canary_ok=False,
            economics_opened=False,
            coverage_pass_n=0,
            pass_n=0,
            winner_id=None,
            stability=None,
        )
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha},
            "canary": canary_pack,
            "leakage": leakage_n(),
            "decision": decision,
            "STOP_REASON": str(har.get("blocker")),
            "safety": _safety(),
            "isolation_before": before,
            "frozen_strategy": frozen_strategy(None),
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    rows_by = dict(har.get("rows_by") or {})
    print("PHASE CANARY RECOVERY_R2_X1_Z3", flush=True)
    canary_ev = evaluate_candidate(CANARY_ROW_ID, list(rows_by.get(CANARY_ROW_ID) or []), days=list(DEVELOPMENT_DAYS))
    canary_pack = canary_parity(canary_ev)
    print(
        f"CANARY signal={canary_ev.get('signal_n')} fill={canary_ev.get('fill_n')} trades={canary_ev.get('TRADE_N')} "
        f"pnl={canary_ev.get('TOTAL_PNL')} pf={canary_ev.get('PF')} HARD_PASS={canary_pack.get('HARD_PASS')}",
        flush=True,
    )

    if canary_pack.get("HARD_PASS") and integ and exec_integ:
        economics_opened = True
        print("PHASE 25-candidate Full Causal economics", flush=True)
        evaluated = []
        for cid in candidate_ids():
            ev = evaluate_candidate(cid, list(rows_by.get(cid) or []), days=list(DEVELOPMENT_DAYS))
            evaluated.append(ev)
            print(
                f"{cid} trades={ev.get('TRADE_N')} pnl={ev.get('TOTAL_PNL')} pf={ev.get('PF')} "
                f"causal={ev.get('CAUSAL_EX_TOP1_PNL')} gate={ev.get('gate')}",
                flush=True,
            )
        ranking = [ranking_public(r) for r in evaluated]
        cov_n = sum(1 for r in evaluated if r.get("gate") != "COVERAGE_FAIL")
        passed = rank_pass(evaluated)
        winner = strip(dict(passed[0])) if passed else None
        print(f"COVERAGE_PASS_N={cov_n} ECONOMIC_PASS_N={len(passed)}", flush=True)
        if winner:
            print("PHASE blocked stability 5x 2-day", flush=True)
            stab = blocked_stability(rows_by, winner.get("candidate_id"))
            wblocks = winner_block_pnls(rows_by, str(winner.get("candidate_id")))
            print(f"STABILITY_PASS={stab.get('STABILITY_PASS')} TRAIN_TOP3_N={stab.get('TRAIN_TOP3_N')}", flush=True)
    else:
        print("CANARY_OR_INTEGRITY_FAIL. 25-candidate economics not opened.", flush=True)

    decision = decide(
        integrity=integ,
        exec_integ=exec_integ,
        canary_ok=bool(canary_pack.get("HARD_PASS")),
        economics_opened=economics_opened,
        coverage_pass_n=cov_n,
        pass_n=len(passed),
        winner_id=None if winner is None else winner.get("candidate_id"),
        stability=stab,
    )
    pin = {
        "SPEC_SHA256": sha,
        "SOURCE_SHA256": src_sha,
        "FULL_STRATEGY_DEV_FROZEN": bool(decision.get("FULL_STRATEGY_DEV_FROZEN")),
        "WINNER": None if winner is None else winner.get("candidate_id"),
        "CANARY_HARD_PASS": bool(canary_pack.get("HARD_PASS")),
        "FREEZE_TIMESTAMP": datetime.now(JST).isoformat(timespec="seconds") if decision.get("FULL_STRATEGY_DEV_FROZEN") else None,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "TOP_SYMBOL_EXCLUSION_IN_FROZEN_STRATEGY": False,
    }
    if decision.get("FULL_STRATEGY_DEV_FROZEN") and winner is not None:
        CACHE.mkdir(parents=True, exist_ok=True)
        freeze_path().write_text(json.dumps({**pin, "winner": winner, "frozen_strategy": frozen_strategy(winner)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"FREEZE {pin['FREEZE_TIMESTAMP']} winner={winner.get('candidate_id')}", flush=True)
    after = snapshot(phase="POST")
    touched = []
    for snap in (before, after):
        cap = snap.get("ACTIVE_CAPTURE_PATH") or ""
        if cap:
            touched.append(Path(cap))
    if stress_path_touch_n(touched) or holdout_path_touch_n(touched):
        raise RuntimeError("SEALED_PATH_TOUCH")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": spec,
        "pin": pin,
        "canary": canary_pack,
        "ranking": ranking,
        "winner": winner,
        "coverage_pass_n": cov_n,
        "economic_pass_n": len(passed),
        "stability": stab,
        "winner_blocks": wblocks,
        "frozen_strategy": frozen_strategy(winner if decision.get("FULL_STRATEGY_DEV_FROZEN") else None),
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
    print(f"OUT {OUT}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
