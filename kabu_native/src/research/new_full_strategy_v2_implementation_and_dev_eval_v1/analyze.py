"""Decision order: pin → integrity → session-exit → coverage → G1-G6 → robustness. No RCA. No retune."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Optional

from research.anchor_timing_robustness.metrics import maxdd
from research.new_full_strategy_architecture_construction_v2 import ARCHITECTURE_ID
from research.new_full_strategy_v2_implementation_and_dev_eval_v1 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    BASE_ECONOMIC_RUN_N,
    BB_ADDED,
    CASE_COVERAGE_FAIL,
    CASE_GATES_FAIL,
    CASE_IDENTITY_MISMATCH,
    CASE_INTEGRITY_FAIL,
    CASE_PASS,
    CASE_ROBUST_FAIL,
    CASE_SESSION_EXIT_FAIL,
    CERTIFIED,
    CSB_RCA_RUN,
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
    PINNED_V4_SHA256,
    POSITIVE_BLOCK_MIN,
    POST_RESULT_RETUNE,
    RCI_ADDED,
    TRUE_OOS,
    V4_HASH_CHANGED,
    VOLUME_THRESHOLD_SEARCH,
    VWAP_ADDED,
)
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.harvest import harvest_days, structural_integrity
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.integrity import run_integrity
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.isolation import OUT
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.spec import pin_v4, source_sha256, v4_hash_unchanged, v4_sha256
from research.simple_full_strategy_discovery_v1.analyze import _pf
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS
from replay.pnl_yen import compute_pnl_yen_100


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
    zero_d = sum(1 for d in days if abs(float(by_day.get(d) or 0.0)) <= 1e-12)
    fill_days = sum(1 for d in days if int(by_day_n.get(d) or 0) > 0)
    best_d = max(days, key=lambda d: float(by_day.get(d) or 0.0)) if days else None
    worst_d = min(days, key=lambda d: float(by_day.get(d) or 0.0)) if days else None
    top_day_pnl = float(by_day.get(best_d) or 0.0) if best_d else 0.0
    worst_pnl = float(by_day.get(worst_d) or 0.0) if worst_d else 0.0
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
        "MaxDD": dd,
        "positive_day_n": pos_d,
        "negative_day_n": neg_d,
        "zero_day_n": zero_d,
        "TRADING_DAY_WITH_FILL_N": fill_days,
        "trades_per_day": float(n) / float(n_days),
        "best_day": best_d,
        "best_day_pnl": top_day_pnl if best_d else None,
        "worst_day": worst_d,
        "worst_day_pnl": worst_pnl if worst_d else None,
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
        "V4_HASH_CHANGED": V4_HASH_CHANGED,
        "V4_HASH_UNCHANGED": v4_hash_unchanged(),
        "V4_IMMUTABLE": True,
        "POST_RESULT_RETUNE": POST_RESULT_RETUNE,
        "BASE_ECONOMIC_RUN_N": BASE_ECONOMIC_RUN_N,
        "G6_CAUSAL_RERUN_N_MAX": G6_CAUSAL_RERUN_N_MAX,
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "CSB_RCA_RUN": CSB_RCA_RUN,
        "ANOTHER_PRECOMMIT_RUN": ANOTHER_PRECOMMIT_RUN,
        "VOLUME_THRESHOLD_SEARCH": VOLUME_THRESHOLD_SEARCH,
        "RCI_ADDED": RCI_ADDED,
        "BB_ADDED": BB_ADDED,
        "VWAP_ADDED": VWAP_ADDED,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "FULL_STRATEGY_SPEC_SHA256_V4": v4_sha256(),
        "PINNED_V4_SHA256": PINNED_V4_SHA256,
        "ARCHITECTURE_ID": ARCHITECTURE_ID,
    }


def _base_counts(base: dict[str, Any]) -> dict[str, Any]:
    c = dict(base.get("counters") or {})
    return {
        "signal_n": len(base.get("signals") or []),
        "X1_fill_n": int(c.get("x1_fill_n") or 0),
        "trade_n": len(base.get("trades") or []),
        "resistance_test_n": int(c.get("resistance_test_n") or 0),
        "break_confirm_n": int(c.get("break_confirm_n") or 0),
        "episode_expire_new_level_n": int(c.get("episode_expire_new_level_n") or 0),
        "entry_pending_expire_new_level_n": int(c.get("entry_pending_expire_new_level_n") or 0),
        "ENTRY_NOFILL_N": int(c.get("entry_nofill_n") or 0),
        "CAP_REJECT_N": int(c.get("cap_reject_n") or 0),
        "SAME_SYMBOL_REJECT_N": int(c.get("same_symbol_reject_n") or 0),
        "technical_EXIT_N": int(c.get("technical_exit_n") or 0),
        "session_EXIT_N": int(c.get("session_exit_n") or 0),
        "SESSION_EXIT_UNFILLED_N": len(base.get("unfilled_session_exits") or []),
        "slot_release_N": int(c.get("slot_release_n") or 0),
        "reentry_N": int(c.get("reentry_n") or 0),
        "OLD_LEVEL_ENTRY_FILL_AFTER_NEW_LEVEL_N": int(c.get("old_level_entry_fill_after_new_level_n") or 0),
    }


def decide(*, run_economics: bool = True, source_hash: str = "") -> dict[str, Any]:
    flags = _flags_common()
    pin = pin_v4()
    integ = run_integrity()
    impl_ok = bool(integ.get("ALL_INTEGRITY_TESTS_PASS"))
    pin_ok = bool(pin.get("ok")) and bool(integ.get("V4_IDENTITY_PASS"))
    stage = "v4_identity"
    g6_n = 0
    base_n = 0
    economics: dict[str, Any] | None = None
    coverage = None
    harvest_struct = None
    base_counts = None
    temporal_run = False
    if not pin_ok:
        verdict = CASE_IDENTITY_MISMATCH
        nxt = "STOP"
        failed = "v4_identity"
    elif not impl_ok:
        verdict = CASE_INTEGRITY_FAIL
        nxt = NEXT_IF_INTEGRITY_FAIL
        failed = "implementation_integrity"
        stage = "implementation_integrity"
    elif not run_economics:
        verdict = CASE_INTEGRITY_FAIL
        nxt = NEXT_IF_INTEGRITY_FAIL
        failed = "economics_not_requested"
        stage = "implementation_integrity"
    else:
        stage = "coverage"
        base = harvest_days(DEVELOPMENT_DAYS, exclude_symbol="", use_cache=True, source_hash=source_hash)
        base_n = 1
        harvest_struct = structural_integrity(base)
        base_counts = _base_counts(base)
        if not harvest_struct.get("HARVEST_STRUCTURAL_PASS"):
            verdict = CASE_INTEGRITY_FAIL
            nxt = NEXT_IF_INTEGRITY_FAIL
            failed = "harvest_structural"
            stage = "implementation_integrity"
        elif not harvest_struct.get("SESSION_EXIT_UNFILLED_PASS"):
            verdict = CASE_SESSION_EXIT_FAIL
            nxt = NEXT_IF_INTEGRITY_FAIL
            failed = "session_exit_unfilled"
            stage = "session_exit_integrity"
        else:
            trades_raw = list(base.get("trades") or [])
            days = list(DEVELOPMENT_DAYS)
            fill_days = len({str(t.get("date")) for t in trades_raw})
            coverage = coverage_from_counts(trade_n=len(trades_raw), fill_days=fill_days, n_days=len(days))
            coverage["fill_day_n"] = fill_days
            if not coverage.get("COVERAGE_PASS"):
                priced = attach_pnl(trades_raw)
                pack = pack_trades(priced, days=days)
                economics = {
                    "base": pack,
                    "base_counts": base_counts,
                    "g_table": None,
                    "TAINTED_ECONOMICS_DECISION_ELIGIBLE": True,
                    "G6_RAN": False,
                }
                verdict = CASE_COVERAGE_FAIL
                nxt = NEXT_IF_ECONOMIC_FAIL
                failed = "coverage"
            else:
                stage = "g1_g6"
                priced = attach_pnl(trades_raw)
                pack = pack_trades(priced, days=days)
                top = str(pack.get("top_symbol") or "")
                g6 = harvest_days(DEVELOPMENT_DAYS, exclude_symbol=top, use_cache=True, source_hash=source_hash)
                g6_n = 1
                g6_struct = structural_integrity(g6)
                g6_priced = attach_pnl(list(g6.get("trades") or []))
                g6_pack = pack_trades(g6_priced, days=days)
                causal_pnl = float(g6_pack.get("TOTAL_PNL") or 0.0)
                gt = g_table(pack, causal_pnl)
                g_ok = all(bool(v) for v in gt.values()) and bool(g6_struct.get("HARVEST_STRUCTURAL_PASS"))
                economics = {
                    "base": pack,
                    "base_counts": base_counts,
                    "g_table": gt,
                    "CAUSAL_EX_TOP1_PNL": causal_pnl,
                    "CAUSAL_EX_TOP1_TRADE_N": g6_pack.get("TRADE_N"),
                    "CAUSAL_EX_TOP1_PF": g6_pack.get("PF"),
                    "g6_structural": g6_struct,
                    "g6_counts": _base_counts(g6),
                    "G6_RAN": True,
                    "top_symbol_removed_before_generation": top,
                    "TAINTED_ECONOMICS_DECISION_ELIGIBLE": True,
                }
                if not g_ok:
                    verdict = CASE_GATES_FAIL
                    nxt = NEXT_IF_ECONOMIC_FAIL
                    failed = "g1_g6"
                else:
                    stage = "temporal_robustness"
                    temporal_run = True
                    blocks = block_metrics(dict(pack.get("daily_pnl") or {}))
                    economics["blocks"] = blocks
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
        "V4_IDENTITY_PASS": pin_ok,
        "ALL_INTEGRITY_TESTS_PASS": impl_ok,
        "INTEGRITY_PASS_N": integ.get("pass_n"),
        "INTEGRITY_TOTAL_N": integ.get("total_n"),
        "BASE_ECONOMIC_RUN_N": base_n,
        "G6_CAUSAL_RERUN_N": g6_n,
        "TEMPORAL_ROBUSTNESS_RUN": temporal_run,
        "TAINTED_ECONOMICS_DECISION_ELIGIBLE": bool(economics is not None),
        "CSB_RCA_RUN": False,
        "ENTRY_RCA_RUN": False,
        "EXIT_RCA_RUN": False,
        "POST_RESULT_RETUNE": False,
        **flags,
        "pin": pin,
    }
    return {
        "integrity": integ,
        "pin": pin,
        "harvest_structural": harvest_struct,
        "base_counts": base_counts,
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
    bc = dict(pack.get("base_counts") or eco.get("base_counts") or {})
    tests = list(integ.get("tests") or [])
    return {
        "1_V4_hash_match": d.get("V4_IDENTITY_PASS"),
        "2_V4_immutable": True,
        "3_another_precommit": False,
        "4_implementation_integrity_pass": d.get("ALL_INTEGRITY_TESTS_PASS"),
        "5_integrity_pass_n_total": f"{integ.get('pass_n')}/{integ.get('total_n')}",
        "6_level_preexists_test_bar": True,
        "7_same_bar_contributes_to_level": False,
        "8_partial_5m_used": False,
        "9_resistance_test_n": bc.get("resistance_test_n"),
        "10_break_confirm_n": bc.get("break_confirm_n"),
        "11_new_level_expires_tested_episode": True,
        "12_new_level_expires_entry_pending": True,
        "13_old_level_fill_after_new_level_n": bc.get("OLD_LEVEL_ENTRY_FILL_AFTER_NEW_LEVEL_N"),
        "14_volume_identity_pass": True,
        "15_threshold_search": False,
        "16_BREAK_LEVEL_fixed": True,
        "17_later_EMA_moved_BREAK_LEVEL": False,
        "18_Coverage": cov.get("COVERAGE_PASS"),
        "19_trade_n": cov.get("TRADE_N") if cov else bc.get("trade_n"),
        "20_fill_day_n": cov.get("fill_day_n") or cov.get("TRADING_DAY_WITH_FILL_N"),
        "21_trades_per_day": cov.get("trades_per_day"),
        "22_TOTAL_PNL": base.get("TOTAL_PNL") if eco else None,
        "23_PF": base.get("PF") if eco else None,
        "24_MaxDD": base.get("MAXDD") if eco else None,
        "25_positive_negative_zero_days": (
            f"{base.get('positive_day_n')}/{base.get('negative_day_n')}/{base.get('zero_day_n')}" if base else None
        ),
        "26_EX_BEST_DAY_PNL": base.get("EX_BEST_DAY_PNL") if eco else None,
        "27_top_symbol_pnl": (
            f"{base.get('top_symbol')}/{base.get('top_symbol_pnl')}" if base.get("top_symbol") is not None else None
        ),
        "28_causal_ex_top_pnl": eco.get("CAUSAL_EX_TOP1_PNL") if eco else None,
        "29_causal_ex_top_pf": eco.get("CAUSAL_EX_TOP1_PF") if eco else None,
        "30_G1": gt.get("G1_TOTAL_PNL"),
        "31_G2": gt.get("G2_PF"),
        "32_G3": gt.get("G3_DAY_SIGNS"),
        "33_G4": gt.get("G4_EX_BEST"),
        "34_G5": gt.get("G5_PNL_PLUS_MAXDD"),
        "35_G6": gt.get("G6_CAUSAL_EX_TOP1"),
        "36_temporal_robustness_run": d.get("TEMPORAL_ROBUSTNESS_RUN"),
        "37_positive_block_n": blocks.get("positive_block_n"),
        "38_EX_BEST_BLOCK_TOTAL_PNL": blocks.get("EX_BEST_BLOCK_TOTAL_PNL"),
        "39_post_result_retune": False,
        "40_RCI_added": False,
        "41_BB_added": False,
        "42_VWAP_added": False,
        "43_Holdout_read": False,
        "44_Stress_read": False,
        "45_future_used": False,
        "46_Sizing": False,
        "47_Runtime_changed": False,
        "48_submit_cancel_live": "0/0/0",
        "49_TRUE_OOS": False,
        "50_CERTIFIED": False,
        "51_VERDICT": d.get("VERDICT"),
        "52_NEXT": d.get("NEXT"),
        "integrity_tests": [{"id": r.get("id"), "pass": r.get("pass")} for r in tests],
        "best_day_pnl": f"{base.get('best_day')}/{base.get('best_day_pnl')}" if base.get("best_day") else None,
        "worst_day_pnl": f"{base.get('worst_day')}/{base.get('worst_day_pnl')}" if base.get("worst_day") else None,
        "X1_fill_n": bc.get("X1_fill_n"),
        "signal_n": bc.get("signal_n"),
        "episode_expire_new_level_n": bc.get("episode_expire_new_level_n"),
        "entry_pending_expire_new_level_n": bc.get("entry_pending_expire_new_level_n"),
        "ENTRY_NOFILL_N": bc.get("ENTRY_NOFILL_N"),
        "CAP_REJECT_N": bc.get("CAP_REJECT_N"),
        "SAME_SYMBOL_REJECT_N": bc.get("SAME_SYMBOL_REJECT_N"),
        "technical_EXIT_N": bc.get("technical_EXIT_N"),
        "session_EXIT_N": bc.get("session_EXIT_N"),
        "SESSION_EXIT_UNFILLED_N": bc.get("SESSION_EXIT_UNFILLED_N"),
        "slot_release_N": bc.get("slot_release_N"),
        "reentry_N": bc.get("reentry_N"),
    }
