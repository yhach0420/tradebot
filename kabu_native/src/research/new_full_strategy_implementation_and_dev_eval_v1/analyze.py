"""Decision order: integrity → session-exit → coverage → G1-G6 → robustness. No RCA."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Optional

from research.anchor_timing_robustness.metrics import maxdd
from research.new_full_strategy_implementation_and_dev_eval_v1 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    ARCHITECTURE_CHANGED,
    BASE_ECONOMIC_RUN_N,
    CASE_COVERAGE_FAIL,
    CASE_GATES_FAIL,
    CASE_INTEGRITY_FAIL,
    CASE_PASS,
    CASE_ROBUST_FAIL,
    CASE_SESSION_EXIT_FAIL,
    CERTIFIED,
    DEVELOPMENT_DAYS,
    ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS,
    G6_CAUSAL_RERUN_N_MAX,
    MIN_PF,
    MIN_TOTAL_TRADES,
    MIN_TRADES_PER_DAY,
    MIN_TRADING_DAYS_WITH_FILL,
    NEXT_IF_ECONOMIC_FAIL,
    NEXT_IF_INTEGRITY_FAIL,
    NEXT_IF_PASS,
    OLD_ST_RCA_CONTINUED,
    PINNED_V3_SHA256,
    POSITIVE_BLOCK_MIN,
    POST_RESULT_ARCHITECTURE_CHANGE,
    TRUE_OOS,
    V3_HASH_CHANGED,
)
from research.new_full_strategy_implementation_and_dev_eval_v1.harvest import harvest_days, structural_integrity
from research.new_full_strategy_implementation_and_dev_eval_v1.integrity import run_integrity
from research.new_full_strategy_implementation_and_dev_eval_v1.isolation import OUT
from research.new_full_strategy_implementation_and_dev_eval_v1.spec import source_sha256, v3_hash_unchanged, v3_sha256
from research.simple_full_strategy_discovery_v1.analyze import _pf
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS
from replay.pnl_yen import compute_pnl_yen_100

PNL_KEYS_EMBARGO = (
    "TOTAL_PNL",
    "PF",
    "MaxDD",
    "MAXDD",
    "EX_BEST",
    "EX_BEST_DAY_PNL",
    "EX_BEST_BLOCK_TOTAL_PNL",
    "CAUSAL_EX_TOP1",
    "CAUSAL_EX_TOP1_PNL",
    "top_symbol",
    "top_symbol_pnl",
    "pnl_yen_100",
    "G1_TOTAL_PNL",
    "G2_PF",
    "G3_DAY_SIGNS",
    "G4_EX_BEST",
    "G5_PNL_PLUS_MAXDD",
    "G6_CAUSAL_EX_TOP1",
)


def already_executed_check(source_hash: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "OUT_REPORT_ABSENT"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "ANALYSIS_ID_MISMATCH"}
    if str(prev.get("source_sha256") or "") == source_hash:
        return {
            "ALREADY_EXECUTED_CHECK": True,
            "REUSED_EXISTING_RESULT": True,
            "REASON": "SAME_METHODOLOGY",
            "prior_report": prev,
        }
    return {
        "ALREADY_EXECUTED_CHECK": True,
        "REUSED_EXISTING_RESULT": False,
        "REASON": "EXISTING_OUT_DIFFERENT_SPEC",
    }


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def attach_pnl(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        row["pnl_yen_100"] = float(
            compute_pnl_yen_100(float(row["fill_price"]), float(row["exit_price"]), side="long")
        )
        out.append(row)
    return out


def pack_trades(trades: list[dict[str, Any]], *, days: list[str]) -> dict[str, Any]:
    n = len(trades)
    pnls = [float(t.get("pnl_yen_100") or 0.0) for t in trades]
    total = float(sum(pnls))
    by_day: dict[str, float] = {d: 0.0 for d in days}
    by_day_n: dict[str, int] = {d: 0 for d in days}
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        d = str(t.get("date") or "")
        by_day[d] = float(by_day.get(d) or 0.0) + float(t.get("pnl_yen_100") or 0.0)
        by_day_n[d] = int(by_day_n.get(d) or 0) + 1
        by_sym[str(t.get("symbol") or "")] += float(t.get("pnl_yen_100") or 0.0)
    pos_d = sum(1 for d in days if float(by_day.get(d) or 0.0) > 1e-12)
    neg_d = sum(1 for d in days if float(by_day.get(d) or 0.0) < -1e-12)
    fill_days = sum(1 for d in days if int(by_day_n.get(d) or 0) > 0)
    best_d = max(days, key=lambda d: float(by_day.get(d) or 0.0)) if days else None
    top_day_pnl = float(by_day.get(best_d) or 0.0) if best_d else 0.0
    ex_best = total - top_day_pnl if best_d else total
    top_sym = max(by_sym.keys(), key=lambda s: by_sym[s]) if by_sym else None
    top_sym_pnl = float(by_sym.get(top_sym) or 0.0) if top_sym else 0.0
    dd = float(maxdd(trades, time_key="exit_t", pnl_key="pnl_yen_100")) if trades else 0.0
    n_days = max(len(days), 1)
    return {
        "TRADE_N": n,
        "TOTAL_PNL": total,
        "PF": _pf(trades),
        "MAXDD": dd,
        "positive_day_n": pos_d,
        "negative_day_n": neg_d,
        "TRADING_DAY_WITH_FILL_N": fill_days,
        "trades_per_day": float(n) / float(n_days),
        "best_day": best_d,
        "best_day_pnl": top_day_pnl if best_d else None,
        "EX_BEST_DAY_PNL": ex_best,
        "top_symbol": top_sym,
        "top_symbol_pnl": top_sym_pnl,
        "daily_pnl": dict(by_day),
        "daily_n": dict(by_day_n),
        "by_symbol_pnl": dict(by_sym),
    }


def coverage_from_counts(*, trade_n: int, fill_days: int, n_days: int) -> dict[str, Any]:
    tpd = float(trade_n) / float(max(n_days, 1))
    ok = (
        int(trade_n) >= int(MIN_TOTAL_TRADES)
        and int(fill_days) >= int(MIN_TRADING_DAYS_WITH_FILL)
        and float(tpd) >= float(MIN_TRADES_PER_DAY)
    )
    return {
        "TRADE_N": int(trade_n),
        "TRADING_DAY_WITH_FILL_N": int(fill_days),
        "trades_per_day": float(tpd),
        "COVERAGE_PASS": bool(ok),
        "MIN_TOTAL_TRADES": int(MIN_TOTAL_TRADES),
        "MIN_TRADING_DAYS_WITH_FILL": int(MIN_TRADING_DAYS_WITH_FILL),
        "MIN_TRADES_PER_DAY": float(MIN_TRADES_PER_DAY),
    }


def g_table(pack: dict[str, Any], causal_pnl: Optional[float]) -> dict[str, Any]:
    total = _f(pack.get("TOTAL_PNL"))
    pf = pack.get("PF")
    pf_ok = pf is not None and (pf == float("inf") or float(pf) > float(MIN_PF))
    dd = _f(pack.get("MAXDD")) or 0.0
    return {
        "G1_TOTAL_PNL": bool(total is not None and float(total) > 0),
        "G2_PF": bool(pf_ok),
        "G3_DAY_SIGNS": int(pack.get("positive_day_n") or 0) > int(pack.get("negative_day_n") or 0),
        "G4_EX_BEST": _f(pack.get("EX_BEST_DAY_PNL")) is not None and float(pack["EX_BEST_DAY_PNL"]) > 0,
        "G5_PNL_PLUS_MAXDD": bool((total or 0.0) + float(dd) > 0),
        "G6_CAUSAL_EX_TOP1": causal_pnl is not None and float(causal_pnl) >= 0.0,
    }


def block_metrics(daily: dict[str, float]) -> dict[str, Any]:
    block_pnl = {b: float(sum(float(daily.get(d) or 0.0) for d in days)) for b, days in FOLD_BLOCKS.items()}
    vals = list(block_pnl.values())
    pos = sum(1 for v in vals if v > 1e-12)
    best = max(vals) if vals else 0.0
    total = float(sum(vals))
    ex_best = total - float(best) if vals else 0.0
    ok = int(pos) >= int(POSITIVE_BLOCK_MIN) and float(ex_best) >= 0.0
    return {
        "B1_B5_PNL": block_pnl,
        "positive_block_n": int(pos),
        "POSITIVE_BLOCK_MIN": int(POSITIVE_BLOCK_MIN),
        "EX_BEST_BLOCK_TOTAL_PNL": float(ex_best),
        "ROBUSTNESS_PASS": bool(ok),
    }


def _flags_common() -> dict[str, Any]:
    return {
        "ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS": ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS,
        "V3_HASH_CHANGED": V3_HASH_CHANGED,
        "V3_HASH_UNCHANGED": v3_hash_unchanged(),
        "POST_RESULT_ARCHITECTURE_CHANGE": POST_RESULT_ARCHITECTURE_CHANGE,
        "ARCHITECTURE_CHANGED": ARCHITECTURE_CHANGED,
        "BASE_ECONOMIC_RUN_N": BASE_ECONOMIC_RUN_N,
        "G6_CAUSAL_RERUN_N_MAX": G6_CAUSAL_RERUN_N_MAX,
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "ANOTHER_PRECOMMIT_RUN": ANOTHER_PRECOMMIT_RUN,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "FULL_STRATEGY_SPEC_SHA256_V3": v3_sha256(),
        "PINNED_V3_SHA256": PINNED_V3_SHA256,
    }


def decide(*, run_economics: bool = True) -> dict[str, Any]:
    integ = run_integrity()
    flags = _flags_common()
    impl_ok = bool(integ.get("V3_IMPLEMENTATION_MATCH"))
    sess_ok = bool(integ.get("SESSION_EXIT_INTEGRITY_PASS"))
    all_int = bool(integ.get("ALL_INTEGRITY_TESTS_PASS"))
    stage = "implementation_integrity"
    g6_n = 0
    base_n = 0
    economics: dict[str, Any] | None = None
    coverage = None
    harvest_struct = None
    if not impl_ok:
        verdict = CASE_INTEGRITY_FAIL
        nxt = NEXT_IF_INTEGRITY_FAIL
        failed = "implementation_integrity"
    elif not sess_ok or not all_int:
        verdict = CASE_SESSION_EXIT_FAIL
        nxt = NEXT_IF_INTEGRITY_FAIL
        failed = "session_exit_integrity"
        stage = "session_exit_integrity"
    elif not run_economics:
        verdict = CASE_INTEGRITY_FAIL
        nxt = NEXT_IF_INTEGRITY_FAIL
        failed = "economics_not_requested"
        stage = "session_exit_integrity"
    else:
        stage = "coverage"
        base = harvest_days(DEVELOPMENT_DAYS, exclude_symbol="", use_cache=True)
        base_n = 1
        harvest_struct = structural_integrity(base)
        if not harvest_struct.get("HARVEST_STRUCTURAL_PASS"):
            verdict = CASE_SESSION_EXIT_FAIL
            nxt = NEXT_IF_INTEGRITY_FAIL
            failed = "harvest_structural_session_exit"
            stage = "session_exit_integrity"
            economics = None
        else:
            trades_raw = list(base.get("trades") or [])
            days = list(DEVELOPMENT_DAYS)
            fill_days = len({str(t.get("date")) for t in trades_raw})
            coverage = coverage_from_counts(trade_n=len(trades_raw), fill_days=fill_days, n_days=len(days))
            if not coverage.get("COVERAGE_PASS"):
                verdict = CASE_COVERAGE_FAIL
                nxt = NEXT_IF_ECONOMIC_FAIL
                failed = "coverage"
            else:
                stage = "g1_g6"
                priced = attach_pnl(trades_raw)
                pack = pack_trades(priced, days=days)
                top = str(pack.get("top_symbol") or "")
                g6 = harvest_days(DEVELOPMENT_DAYS, exclude_symbol=top, use_cache=True)
                g6_n = 1
                g6_struct = structural_integrity(g6)
                g6_priced = attach_pnl(list(g6.get("trades") or []))
                g6_pack = pack_trades(g6_priced, days=days)
                causal_pnl = float(g6_pack.get("TOTAL_PNL") or 0.0)
                gt = g_table(pack, causal_pnl)
                blocks = block_metrics(dict(pack.get("daily_pnl") or {}))
                g_ok = all(bool(v) for v in gt.values()) and bool(g6_struct.get("HARVEST_STRUCTURAL_PASS"))
                economics = {
                    "base": pack,
                    "g_table": gt,
                    "CAUSAL_EX_TOP1_PNL": causal_pnl,
                    "CAUSAL_EX_TOP1_TRADE_N": g6_pack.get("TRADE_N"),
                    "CAUSAL_EX_TOP1_PF": g6_pack.get("PF"),
                    "g6_structural": g6_struct,
                    "blocks": blocks,
                    "TAINTED_ECONOMICS_DECISION_ELIGIBLE": True,
                }
                if not g_ok:
                    verdict = CASE_GATES_FAIL
                    nxt = NEXT_IF_ECONOMIC_FAIL
                    failed = "g1_g6"
                else:
                    stage = "temporal_robustness"
                    if not blocks.get("ROBUSTNESS_PASS"):
                        verdict = CASE_ROBUST_FAIL
                        nxt = NEXT_IF_ECONOMIC_FAIL
                        failed = "temporal_robustness"
                    else:
                        verdict = CASE_PASS
                        nxt = NEXT_IF_PASS
                        failed = None
    decision = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "FAILED_STAGE": failed,
        "DECISION_STAGE": stage,
        "V3_IMPLEMENTATION_MATCH": impl_ok,
        "ALL_INTEGRITY_TESTS_PASS": all_int,
        "SESSION_EXIT_INTEGRITY_PASS": sess_ok,
        "BASE_ECONOMIC_RUN_N": base_n,
        "G6_CAUSAL_RERUN_N": g6_n,
        "TAINTED_ECONOMICS_DECISION_ELIGIBLE": bool(economics is not None),
        "CSB_RCA_RUN": False,
        "ENTRY_RCA_RUN": False,
        "EXIT_RCA_RUN": False,
        **flags,
    }
    return {
        "integrity": integ,
        "harvest_structural": harvest_struct,
        "coverage": coverage,
        "economics": economics,
        "decision": decision,
        "flags": flags,
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    d = dict(pack.get("decision") or {})
    integ = dict(pack.get("integrity") or {})
    cov = dict(pack.get("coverage") or {})
    eco = dict(pack.get("economics") or {})
    gt = dict(eco.get("g_table") or {})
    blocks = dict(eco.get("blocks") or {})
    base = dict(eco.get("base") or {})
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "V3_IMPLEMENTATION_MATCH": d.get("V3_IMPLEMENTATION_MATCH"),
        "ALL_INTEGRITY_TESTS_PASS": d.get("ALL_INTEGRITY_TESTS_PASS"),
        "ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS": ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS,
        "V3_HASH_UNCHANGED": d.get("V3_HASH_UNCHANGED"),
        "V3_HASH_CHANGED": d.get("V3_HASH_CHANGED"),
        "FULL_STRATEGY_SPEC_SHA256_V3": d.get("FULL_STRATEGY_SPEC_SHA256_V3"),
        "POST_RESULT_ARCHITECTURE_CHANGE": POST_RESULT_ARCHITECTURE_CHANGE,
        "BASE_ECONOMIC_RUN_N": d.get("BASE_ECONOMIC_RUN_N"),
        "G6_CAUSAL_RERUN_N": d.get("G6_CAUSAL_RERUN_N"),
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "ANOTHER_PRECOMMIT_RUN": ANOTHER_PRECOMMIT_RUN,
        "FAILED_STAGE": d.get("FAILED_STAGE"),
        "DECISION_STAGE": d.get("DECISION_STAGE"),
        "COVERAGE_PASS": cov.get("COVERAGE_PASS"),
        "TRADE_N": cov.get("TRADE_N") if eco else cov.get("TRADE_N"),
        "G1_G6": gt if gt else None,
        "ROBUSTNESS_PASS": blocks.get("ROBUSTNESS_PASS"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "integrity_implementation_n": len(integ.get("implementation") or []),
        "integrity_session_exit_n": len(integ.get("session_exit") or []),
        "top_symbol": base.get("top_symbol") if eco else None,
        "TOTAL_PNL": base.get("TOTAL_PNL") if eco else None,
        "CAUSAL_EX_TOP1_PNL": eco.get("CAUSAL_EX_TOP1_PNL") if eco else None,
    }
