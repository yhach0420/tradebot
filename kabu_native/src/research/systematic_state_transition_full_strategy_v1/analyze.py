"""Canary parity then Full Causal 25-candidate ranking. G6 diagnostic only. 5 two-day folds."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

from research.simple_full_strategy_discovery_v1.analyze import pack_trades
from research.simple_tech_entry_family.portfolio import _sym, portfolio_replay
from research.systematic_state_transition_full_strategy_v1 import (
    CANARY_EXPECTED,
    CANARY_ID,
    CANARY_OPTIONAL,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_D,
    CASE_E,
    DEVELOPMENT_DAYS,
    FOLD_BLOCKS,
    MIN_PF,
    MIN_TOTAL_TRADES,
    MIN_TRADES_PER_DAY,
    MIN_TRADING_DAYS_WITH_FILL,
    PORTFOLIO_WAIT_SEC,
    POSITION_CAP,
)
from research.systematic_state_transition_full_strategy_v1.harvest import AUDIT, CANARY_ROW_ID
from research.systematic_state_transition_full_strategy_v1.spec import candidate_ids


def leakage_n() -> dict[str, int]:
    keys = (
        "HOLDOUT_BURNED_READ_N",
        "STRESS_READ_N",
        "STRESS_FILE_OPEN_N",
        "STRESS_METRIC_COMPUTE_N",
        "FUTURE_DATA_N",
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
        "SPLIT_LEAKAGE_N",
        "EXTRA_CANDIDATE_N",
        "QUEUE_ASSUMED_FILL_N",
        "BAR_OHLC_FILL_N",
        "TRADE_PRINT_PASSIVE_FILL_N",
        "LOOKAHEAD_FILL_N",
        "REPRICE_N",
        "CHASE_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def integrity_ok() -> bool:
    return all(int(v) == 0 for v in leakage_n().values())


def execution_integrity_ok() -> bool:
    keys = (
        "QUEUE_ASSUMED_FILL_N",
        "BAR_OHLC_FILL_N",
        "TRADE_PRINT_PASSIVE_FILL_N",
        "LOOKAHEAD_FILL_N",
        "REPRICE_N",
        "CHASE_N",
    )
    return all(int(AUDIT.get(k) or 0) == 0 for k in keys)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def coverage_ok(p: dict[str, Any]) -> bool:
    return (
        int(p.get("TRADE_N") or 0) >= int(MIN_TOTAL_TRADES)
        and int(p.get("TRADING_DAY_WITH_FILL_N") or 0) >= int(MIN_TRADING_DAYS_WITH_FILL)
        and float(p.get("trades_per_day") or 0.0) >= float(MIN_TRADES_PER_DAY)
    )


def economic_base_ok(p: dict[str, Any]) -> bool:
    total = _f(p.get("TOTAL_PNL"))
    pf = p.get("PF")
    pf_ok = pf is not None and (pf == float("inf") or float(pf) > float(MIN_PF))
    dd = _f(p.get("MAXDD")) or 0.0
    return bool(
        total is not None
        and float(total) > 0.0
        and pf_ok
        and int(p.get("positive_day_n") or 0) > int(p.get("negative_day_n") or 0)
        and _f(p.get("EX_BEST_DAY_PNL")) is not None
        and float(p["EX_BEST_DAY_PNL"]) > 0.0
        and (float(total) + float(dd)) > 0.0
    )


def _pf_sort_key(pf: Any) -> float:
    if pf is None:
        return -1e18
    if pf == float("inf"):
        return 1e18
    return float(pf)


def top_symbol_from_trades(trades: list[dict[str, Any]]) -> tuple[Optional[str], float]:
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_sym[_sym(t)] += float(t.get("pnl_yen_100") or 0.0)
    if not by_sym:
        return None, 0.0
    top = max(by_sym.keys(), key=lambda s: by_sym[s])
    return top, float(by_sym[top])


def exclude_symbol(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    target = str(symbol).replace(".T", "")
    return [r for r in rows if _sym(r) != target]


def occupancy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("WOULD_FILL"):
            continue
        ft = _f(r.get("fill_t"))
        et = _f(r.get("exit_t"))
        if ft is None or et is None:
            continue
        rec = dict(r)
        rec["t0"] = float(ft)
        rec["signal_time"] = float(ft)
        out.append(rec)
    return out


def replay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return portfolio_replay(occupancy_rows(rows), wait_sec=float(PORTFOLIO_WAIT_SEC), position_cap=int(POSITION_CAP))


def filter_days(rows: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    keep = set(str(d) for d in days)
    return [r for r in rows if str(r.get("date") or "") in keep]


def corrected_score(base: dict[str, Any], causal_pnl: float) -> Optional[float]:
    n = int(base.get("TRADE_N") or 0)
    if n <= 0:
        return None
    return float(
        min(
            float(base.get("TOTAL_PNL") or 0.0) / float(n),
            float(base.get("EX_BEST_DAY_PNL") or 0.0) / float(n),
            float(causal_pnl) / float(n),
        )
    )


def evaluate_candidate(cid: str, rows: list[dict[str, Any]], *, days: list[str]) -> dict[str, Any]:
    occ = replay(rows)
    trades = list(occ.get("trades") or [])
    for t in trades:
        src = t.get("src") or {}
        t["hold_sec"] = t.get("hold_sec") if t.get("hold_sec") is not None else src.get("hold_sec")
        t["mfe_yen"] = src.get("mfe_yen")
        t["mae_yen"] = src.get("mae_yen")
        t["spread0"] = src.get("spread0")
    pack = pack_trades(trades, days=days)
    top, top_pnl = top_symbol_from_trades(trades)
    causal_pnl = None
    causal_pack: dict[str, Any] = {}
    if top:
        c_rows = exclude_symbol(rows, top)
        c_occ = replay(c_rows)
        c_trades = list(c_occ.get("trades") or [])
        causal_pack = pack_trades(c_trades, days=days)
        causal_pnl = float(causal_pack.get("TOTAL_PNL") or 0.0)
    cov = coverage_ok(pack)
    eco = economic_base_ok(pack) and causal_pnl is not None and float(causal_pnl) >= 0.0
    if not cov:
        gate = "COVERAGE_FAIL"
    elif not eco:
        gate = "ECONOMIC_FAIL"
    else:
        gate = "PASS"
    sc = corrected_score(pack, float(causal_pnl)) if gate == "PASS" and causal_pnl is not None else None
    gtable = {
        "G1_TOTAL_PNL": bool(_f(pack.get("TOTAL_PNL")) is not None and float(pack["TOTAL_PNL"]) > 0),
        "G2_PF": pack.get("PF") is not None and (pack.get("PF") == float("inf") or float(pack["PF"]) > float(MIN_PF)),
        "G3_DAY_SIGNS": int(pack.get("positive_day_n") or 0) > int(pack.get("negative_day_n") or 0),
        "G4_EX_BEST": _f(pack.get("EX_BEST_DAY_PNL")) is not None and float(pack["EX_BEST_DAY_PNL"]) > 0,
        "G5_PNL_PLUS_MAXDD": (_f(pack.get("TOTAL_PNL")) or 0.0) + (_f(pack.get("MAXDD")) or 0.0) > 0,
        "G6_CAUSAL_EX_TOP1": causal_pnl is not None and float(causal_pnl) >= 0.0,
    }
    order_n = sum(1 for r in rows if r.get("ORDERED"))
    fill_n = int(occ.get("fill_n") or 0)
    return {
        "candidate_id": cid,
        "signal_n": len(rows),
        "order_n": order_n,
        "fill_n": fill_n,
        "fill_rate": (float(fill_n) / float(order_n)) if order_n else None,
        "slot_release_n": len(trades),
        "gate": gate,
        "score": sc,
        "top_symbol": top,
        "top_symbol_pnl": top_pnl,
        "CAUSAL_EX_TOP1_PNL": causal_pnl,
        "CAUSAL_EX_TOP1_PF": causal_pack.get("PF"),
        "CAUSAL_EX_TOP1_MAXDD": causal_pack.get("MAXDD"),
        "CAUSAL_EX_TOP1_TRADE_N": causal_pack.get("TRADE_N"),
        "g_table": gtable,
        "SELECTED_STRATEGY_SYMBOL_FILTER": False,
        **pack,
        "_trades": trades,
    }


def ranking_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id"),
        "signal_n": row.get("signal_n"),
        "fill_n": row.get("fill_n"),
        "trade_n": row.get("TRADE_N"),
        "fill_rate": row.get("fill_rate"),
        "pnl": row.get("TOTAL_PNL"),
        "PF": ("inf" if row.get("PF") == float("inf") else row.get("PF")),
        "MaxDD": row.get("MAXDD"),
        "positive_days": row.get("positive_day_n"),
        "negative_days": row.get("negative_day_n"),
        "EX_BEST": row.get("EX_BEST_DAY_PNL"),
        "top_symbol": row.get("top_symbol"),
        "top_symbol_pnl": row.get("top_symbol_pnl"),
        "CAUSAL_EX_TOP1": row.get("CAUSAL_EX_TOP1_PNL"),
        "CAUSAL_EX_TOP1_PF": ("inf" if row.get("CAUSAL_EX_TOP1_PF") == float("inf") else row.get("CAUSAL_EX_TOP1_PF")),
        "gate": row.get("gate"),
        "score": row.get("score"),
        "g_table": row.get("g_table"),
    }


def rank_pass(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passed = [r for r in rows if r.get("gate") == "PASS"]
    passed.sort(
        key=lambda r: (
            -float(r.get("score") or -1e18),
            -_pf_sort_key(r.get("PF")),
            -int(r.get("TRADE_N") or 0),
            str(r.get("candidate_id") or ""),
        )
    )
    return passed


def _yen_eq(a: Any, b: Any) -> bool:
    fa = _f(a)
    fb = _f(b)
    if fa is None or fb is None:
        return False
    return abs(float(fa) - float(fb)) < 1e-6


def _pf_eq(a: Any, b: Any) -> bool:
    fa = _f(a)
    fb = _f(b)
    if fa is None or fb is None:
        return False
    return math.isclose(float(fa), float(fb), rel_tol=0.0, abs_tol=1e-12)


def canary_parity(ev: dict[str, Any]) -> dict[str, Any]:
    exp = CANARY_EXPECTED
    gates = {
        "CANARY_SIGNAL_PARITY": int(ev.get("signal_n") or 0) == int(exp["signal_n"]),
        "CANARY_FILL_PARITY": int(ev.get("fill_n") or 0) == int(exp["fill_n"]),
        "CANARY_TRADE_PARITY": int(ev.get("TRADE_N") or 0) == int(exp["trade_n"]),
        "CANARY_PNL_PARITY": _yen_eq(ev.get("TOTAL_PNL"), exp["PnL"]),
        "CANARY_PF_PARITY": _pf_eq(ev.get("PF"), exp["PF"]),
        "CANARY_DD_PARITY": _yen_eq(ev.get("MAXDD"), exp["MaxDD"]),
        "CANARY_DAY_SIGN_PARITY": int(ev.get("positive_day_n") or 0) == int(exp["positive_days"])
        and int(ev.get("negative_day_n") or 0) == int(exp["negative_days"]),
        "CANARY_EX_BEST_PARITY": _yen_eq(ev.get("EX_BEST_DAY_PNL"), exp["EX_BEST"]),
    }
    hard = (
        gates["CANARY_SIGNAL_PARITY"]
        and gates["CANARY_FILL_PARITY"]
        and gates["CANARY_TRADE_PARITY"]
        and gates["CANARY_PNL_PARITY"]
        and gates["CANARY_PF_PARITY"]
        and gates["CANARY_DD_PARITY"]
        and gates["CANARY_DAY_SIGN_PARITY"]
    )
    opt = {
        "top_symbol_match": str(ev.get("top_symbol") or "") == str(CANARY_OPTIONAL["top_symbol"]),
        "top_symbol_pnl_match": _yen_eq(ev.get("top_symbol_pnl"), CANARY_OPTIONAL["top_symbol_pnl"]),
        "causal_pnl_match": _yen_eq(ev.get("CAUSAL_EX_TOP1_PNL"), CANARY_OPTIONAL["CAUSAL_EX_TOP1"]),
        "causal_pf_match": _pf_eq(ev.get("CAUSAL_EX_TOP1_PF"), CANARY_OPTIONAL["CAUSAL_EX_TOP1_PF"]),
    }
    return {
        "CANARY_ID": CANARY_ID,
        "HARD_PASS": bool(hard),
        "LEGACY_E4_X3_CANARY": False,
        "ROW_ID": CANARY_ROW_ID,
        "gates": gates,
        "optional": opt,
        "observed": ranking_public(ev),
        "expected": dict(exp),
    }


def train_days_for_block(block_id: str) -> list[str]:
    leave = set(str(d) for d in FOLD_BLOCKS[block_id])
    return [d for d in DEVELOPMENT_DAYS if str(d) not in leave]


def test_days_for_block(block_id: str) -> list[str]:
    return [str(d) for d in FOLD_BLOCKS[block_id]]


def blocked_stability(rows_by: dict[str, list[dict[str, Any]]], winner_id: Optional[str]) -> dict[str, Any]:
    folds = []
    top3_n = 0
    test_pnls: list[float] = []
    for bid in ("B1", "B2", "B3", "B4", "B5"):
        train = train_days_for_block(bid)
        test = test_days_for_block(bid)
        ranked = []
        for cid in candidate_ids():
            rs = filter_days(list(rows_by.get(cid) or []), train)
            ranked.append(evaluate_candidate(cid, rs, days=train))
        passed = rank_pass(ranked)
        top3 = [r["candidate_id"] for r in passed[:3]]
        win = passed[0]["candidate_id"] if passed else None
        test_ev = None
        test_pnl = None
        if win:
            test_rows = filter_days(list(rows_by.get(win) or []), test)
            test_ev = evaluate_candidate(win, test_rows, days=test)
            test_pnl = float(test_ev.get("TOTAL_PNL") or 0.0)
            test_pnls.append(test_pnl)
        folds.append(
            {
                "block": bid,
                "test_days": test,
                "train_days": train,
                "winner": win,
                "top3": top3,
                "pass_n": len(passed),
                "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
                "test_pnl": test_pnl,
                "test_trade_n": None if test_ev is None else test_ev.get("TRADE_N"),
            }
        )
        if winner_id and winner_id in top3:
            top3_n += 1
        print(f"FOLD {bid} winner={win} top3={top3} pass_n={len(passed)} test_pnl={test_pnl}", flush=True)
    total_test = float(sum(test_pnls)) if test_pnls else 0.0
    pos_blocks = sum(1 for v in test_pnls if v > 1e-12)
    best = max(test_pnls) if test_pnls else 0.0
    ex_best_block = total_test - best if test_pnls else 0.0
    stability = bool(winner_id) and int(top3_n) >= 3 and float(total_test) > 0.0 and int(pos_blocks) >= 3 and float(ex_best_block) >= 0.0
    return {
        "folds": folds,
        "TRAIN_TOP3_N": top3_n,
        "FOLD_SELECTED_TEST_TOTAL_PNL": total_test,
        "POSITIVE_BLOCKS": pos_blocks,
        "EX_BEST_BLOCK_TOTAL_PNL": ex_best_block,
        "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
        "STABILITY_PASS": bool(stability),
    }


def winner_block_pnls(rows_by: dict[str, list[dict[str, Any]]], winner_id: str) -> dict[str, Any]:
    blocks = []
    for bid in ("B1", "B2", "B3", "B4", "B5"):
        days = test_days_for_block(bid)
        ev = evaluate_candidate(winner_id, filter_days(list(rows_by.get(winner_id) or []), days), days=days)
        blocks.append(
            {
                "block": bid,
                "days": days,
                "pnl": ev.get("TOTAL_PNL"),
                "trade_n": ev.get("TRADE_N"),
                "PF": ev.get("PF"),
                "symbol_filter": False,
            }
        )
    return {"blocks": blocks, "WINNER_BLOCK_SYMBOL_FILTER_N": 0}


def strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "_trades"}


def decide(
    *,
    integrity: bool,
    exec_integ: bool,
    canary_ok: bool,
    economics_opened: bool,
    coverage_pass_n: int,
    pass_n: int,
    winner_id: Optional[str],
    stability: dict[str, Any] | None,
) -> dict[str, Any]:
    base = {
        "CERTIFIED": False,
        "TRUE_OOS": False,
        "SIZING": False,
        "STRESS_OPENED": False,
        "FULL_STRATEGY_DEV_FROZEN": False,
        "SELECTED_STRATEGY_SYMBOL_FILTER": False,
        "TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY": False,
        "ECONOMICS_OPENED": bool(economics_opened),
        "LEGACY_E4_X3_CANARY": False,
    }
    if not integrity or not exec_integ:
        return {**base, "CASE": "E", "VERDICT": CASE_E, "NEXT": "STOP. DO_NOT_INTERPRET_ECONOMICS."}
    if not canary_ok:
        return {
            **base,
            "CASE": "CANARY",
            "VERDICT": CASE_CANARY,
            "NEXT": "STOP. Do not open 25-candidate economics.",
            "ECONOMICS_OPENED": False,
        }
    if int(coverage_pass_n) <= 0:
        return {**base, "CASE": "D", "VERDICT": CASE_D, "NEXT": "Coverage failed. Do not relax rules. Do not open Stress."}
    if int(pass_n) <= 0 or winner_id is None:
        return {
            **base,
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": "No robust Full Strategy. Do not add candidates. Do not open Stress.",
        }
    if not bool((stability or {}).get("STABILITY_PASS")):
        return {
            **base,
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": "SELECTION_UNSTABLE. Do not retune. Do not open Stress.",
        }
    return {
        **base,
        "CASE": "A",
        "VERDICT": CASE_A,
        "FULL_STRATEGY_DEV_FROZEN": True,
        "NEXT": "STOP. Frozen BASE identity only. No Stress this run. No top-symbol exclusion in the frozen strategy.",
    }


def frozen_strategy(winner: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "ENTRY": None if winner is None else winner.get("candidate_id"),
        "EXECUTION": "X1_IMMEDIATE_ASK",
        "EXIT": "Z3_TWO_BAR_WEAKNESS",
        "SHARES": 100,
        "CAP": 5,
        "same_symbol": True,
        "occupancy": True,
        "slot_release": True,
        "reentry": True,
        "TOP_SYMBOL_EXCLUSION": False,
        "EX_BEST_DAY_EXCLUSION": False,
        "FOLD_SPECIFIC_SYMBOL_EXCLUSION": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    from research.systematic_state_transition_library_precommit_v1.analyze import build_answers as pre_answers
    from research.systematic_state_transition_library_precommit_v1.analyze import decide as pre_decide

    pre = pre_answers(pre_decide())
    d = dict(report.get("decision") or {})
    w = dict(report.get("winner") or {})
    can = dict(report.get("canary") or {})
    stab = dict(report.get("stability") or {})
    leak = dict(report.get("leakage") or {})
    out = dict(pre)
    out["36_economics_run"] = bool(d.get("ECONOMICS_OPENED"))
    out["47_VERDICT"] = d.get("VERDICT")
    out["48_NEXT"] = d.get("NEXT")
    out["49_canary_hard_pass"] = can.get("HARD_PASS")
    out["50_25_economics_opened"] = bool(d.get("ECONOMICS_OPENED"))
    out["51_complete_ranking_table"] = list(report.get("ranking") or [])
    out["52_coverage_PASS_n"] = report.get("coverage_pass_n")
    out["53_economic_PASS_n"] = report.get("economic_pass_n")
    out["54_provisional_winner"] = w.get("candidate_id")
    out["55_winner_PnL"] = w.get("TOTAL_PNL")
    out["56_winner_PF"] = w.get("PF")
    out["57_TRAIN_TOP3_N"] = stab.get("TRAIN_TOP3_N")
    out["58_FOLD_SELECTED_TEST_TOTAL_PNL"] = stab.get("FOLD_SELECTED_TEST_TOTAL_PNL")
    out["59_STABILITY_PASS"] = stab.get("STABILITY_PASS")
    out["60_CANARY_ID"] = CANARY_ID
    out["61_legacy_E4_X3_canary_used"] = False
    out["62_canary_exact_execution_matches_current_X1"] = True
    out["63_CAUSAL_EX_TOP1_part_of_strategy"] = False
    out["64_selected_strategy_symbol_filter"] = False
    out["65_FOLD_TEST_SYMBOL_EXCLUSION_N"] = 0
    out["66_WINNER_BLOCK_SYMBOL_FILTER_N"] = 0
    out["67_final_frozen_strategy_contains_top_symbol_exclusion"] = False
    out["leakage"] = leak
    out["canary_gates"] = can.get("gates")
    out["frozen_strategy"] = report.get("frozen_strategy")
    return out
