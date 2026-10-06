"""Offline C4 Full Strategy V2. Fold-local eligibility parity before economics. Stress sealed."""
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
from research.c1_multi_timeframe_precommit_v1.spec import execution_contract, portfolio_contract
from research.c4_portfolio_crowding_full_strategy_v2 import (
    ANALYSIS_ID,
    CANARY_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FROZEN_ARM_IDS,
    KEPT_EXIT_IDS,
    MAX_RESEARCH_DATE,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
)
from research.c4_portfolio_crowding_full_strategy_v2.analyze import (
    attach_incremental,
    attribution_stability,
    blocked_stability,
    build_answers,
    canary_parity,
    decide,
    entry_exit_interaction,
    evaluate_candidate,
    execution_integrity_ok,
    exit_entry_interaction,
    frozen_strategy,
    integrity_ok,
    leakage_n,
    rank_pass,
    ranking_public,
    stability_pass,
    strip,
    winner_block_pnls,
)
from research.c4_portfolio_crowding_full_strategy_v2.fold_parity import evaluate_fold_local_parity
from research.c4_portfolio_crowding_full_strategy_v2.harvest import AUDIT, CANARY_ROW_ID, freeze_path, harvest_development
from research.c4_portfolio_crowding_full_strategy_v2.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.c4_portfolio_crowding_full_strategy_v2.prior import artifacts_modified, load_v2, snapshot_prior_artifacts
from research.c4_portfolio_crowding_full_strategy_v2.publish import SHEET_ORDER, build_markdown, kv_rows, write_artifacts
from research.c4_portfolio_crowding_full_strategy_v2.spec import (
    canonical_spec,
    candidate_ids,
    matched_control_id,
    source_sha256,
    spec_sha256,
)
from research.c4_portfolio_crowding_full_strategy_v2.streams import prove_raw_entry_stream_invariance
from research.c4_portfolio_crowding_precommit_v2 import CONTROL_POLICY_ID, TREATMENT_POLICY_ID

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
        "SELECTED_STRATEGY_SYMBOL_FILTER": False,
        "NEW_EXIT_CREATED": False,
        "EXIT6_CREATED": False,
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
        "NEW_C4_POLICY": False,
        "NEW_THRESHOLD": False,
        "PRIOR_ARTIFACT_MODIFIED": False,
    }


def _flatten_interaction(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = {k: v for k, v in r.items() if k != "exits"}
        out.append(rec)
        for ex in list(r.get("exits") or []):
            row = dict(rec)
            row.update(ex)
            out.append(row)
    return out or [{"empty": True}]


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    ranking = list(report.get("ranking") or [])
    stab = dict(report.get("stability") or {})
    folds = list(stab.get("folds") or [])
    stream = dict(report.get("raw_entry_stream_invariance") or {})
    parity = dict(report.get("fold_local_eligibility") or {})
    attr = dict(report.get("attribution") or {})
    sheets = {
        "answers": kv_rows(report.get("answers") or {}),
        "prior_integrity": kv_rows(report.get("prior_integrity") or {}),
        "fold_local_eligibility": kv_rows({k: v for k, v in parity.items() if k not in ("folds", "rows")}),
        "fold_local_detail": list(parity.get("rows") or [{"empty": True}]),
        "canary": kv_rows(report.get("canary") or {}),
        "raw_entry_streams": list(stream.get("entries") or [{"empty": True}]),
        "raw_entry_stream_invariance": list(stream.get("variants") or [{"empty": True}]),
        "arms": ranking or [{"empty": True}],
        "coverage": [
            {
                "candidate_id": r.get("candidate_id"),
                "ENTRY_ID": r.get("ENTRY_ID"),
                "C4_POLICY": r.get("C4_POLICY"),
                "EXIT_ID": r.get("EXIT_ID"),
                "TRADE_N": r.get("trade_n"),
                "TRADING_DAY_WITH_FILL_N": r.get("TRADING_DAY_WITH_FILL_N"),
                "trades_per_day": r.get("trades_per_day"),
                "gate": r.get("gate"),
            }
            for r in ranking
        ]
        or [{"empty": True}],
        "economics": [
            {
                "candidate_id": r.get("candidate_id"),
                "ENTRY_ID": r.get("ENTRY_ID"),
                "C4_POLICY": r.get("C4_POLICY"),
                "EXIT_ID": r.get("EXIT_ID"),
                "pnl": r.get("pnl"),
                "PF": r.get("PF"),
                "MaxDD": r.get("MaxDD"),
                "positive_days": r.get("positive_days"),
                "negative_days": r.get("negative_days"),
                "EX_BEST": r.get("EX_BEST"),
                "g_table": r.get("g_table"),
                "gate": r.get("gate"),
                "score": r.get("score"),
            }
            for r in ranking
        ]
        or [{"empty": True}],
        "incremental": [
            {
                "candidate_id": r.get("candidate_id"),
                "CONTROL_ID": r.get("CONTROL_ID"),
                "I1": r.get("I1"),
                "I2": r.get("I2"),
                "C4_INCREMENTAL_SUPPORT": r.get("C4_INCREMENTAL_SUPPORT"),
                "TREATMENT_ROBUST_VALUE": r.get("TREATMENT_ROBUST_VALUE"),
                "CONTROL_ROBUST_VALUE": r.get("CONTROL_ROBUST_VALUE"),
                "CONTROL_TOTAL_PNL": r.get("CONTROL_TOTAL_PNL"),
            }
            for r in ranking
            if r.get("C4_POLICY") == TREATMENT_POLICY_ID
        ]
        or [{"empty": True}],
        "ranking": ranking or [{"empty": True}],
        "causal_ex_top1": [
            {
                "candidate_id": r.get("candidate_id"),
                "top_symbol": r.get("top_symbol"),
                "TOP_SYMBOL_BASE_TRADE_N": r.get("TOP_SYMBOL_BASE_TRADE_N"),
                "TOP_SYMBOL_BASE_PNL": r.get("TOP_SYMBOL_BASE_PNL"),
                "CAUSAL_EX_TOP1": r.get("CAUSAL_EX_TOP1"),
                "CAUSAL_EX_TOP1_PF": r.get("CAUSAL_EX_TOP1_PF"),
                "CAUSAL_EX_TOP1_TRADE_N": r.get("CAUSAL_EX_TOP1_TRADE_N"),
                "CAUSAL_EX_TOP1_MAXDD": r.get("CAUSAL_EX_TOP1_MAXDD"),
            }
            for r in ranking
        ]
        or [{"empty": True}],
        "entry_exit_interaction": _flatten_interaction(list(report.get("entry_exit_interaction") or [])),
        "exit_entry_interaction": list(report.get("exit_entry_interaction") or [{"empty": True}]),
        "fold_training": folds or [{"empty": True}],
        "fold_selected_test": [
            {
                "block": f.get("block"),
                "selected": f.get("winner"),
                "TEST_TREATMENT_PNL": f.get("TEST_TREATMENT_PNL"),
                "TEST_CONTROL_PNL": f.get("TEST_CONTROL_PNL"),
                "TEST_C4_DELTA_PNL": f.get("TEST_C4_DELTA_PNL"),
                "TEST_C4_INTERVENTION_N": f.get("TEST_C4_INTERVENTION_N"),
                "HELD_OUT_DELTA_DIAGNOSTIC_ONLY": True,
            }
            for f in folds
        ]
        or [{"empty": True}],
        "winner_blocks": list((report.get("winner_blocks") or {}).get("blocks") or [{"empty": True}]),
        "attribution_stability": list(attr.get("blocks") or [{"empty": True}])
        if attr
        else [{"empty": True}],
        "stability": kv_rows({k: v for k, v in stab.items() if k != "folds"}),
        "execution": kv_rows(execution_contract()),
        "portfolio": kv_rows(portfolio_contract()),
        "leakage": kv_rows(report.get("leakage") or {}),
        "decision": kv_rows(report.get("decision") or {}),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE C4 PORTFOLIO CROWDING FULL STRATEGY V2", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"CANARY {CANARY_ID}", flush=True)
    print(f"ARM_N={len(FROZEN_ARM_IDS)} EXIT={list(KEPT_EXIT_IDS)}", flush=True)

    before = snapshot(phase="PRE")
    prior_hashes_before = snapshot_prior_artifacts()
    overlap = write_overlap_n(str(before.get("ACTIVE_CAPTURE_PATH") or ""), str(before.get("ACTIVE_PAPER_SESSION") or ""))
    if overlap:
        AUDIT["SPLIT_LEAKAGE_N"] += 1

    spec = canonical_spec()
    sha = spec_sha256()
    src_sha = source_sha256()
    print(f"SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)

    pre = load_v2()
    print(f"V2_PIN ok={pre.get('ok')} verdict={pre.get('VERDICT')} failed={pre.get('failed')}", flush=True)

    parity = evaluate_fold_local_parity(list(pre.get("block_intervention") or []))
    print(
        f"FOLD_LOCAL_ELIGIBILITY_PARITY_PASS={parity.get('FOLD_LOCAL_ELIGIBILITY_PARITY_PASS')} "
        f"N={parity.get('FOLD_LOCAL_ELIGIBLE_ENTRY_N_B1')}/"
        f"{parity.get('FOLD_LOCAL_ELIGIBLE_ENTRY_N_B2')}/"
        f"{parity.get('FOLD_LOCAL_ELIGIBLE_ENTRY_N_B3')}/"
        f"{parity.get('FOLD_LOCAL_ELIGIBLE_ENTRY_N_B4')}/"
        f"{parity.get('FOLD_LOCAL_ELIGIBLE_ENTRY_N_B5')} "
        f"HANDOFF={parity.get('ANY_HANDOFF_TRAIN_ELIGIBLE')}",
        flush=True,
    )

    canary_pack: dict[str, Any] = {"CANARY_ID": CANARY_ID, "HARD_PASS": False}
    ranking: list[dict[str, Any]] = []
    winner = None
    cov_n = 0
    passed: list[dict[str, Any]] = []
    stab = None
    wblocks = None
    attr = None
    economics_opened = False
    arm_economics_run = False
    stream_pack: dict[str, Any] = {"ENTRY_RAW_STREAM_INVARIANCE_PASS": False}
    pair_rerun_n = 0
    prior_z3_reuse_n = 0
    entry_ix: list[dict[str, Any]] = []
    exit_ix: list[dict[str, Any]] = []

    def _fail(reason: str, *, canary_ok: bool, econ: bool, stream_ok: bool, arms: bool) -> int:
        decision = decide(
            parity_ok=bool(parity.get("FOLD_LOCAL_ELIGIBILITY_PARITY_PASS")) and bool(pre.get("ok")),
            integrity=integrity_ok() and bool(pre.get("ok")),
            exec_integ=execution_integrity_ok(),
            precommit_ok=bool(pre.get("ok")),
            stream_ok=stream_ok,
            canary_ok=canary_ok,
            economics_opened=econ,
            arm_economics_run=arms,
            coverage_pass_n=cov_n,
            pass_n=len(passed),
            winner_id=None if winner is None else winner.get("candidate_id"),
            stability=stab,
            attribution=attr,
            pair_rerun_n=pair_rerun_n,
            prior_z3_reuse_n=prior_z3_reuse_n,
        )
        if reason == "FOLD_LOCAL_ELIGIBILITY_PARITY" or not parity.get("FOLD_LOCAL_ELIGIBILITY_PARITY_PASS"):
            decision["VERDICT"] = "C4_PORTFOLIO_CROWDING_FULL_STRATEGY_V2_INTEGRITY_FAILED"
            decision["ARM_ECONOMICS_RUN"] = False
            decision["ECONOMICS_OPENED"] = False
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "spec": spec,
            "prior_integrity": pre,
            "fold_local_eligibility": parity,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha},
            "canary": canary_pack,
            "raw_entry_stream_invariance": stream_pack,
            "leakage": leakage_n(),
            "decision": decision,
            "STOP_REASON": reason,
            "safety": _safety(),
            "isolation_before": before,
            "frozen_strategy": frozen_strategy(None),
            "ranking": ranking,
            "coverage_pass_n": cov_n,
            "economic_pass_n": len(passed),
            "stability": stab,
            "winner_blocks": wblocks,
            "attribution": attr,
            "entry_exit_interaction": entry_ix,
            "exit_entry_interaction": exit_ix,
            "PAIR_RERUN_N": pair_rerun_n,
            "PRIOR_Z3_METRIC_REUSE_N": prior_z3_reuse_n,
            "TRUE_OOS": TRUE_OOS,
            "CERTIFIED": CERTIFIED,
        }
        _publish(report)
        print(f"VERDICT {decision['VERDICT']}", flush=True)
        print("STOP.", flush=True)
        return 2

    if not pre.get("ok"):
        return _fail(str(pre.get("blocker")), canary_ok=False, econ=False, stream_ok=False, arms=False)

    if not parity.get("FOLD_LOCAL_ELIGIBILITY_PARITY_PASS"):
        return _fail("FOLD_LOCAL_ELIGIBILITY_PARITY", canary_ok=False, econ=False, stream_ok=False, arms=False)

    print("PHASE DEVELOPMENT harvest (canary + uniform 40 arms, no ST/C1 fill reuse)", flush=True)
    har = harvest_development()
    pair_rerun_n = int(har.get("PAIR_RERUN_N") or 0)
    prior_z3_reuse_n = int(har.get("PRIOR_Z3_METRIC_REUSE_N") or 0)
    integ = integrity_ok()
    exec_integ = execution_integrity_ok()

    if not har.get("ok"):
        return _fail(str(har.get("blocker")), canary_ok=False, econ=False, stream_ok=False, arms=False)

    rows_by = dict(har.get("rows_by") or {})
    stream_pack = prove_raw_entry_stream_invariance(rows_by)
    print(
        f"STREAM invariance pass={stream_pack.get('ENTRY_RAW_STREAM_INVARIANCE_PASS')} "
        f"PASS_N={stream_pack.get('PASS_N')} FAIL={stream_pack.get('FAIL_ENTRY_IDS')}",
        flush=True,
    )
    if not stream_pack.get("ENTRY_RAW_STREAM_INVARIANCE_PASS") or pair_rerun_n != 40:
        return _fail("RAW_ENTRY_STREAM_INVARIANCE", canary_ok=False, econ=False, stream_ok=False, arms=False)

    print("PHASE CANARY RECOVERY_R2_X1_Z3 (no C4)", flush=True)
    canary_ev = evaluate_candidate(
        CANARY_ROW_ID, list(rows_by.get(CANARY_ROW_ID) or []), days=list(DEVELOPMENT_DAYS), run_g6="never"
    )
    canary_pack = canary_parity(canary_ev)
    print(
        f"CANARY signal={canary_ev.get('signal_n')} fill={canary_ev.get('fill_n')} trades={canary_ev.get('TRADE_N')} "
        f"pnl={canary_ev.get('TOTAL_PNL')} pf={canary_ev.get('PF')} HARD_PASS={canary_pack.get('HARD_PASS')}",
        flush=True,
    )

    if canary_pack.get("HARD_PASS") and integ and exec_integ:
        economics_opened = True
        arm_economics_run = True
        print("PHASE 40-arm Full Causal economics (uniform C4 V2 engine)", flush=True)
        evaluated = []
        by_id: dict[str, dict[str, Any]] = {}
        for cid in candidate_ids():
            run_g6 = "attribution" if CONTROL_POLICY_ID in cid else "auto"
            ev = evaluate_candidate(cid, list(rows_by.get(cid) or []), days=list(DEVELOPMENT_DAYS), run_g6=run_g6)
            by_id[cid] = ev
            print(
                f"{cid} trades={ev.get('TRADE_N')} pnl={ev.get('TOTAL_PNL')} pf={ev.get('PF')} "
                f"causal={ev.get('CAUSAL_EX_TOP1_PNL')} gate={ev.get('gate')}",
                flush=True,
            )
        for cid in candidate_ids():
            ev = by_id[cid]
            if ev.get("C4_POLICY") == TREATMENT_POLICY_ID:
                ev = attach_incremental(ev, by_id[matched_control_id(cid)])
            evaluated.append(ev)
        ranking = [ranking_public(r) for r in evaluated]
        cov_n = sum(1 for r in evaluated if r.get("WINNER_ELIGIBLE") and r.get("gate") != "COVERAGE_FAIL")
        passed = rank_pass(evaluated)
        winner = strip(dict(passed[0])) if passed else None
        entry_ix = entry_exit_interaction(evaluated)
        exit_ix = exit_entry_interaction(evaluated)
        print(f"COVERAGE_PASS_N={cov_n} ECONOMIC_PASS_N={len(passed)}", flush=True)
        if winner:
            print("PHASE blocked stability 5x 2-day + A1-A3", flush=True)
            raw_stab = blocked_stability(rows_by, winner.get("candidate_id"), list(pre.get("block_intervention") or []))
            wblocks = winner_block_pnls(rows_by, str(winner.get("candidate_id")))
            stab = stability_pass(raw_stab, wblocks)
            attr = attribution_stability(rows_by, str(winner.get("candidate_id")), list(pre.get("block_intervention") or []))
            print(
                f"STABILITY_PASS={stab.get('STABILITY_PASS')} "
                f"A1-A3={attr.get('C4_ATTRIBUTION_STABILITY_PASS')} "
                f"TRAIN_TOP3_N={stab.get('TRAIN_TOP3_N')}",
                flush=True,
            )
    else:
        print("CANARY_OR_INTEGRITY_FAIL. 40-arm economics not opened.", flush=True)

    decision = decide(
        parity_ok=True,
        integrity=integ,
        exec_integ=exec_integ,
        precommit_ok=True,
        stream_ok=bool(stream_pack.get("ENTRY_RAW_STREAM_INVARIANCE_PASS")),
        canary_ok=bool(canary_pack.get("HARD_PASS")),
        economics_opened=economics_opened,
        arm_economics_run=arm_economics_run,
        coverage_pass_n=cov_n,
        pass_n=len(passed),
        winner_id=None if winner is None else winner.get("candidate_id"),
        stability=stab,
        attribution=attr,
        pair_rerun_n=pair_rerun_n,
        prior_z3_reuse_n=prior_z3_reuse_n,
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
        "PAIR_RERUN_N": pair_rerun_n,
        "PRIOR_Z3_METRIC_REUSE_N": prior_z3_reuse_n,
        "FINAL_EXIT_N": 4,
        "FINAL_ARM_N": 40,
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
        "FOLD_LOCAL_ELIGIBILITY_PARITY_PASS": True,
    }
    if decision.get("FULL_STRATEGY_DEV_FROZEN") and winner is not None:
        CACHE.mkdir(parents=True, exist_ok=True)
        freeze_path().write_text(
            json.dumps({**pin, "winner": winner, "frozen_strategy": frozen_strategy(winner)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"FREEZE {pin['FREEZE_TIMESTAMP']} winner={winner.get('candidate_id')}", flush=True)
    after = snapshot(phase="POST")
    opened: list[Path] = []
    if stress_path_touch_n(opened) or holdout_path_touch_n(opened):
        raise RuntimeError("SEALED_PATH_TOUCH")
    prior_hashes_after = snapshot_prior_artifacts()
    if artifacts_modified(prior_hashes_before, prior_hashes_after):
        raise RuntimeError("PRIOR_ARTIFACT_MODIFIED")
    safety = _safety()
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "spec": spec,
        "prior_integrity": pre,
        "fold_local_eligibility": parity,
        "pin": pin,
        "canary": canary_pack,
        "raw_entry_stream_invariance": stream_pack,
        "ranking": ranking,
        "winner": winner,
        "coverage_pass_n": cov_n,
        "economic_pass_n": len(passed),
        "stability": stab,
        "winner_blocks": wblocks,
        "attribution": attr,
        "entry_exit_interaction": entry_ix,
        "exit_entry_interaction": exit_ix,
        "frozen_strategy": frozen_strategy(winner if decision.get("FULL_STRATEGY_DEV_FROZEN") else None),
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "leakage": leakage_n(),
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "PAIR_RERUN_N": pair_rerun_n,
        "PRIOR_Z3_METRIC_REUSE_N": prior_z3_reuse_n,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "PROSPECTIVE_HARVEST_SUSPENDED": PROSPECTIVE_HARVEST_SUSPENDED,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "NEW_EXIT_CREATED": False,
        "EXIT6_CREATED": False,
    }
    _publish(report)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
