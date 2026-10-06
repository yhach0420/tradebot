"""Offline causal DROP_TOP recheck + original LODO. DEV cache only. Stress sealed. No Runtime write."""
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
from research.e4_x2_z3_causal_concentration_recheck_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    FOCUS_CANDIDATE,
    MAX_RESEARCH_DATE,
    PRIOR_ECONOMIC_PASS_N,
    PROSPECTIVE_HARVEST_SUSPENDED,
    TRUE_OOS,
)
from research.e4_x2_z3_causal_concentration_recheck_v1.analyze import (
    audit_drop_top_method,
    build_answers,
    decide,
    evaluate_corrected_set,
    integrity_ok,
    leakage_n,
    lodo,
    notional_diagnostic,
    other_gate_pass,
    parity_check,
    posthoc_ex_top1,
    public_pass_row,
    rank_corrected,
    strip,
    symbol_ledger,
    top_symbol_from_trades,
)
from research.e4_x2_z3_causal_concentration_recheck_v1.harvest import AUDIT, load_prior_development_grid
from research.e4_x2_z3_causal_concentration_recheck_v1.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.e4_x2_z3_causal_concentration_recheck_v1.publish import build_markdown, kv_rows, write_artifacts
from research.e4_x2_z3_causal_concentration_recheck_v1.spec import canonical_spec, source_sha256, spec_sha256
from research.simple_full_strategy_discovery_v1 import DEVELOPMENT_DAYS

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
        "RETUNE": False,
    }


def freeze_path() -> Path:
    return CACHE / "strategy_freeze.json"


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items()}),
        "precommit": kv_rows(report.get("precommit") or {}),
        "pin": kv_rows(report.get("pin") or {}),
        "method": kv_rows(report.get("method_audit") or {}),
        "parity": kv_rows((report.get("parity") or {}).get("diffs") or report.get("parity") or {}),
        "symbol_ledger": list(report.get("symbol_ledger") or [{"empty": True}]),
        "e4": kv_rows(strip(report.get("e4_pack") or {})),
        "corrected_pass": list(report.get("corrected_pass_rows") or [{"empty": True}]),
        "lodo": list((report.get("lodo") or {}).get("folds") or [{"empty": True}]),
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
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE E4_X2_Z3 CAUSAL CONCENTRATION RECHECK V1", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("Read-only prior DEV cache. Stress sealed. No E4 retune.", flush=True)

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

    spec = canonical_spec()
    sha = spec_sha256(spec)
    src_sha = source_sha256()
    print(f"SPEC_SHA256 {sha}", flush=True)
    print(f"SOURCE_SHA256 {src_sha}", flush=True)

    method = audit_drop_top_method()
    print(f"DROP_TOP_METHOD {method.get('DROP_TOP_SYMBOL_METHOD')} class={method.get('METHOD_CLASS')}", flush=True)

    print("PHASE load prior DEVELOPMENT grid cache", flush=True)
    har = load_prior_development_grid()
    if not har.get("ok"):
        decision = decide(
            integrity=False,
            parity_ok_flag=False,
            method_class=str(method.get("METHOD_CLASS") or ""),
            e4_causal_pnl=None,
            pass_n=0,
            winner_id=None,
            lodo_pack=None,
        )
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "precommit": spec,
            "pin": {"SPEC_SHA256": sha, "SOURCE_SHA256": src_sha},
            "method_audit": method,
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
    days = list(DEVELOPMENT_DAYS)
    print("PHASE Full DEV 75 candidates with corrected CAUSAL_EX_TOP1 where other gates pass", flush=True)
    evaluated = evaluate_corrected_set(rows_by, ctrl_by, days=days)
    e4 = next(r for r in evaluated if r.get("candidate_id") == FOCUS_CANDIDATE)
    parity = parity_check(e4)
    print(f"BASE_PARITY ok={parity.get('ok')}", flush=True)
    if not parity.get("ok"):
        print(json.dumps(parity.get("diffs"), default=str), flush=True)

    trades = list(e4.get("_trades") or [])
    ledger = symbol_ledger(trades)
    top, top_pnl = top_symbol_from_trades(trades)
    top_trades = [t for t in trades if str(t.get("symbol") or "").replace(".T", "") == str(top or "")]
    rest_trades = [t for t in trades if str(t.get("symbol") or "").replace(".T", "") != str(top or "")]
    top_d = notional_diagnostic(top_trades)
    port_d = notional_diagnostic(trades)
    rest_d = notional_diagnostic(rest_trades)
    top_pack = {
        "symbol": top,
        "trade_n": len(top_trades),
        "net_pnl": top_pnl,
        "bps": {
            "trade_n": top_d["trade_n"],
            "trade_weighted_pnl_bps": top_d["trade_weighted_pnl_bps"],
            "avg_pnl_bps": top_d["avg_pnl_bps"],
            "median_pnl_bps": top_d["median_pnl_bps"],
            "portfolio_trade_weighted_pnl_bps": port_d["trade_weighted_pnl_bps"],
            "portfolio_avg_pnl_bps": port_d["avg_pnl_bps"],
            "portfolio_median_pnl_bps": port_d["median_pnl_bps"],
            "rest_trade_weighted_pnl_bps": rest_d["trade_weighted_pnl_bps"],
        },
        "notional": {
            "trade_n": top_d["trade_n"],
            "median_entry_price": top_d["median_entry_price"],
            "median_notional_100": top_d["median_notional_100"],
            "portfolio_trade_n": port_d["trade_n"],
            "portfolio_median_entry_price": port_d["median_entry_price"],
            "portfolio_median_notional_100": port_d["median_notional_100"],
            "rest_trade_n": rest_d["trade_n"],
            "rest_median_entry_price": rest_d["median_entry_price"],
            "rest_median_notional_100": rest_d["median_notional_100"],
        },
    }
    posthoc = posthoc_ex_top1(trades, str(top or ""), days=days) if top else {}
    causal = dict(e4.get("_causal") or {})
    post_pnl = float(posthoc.get("TOTAL_PNL") or 0.0) if posthoc else None
    cau_pnl = e4.get("CAUSAL_EX_TOP1_PNL")
    refill = None if post_pnl is None or cau_pnl is None else float(cau_pnl) - float(post_pnl)
    print(
        f"TOP_SYMBOL={top} n={len(top_trades)} pnl={top_pnl} posthoc={post_pnl} causal={cau_pnl} refill={refill}",
        flush=True,
    )
    print(
        f"E4 other_gates={e4.get('other_gates')} corrected_gate={e4.get('corrected_gate')}",
        flush=True,
    )

    passed = rank_corrected(evaluated)
    pass_rows = [public_pass_row(r) for r in passed]
    winner = dict(passed[0]) if passed else None
    if winner:
        winner = strip(winner)
        winner.pop("_causal", None)
    print(
        f"PRIOR_ECONOMIC_PASS_N={PRIOR_ECONOMIC_PASS_N} CORRECTED_ECONOMIC_PASS_N={len(passed)} winner={None if winner is None else winner.get('candidate_id')}",
        flush=True,
    )

    lodo_pack = None
    if winner is not None and method.get("METHOD_CLASS") == "A":
        print("PHASE LODO 10 folds fold-local top symbol CAUSAL_EX_TOP1", flush=True)
        lodo_pack = lodo(rows_by, ctrl_by, winner.get("candidate_id"))
        print(
            f"LODO SELECTED_N={lodo_pack.get('WINNER_SELECTED_N')} TOP3_N={lodo_pack.get('WINNER_TOP3_N')} stable={lodo_pack.get('stable')}",
            flush=True,
        )

    integ = integrity_ok()
    decision = decide(
        integrity=integ,
        parity_ok_flag=bool(parity.get("ok")),
        method_class=str(method.get("METHOD_CLASS") or ""),
        e4_causal_pnl=None if cau_pnl is None else float(cau_pnl),
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
        "ENTRY": "E4_VWAP_RECLAIM_SIMPLE" if (winner or {}).get("candidate_id") == FOCUS_CANDIDATE else (winner or {}).get("entry_name"),
        "EXECUTION": "X2_PASSIVE_MID_THEN_CANCEL" if (winner or {}).get("candidate_id") == FOCUS_CANDIDATE else (winner or {}).get("exec_name"),
        "EXIT": "Z3_TWO_BAR_WEAKNESS" if (winner or {}).get("candidate_id") == FOCUS_CANDIDATE else (winner or {}).get("exit_name"),
    }
    if decision.get("FULL_STRATEGY_DEV_FROZEN") and winner is not None:
        CACHE.mkdir(parents=True, exist_ok=True)
        freeze_path().write_text(
            json.dumps({**pin, "winner": winner}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"FREEZE {pin['FREEZE_TIMESTAMP']} winner={winner.get('candidate_id')}", flush=True)

    after = snapshot(phase="POST")
    e4_pub = strip(e4)
    e4_pub.pop("_causal", None)
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": spec,
        "pin": pin,
        "method_audit": method,
        "parity": parity,
        "e4_pack": e4_pub,
        "top_symbol_pack": top_pack,
        "symbol_ledger": ledger,
        "posthoc": posthoc,
        "causal": causal,
        "CAUSAL_REFILL_EFFECT": refill,
        "CORRECTED_ECONOMIC_PASS_N": len(passed),
        "corrected_pass_ids": [r.get("candidate_id") for r in passed],
        "corrected_pass_rows": pass_rows,
        "provisional_winner": winner,
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
        "other_gate_pass_n": sum(1 for r in evaluated if other_gate_pass(r)),
    }
    _publish(report)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
