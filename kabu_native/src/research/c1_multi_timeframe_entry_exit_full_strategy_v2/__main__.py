"""Offline C1 ENTRY×EXIT Full Strategy V2. Uniform 40-pair. Canary first. Stress sealed."""
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
from research.c1_multi_timeframe_entry_exit_full_strategy_v2 import (
    ANALYSIS_ID,
    CANARY_ID,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FROZEN_PAIRS,
    KEPT_EXIT_IDS,
    MAX_RESEARCH_DATE,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
)
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.analyze import (
    blocked_stability,
    build_answers,
    canary_parity,
    decide,
    entry_exit_interaction,
    evaluate_candidate,
    execution_integrity_ok,
    exit_entry_interaction,
    frozen_strategy,
    htf_integrity,
    integrity_ok,
    leakage_n,
    rank_pass,
    ranking_public,
    stability_pass,
    strip,
    winner_block_pnls,
)
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.harvest import AUDIT, CANARY_ROW_ID, freeze_path, harvest_development
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.prior_pin import load_precommit_report
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.publish import SHEET_ORDER, build_markdown, kv_rows, write_artifacts
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.spec import canonical_spec, candidate_ids, source_sha256, spec_sha256
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.streams import prove_raw_entry_stream_invariance
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import execution_contract, portfolio_contract

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
        "GLOBAL_VWAP_ENTRY_GATE": False,
        "NEW_EXIT_CREATED": False,
        "EXIT6_CREATED": False,
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
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
    sheets = {
        "answers": kv_rows(report.get("answers") or {}),
        "prior_integrity": kv_rows(report.get("prior_integrity") or {}),
        "canary": kv_rows(report.get("canary") or {}),
        "raw_entry_streams": list(stream.get("entries") or [{"empty": True}]),
        "raw_entry_stream_invariance": list(stream.get("variants") or [{"empty": True}]),
        "htf_integrity": kv_rows(report.get("htf_integrity")),
        "pairs": ranking or [{"empty": True}],
        "coverage": [
            {
                "candidate_id": r.get("candidate_id"),
                "ENTRY_ID": r.get("ENTRY_ID"),
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
                "NEWLY_ADMITTED_N": r.get("NEWLY_ADMITTED_N"),
                "NEWLY_ADMITTED_PNL": r.get("NEWLY_ADMITTED_PNL"),
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
                "test_trade_n": f.get("test_trade_n"),
                "test_pnl": f.get("test_pnl"),
                "test_PF": f.get("test_PF"),
                "test_positive_days": f.get("test_positive_days"),
                "test_negative_days": f.get("test_negative_days"),
                "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
            }
            for f in folds
        ]
        or [{"empty": True}],
        "winner_blocks": list((report.get("winner_blocks") or {}).get("blocks") or [{"empty": True}]),
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE C1 MULTI TIMEFRAME ENTRY EXIT FULL STRATEGY V2", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"CANARY {CANARY_ID}", flush=True)
    print(f"PAIR_N={len(FROZEN_PAIRS)} EXIT={list(KEPT_EXIT_IDS)}", flush=True)

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(before.get("ACTIVE_CAPTURE_PATH") or ""), str(before.get("ACTIVE_PAPER_SESSION") or ""))
    if overlap:
        AUDIT["SPLIT_LEAKAGE_N"] += 1

    spec = canonical_spec()
    sha = spec_sha256(spec)
    src_sha = source_sha256()
    print(f"SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)

    pre = load_precommit_report()
    print(f"V2_PIN ok={pre.get('ok')} verdict={pre.get('VERDICT')} hashes_match={pre.get('hashes_match')}", flush=True)

    canary_pack: dict[str, Any] = {"CANARY_ID": CANARY_ID, "HARD_PASS": False}
    ranking: list[dict[str, Any]] = []
    winner = None
    cov_n = 0
    passed: list[dict[str, Any]] = []
    stab = None
    wblocks = None
    economics_opened = False
    stream_pack: dict[str, Any] = {"ENTRY_RAW_STREAM_INVARIANCE_PASS": False}
    pair_rerun_n = 0
    prior_z3_reuse_n = 0
    entry_ix: list[dict[str, Any]] = []
    exit_ix: list[dict[str, Any]] = []
    htf = htf_integrity()

    def _fail(reason: str, *, canary_ok: bool, econ: bool, stream_ok: bool) -> int:
        decision = decide(
            integrity=integrity_ok() and bool(pre.get("ok")),
            exec_integ=execution_integrity_ok(),
            htf_ok=bool(htf_integrity().get("ok")),
            precommit_ok=bool(pre.get("ok")),
            stream_ok=stream_ok,
            canary_ok=canary_ok,
            economics_opened=econ,
            coverage_pass_n=cov_n,
            pass_n=len(passed),
            winner_id=None if winner is None else winner.get("candidate_id"),
            stability=stab,
            pair_rerun_n=pair_rerun_n,
            prior_z3_reuse_n=prior_z3_reuse_n,
        )
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "spec": spec,
            "prior_integrity": pre,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha},
            "canary": canary_pack,
            "raw_entry_stream_invariance": stream_pack,
            "htf_integrity": htf_integrity(),
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
        return _fail(str(pre.get("blocker")), canary_ok=False, econ=False, stream_ok=False)

    print("PHASE DEVELOPMENT harvest (canary + uniform 40 pairs, no V1 Z3 reuse)", flush=True)
    har = harvest_development()
    pair_rerun_n = int(har.get("PAIR_RERUN_N") or 0)
    prior_z3_reuse_n = int(har.get("PRIOR_Z3_METRIC_REUSE_N") or 0)
    integ = integrity_ok()
    exec_integ = execution_integrity_ok()
    htf = htf_integrity()

    if not har.get("ok"):
        return _fail(str(har.get("blocker")), canary_ok=False, econ=False, stream_ok=False)

    rows_by = dict(har.get("rows_by") or {})
    stream_pack = prove_raw_entry_stream_invariance(rows_by)
    print(
        f"STREAM invariance pass={stream_pack.get('ENTRY_RAW_STREAM_INVARIANCE_PASS')} "
        f"PASS_N={stream_pack.get('PASS_N')} FAIL={stream_pack.get('FAIL_ENTRY_IDS')}",
        flush=True,
    )
    if not stream_pack.get("ENTRY_RAW_STREAM_INVARIANCE_PASS") or pair_rerun_n != 40:
        return _fail("RAW_ENTRY_STREAM_INVARIANCE", canary_ok=False, econ=False, stream_ok=False)

    print("PHASE CANARY RECOVERY_R2_X1_Z3", flush=True)
    canary_ev = evaluate_candidate(CANARY_ROW_ID, list(rows_by.get(CANARY_ROW_ID) or []), days=list(DEVELOPMENT_DAYS), run_g6="never")
    canary_pack = canary_parity(canary_ev)
    print(
        f"CANARY signal={canary_ev.get('signal_n')} fill={canary_ev.get('fill_n')} trades={canary_ev.get('TRADE_N')} "
        f"pnl={canary_ev.get('TOTAL_PNL')} pf={canary_ev.get('PF')} HARD_PASS={canary_pack.get('HARD_PASS')}",
        flush=True,
    )

    if canary_pack.get("HARD_PASS") and integ and exec_integ and htf.get("ok"):
        economics_opened = True
        print("PHASE 40-pair Full Causal economics (uniform V2 engine)", flush=True)
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
        entry_ix = entry_exit_interaction(evaluated)
        exit_ix = exit_entry_interaction(evaluated)
        print(f"COVERAGE_PASS_N={cov_n} ECONOMIC_PASS_N={len(passed)}", flush=True)
        if winner:
            print("PHASE blocked stability 5x 2-day", flush=True)
            raw_stab = blocked_stability(rows_by, winner.get("candidate_id"))
            wblocks = winner_block_pnls(rows_by, str(winner.get("candidate_id")))
            stab = stability_pass(raw_stab, wblocks)
            print(f"STABILITY_PASS={stab.get('STABILITY_PASS')} TRAIN_TOP3_N={stab.get('TRAIN_TOP3_N')}", flush=True)
    else:
        print("CANARY_OR_INTEGRITY_FAIL. 40-pair economics not opened.", flush=True)

    decision = decide(
        integrity=integ,
        exec_integ=exec_integ,
        htf_ok=bool(htf.get("ok")),
        precommit_ok=True,
        stream_ok=bool(stream_pack.get("ENTRY_RAW_STREAM_INVARIANCE_PASS")),
        canary_ok=bool(canary_pack.get("HARD_PASS")),
        economics_opened=economics_opened,
        coverage_pass_n=cov_n,
        pass_n=len(passed),
        winner_id=None if winner is None else winner.get("candidate_id"),
        stability=stab,
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
        "FINAL_PAIR_N": 40,
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
        "GLOBAL_VWAP_ENTRY_GATE": False,
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
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "spec": spec,
        "prior_integrity": pre,
        "pin": pin,
        "canary": canary_pack,
        "raw_entry_stream_invariance": stream_pack,
        "htf_integrity": htf,
        "ranking": ranking,
        "winner": winner,
        "coverage_pass_n": cov_n,
        "economic_pass_n": len(passed),
        "stability": stab,
        "winner_blocks": wblocks,
        "entry_exit_interaction": entry_ix,
        "exit_entry_interaction": exit_ix,
        "frozen_strategy": frozen_strategy(winner if decision.get("FULL_STRATEGY_DEV_FROZEN") else None),
        "HOLDOUT_OPENED": False,
        "STRESS_OPENED": False,
        "leakage": leakage_n(),
        "decision": decision,
        "safety": _safety(),
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
