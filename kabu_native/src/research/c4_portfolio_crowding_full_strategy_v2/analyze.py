"""Canary first, then 40-arm Full Causal ranking with matched I1/I2, S1-S6, A1-A3."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.c4_portfolio_crowding_full_strategy_v2 import (
    CANARY_EXPECTED,
    CANARY_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_E,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    FOLD_BLOCKS,
    FOLD_TRAIN_FILL_DAYS_MIN,
    FOLD_TRAIN_TRADE_N_MIN,
    FOLD_TRAIN_TRADES_PER_DAY_MIN,
    FROZEN_ELIGIBLE_IDS,
    KEPT_EXIT_IDS,
    MIN_PF,
    MIN_TOTAL_TRADES,
    MIN_TRADES_PER_DAY,
    MIN_TRADING_DAYS_WITH_FILL,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_C,
    NEXT_IF_CANARY,
    NEXT_IF_E,
    PORTFOLIO_WAIT_SEC,
    POSITION_CAP,
)
from research.c4_portfolio_crowding_full_strategy_v2.harvest import AUDIT, CANARY_ROW_ID
from research.c4_portfolio_crowding_full_strategy_v2.spec import (
    candidate_ids,
    matched_control_id,
    parse_arm_id,
    treatment_ids,
)
from research.c4_portfolio_crowding_precommit_v2 import CONTROL_POLICY_ID, TREATMENT_POLICY_ID
from research.simple_full_strategy_discovery_v1.analyze import pack_trades
from research.simple_tech_entry_family.portfolio import _sym, portfolio_replay
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS as _FOLDS

assert FOLD_BLOCKS is _FOLDS


def leakage_n() -> dict[str, int]:
    keys = (
        "HOLDOUT_BURNED_READ_N",
        "STRESS_READ_N",
        "STRESS_FILE_OPEN_N",
        "STRESS_METRIC_COMPUTE_N",
        "STRESS_REPLAY_N",
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
        "SEQ_MISSING_N",
        "SEQ_DUPLICATE_N",
        "SEQ_NON_MONOTONE_N",
        "PRIOR_Z3_GRID_READ_N",
        "PRIOR_Z3_METRIC_REUSE_N",
        "Z4_APPEARED_N",
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
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
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


def coverage_ok(p: dict[str, Any], *, fold_train: bool = False) -> bool:
    if fold_train:
        return (
            int(p.get("TRADE_N") or 0) >= int(FOLD_TRAIN_TRADE_N_MIN)
            and int(p.get("TRADING_DAY_WITH_FILL_N") or 0) >= int(FOLD_TRAIN_FILL_DAYS_MIN)
            and float(p.get("trades_per_day") or 0.0) >= float(FOLD_TRAIN_TRADES_PER_DAY_MIN)
        )
    return (
        int(p.get("TRADE_N") or 0) >= int(MIN_TOTAL_TRADES)
        and int(p.get("TRADING_DAY_WITH_FILL_N") or 0) >= int(MIN_TRADING_DAYS_WITH_FILL)
        and float(p.get("trades_per_day") or 0.0) >= float(MIN_TRADES_PER_DAY)
    )


def economic_g1_g5_ok(p: dict[str, Any]) -> bool:
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


def top_symbol_from_trades(trades: list[dict[str, Any]]) -> tuple[Optional[str], float, int]:
    by_sym: dict[str, float] = defaultdict(float)
    n_sym: dict[str, int] = defaultdict(int)
    for t in trades:
        s = _sym(t)
        by_sym[s] += float(t.get("pnl_yen_100") or 0.0)
        n_sym[s] += 1
    if not by_sym:
        return None, 0.0, 0
    top = max(by_sym.keys(), key=lambda s: by_sym[s])
    return top, float(by_sym[top]), int(n_sym[top])


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


def _trade_key(t: dict[str, Any]) -> tuple[str, str, float]:
    ft = _f(t.get("fill_time") if t.get("fill_time") is not None else t.get("fill_t")) or 0.0
    return (str(t.get("date") or ""), _sym(t), round(float(ft), 6))


def newly_admitted(base_trades: list[dict[str, Any]], causal_trades: list[dict[str, Any]]) -> tuple[int, float]:
    base_keys = {_trade_key(t) for t in base_trades}
    new = [t for t in causal_trades if _trade_key(t) not in base_keys]
    pnl = float(sum(float(t.get("pnl_yen_100") or 0.0) for t in new))
    return len(new), pnl


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


def robust_value(row: dict[str, Any]) -> Optional[float]:
    n = int(row.get("TRADE_N") or 0)
    causal = _f(row.get("CAUSAL_EX_TOP1_PNL"))
    if n <= 0 or causal is None:
        return None
    return float(
        min(
            float(row.get("TOTAL_PNL") or 0.0) / float(n),
            float(row.get("EX_BEST_DAY_PNL") or 0.0) / float(n),
            float(causal) / float(n),
        )
    )


def _hold_vals(trades: list[dict[str, Any]]) -> list[float]:
    hold_vals: list[float] = []
    for t in trades:
        h = _f(t.get("hold_sec"))
        if h is None and isinstance(t.get("src"), dict):
            h = _f(t["src"].get("hold_sec"))
        if h is not None:
            hold_vals.append(float(h))
    return hold_vals


def _daily_pnl(trades: list[dict[str, Any]], days: list[str]) -> dict[str, float]:
    by_day = {str(d): 0.0 for d in days}
    for t in trades:
        d = str(t.get("date") or "")
        if d in by_day:
            by_day[d] += float(t.get("pnl_yen_100") or 0.0)
    return by_day


def evaluate_candidate(
    cid: str,
    rows: list[dict[str, Any]],
    *,
    days: list[str],
    fold_train: bool = False,
    run_g6: str = "auto",
) -> dict[str, Any]:
    occ = replay(rows)
    trades = list(occ.get("trades") or [])
    for t in trades:
        src = t.get("src") or {}
        t["hold_sec"] = t.get("hold_sec") if t.get("hold_sec") is not None else src.get("hold_sec")
        t["mfe_yen"] = src.get("mfe_yen")
        t["mae_yen"] = src.get("mae_yen")
        t["spread0"] = src.get("spread0")
    pack = pack_trades(trades, days=days)
    holds = _hold_vals(trades)
    pack["median_hold_sec"] = float(np.median(holds)) if holds else None
    pack["daily_pnl"] = _daily_pnl(trades, days)
    pack["exit_reason_distribution"] = dict(pack.get("exit_reasons") or {})
    cov = coverage_ok(pack, fold_train=fold_train)
    g15 = economic_g1_g5_ok(pack)
    n_tr = int(pack.get("TRADE_N") or 0)
    do_g6 = (run_g6 == "always") or (run_g6 == "auto" and cov and g15) or (run_g6 == "always_if_trades" and n_tr > 0)
    if run_g6 == "attribution":
        do_g6 = n_tr > 0
    top, top_pnl, top_n = top_symbol_from_trades(trades)
    causal_pnl = None
    causal_pack: dict[str, Any] = {}
    new_n = 0
    new_pnl = 0.0
    if do_g6 and top:
        c_rows = exclude_symbol(rows, top)
        c_occ = replay(c_rows)
        c_trades = list(c_occ.get("trades") or [])
        causal_pack = pack_trades(c_trades, days=days)
        causal_pnl = float(causal_pack.get("TOTAL_PNL") or 0.0)
        new_n, new_pnl = newly_admitted(trades, c_trades)
    g6_ok = causal_pnl is not None and float(causal_pnl) >= 0.0
    if not cov:
        gate = "COVERAGE_FAIL"
    elif not g15:
        gate = "ECONOMIC_FAIL"
    elif not g6_ok:
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
        "G6_CAUSAL_EX_TOP1": g6_ok,
        "G6_RAN": bool(do_g6),
    }
    order_n = sum(1 for r in rows if r.get("ORDERED"))
    fill_n = int(occ.get("fill_n") or 0)
    if cid == CANARY_ROW_ID:
        meta = {
            "ENTRY_ID": "",
            "EXIT_ID": "Z3",
            "EXECUTION": EXECUTION_ID,
            "POLICY_ID": "",
            "ARM": "CANARY",
            "WINNER_ELIGIBLE": False,
        }
    else:
        meta = parse_arm_id(cid)
    rob = corrected_score(pack, float(causal_pnl)) if causal_pnl is not None and n_tr > 0 else None
    return {
        "candidate_id": cid,
        "ENTRY_ID": meta.get("ENTRY_ID"),
        "EXIT_ID": meta.get("EXIT_ID"),
        "EXECUTION": meta.get("EXECUTION") or EXECUTION_ID,
        "C4_POLICY": meta.get("POLICY_ID"),
        "ARM": meta.get("ARM"),
        "WINNER_ELIGIBLE": bool(meta.get("WINNER_ELIGIBLE")),
        "signal_n": len(rows),
        "order_n": order_n,
        "fill_n": fill_n,
        "fill_rate": (float(fill_n) / float(order_n)) if order_n else None,
        "CAP_REJECT_N": int(occ.get("cap_blocked") or 0),
        "SAME_SYMBOL_REJECT_N": int(occ.get("same_symbol_blocked") or 0),
        "slot_release_n": len(trades),
        "DOWNSTREAM_ADMISSION_N": new_n,
        "DOWNSTREAM_ADMISSION_PNL": new_pnl,
        "NEWLY_ADMITTED_N": new_n,
        "NEWLY_ADMITTED_PNL": new_pnl,
        "gate": gate,
        "score": sc,
        "ROBUST_VALUE": rob,
        "top_symbol": top,
        "TOP_SYMBOL_BASE_TRADE_N": top_n,
        "TOP_SYMBOL_BASE_PNL": top_pnl,
        "CAUSAL_EX_TOP1_PNL": causal_pnl,
        "CAUSAL_EX_TOP1_PF": causal_pack.get("PF"),
        "CAUSAL_EX_TOP1_MAXDD": causal_pack.get("MAXDD"),
        "CAUSAL_EX_TOP1_TRADE_N": causal_pack.get("TRADE_N"),
        "CAUSAL_EX_TOP1_POS_DAYS": causal_pack.get("positive_day_n"),
        "CAUSAL_EX_TOP1_NEG_DAYS": causal_pack.get("negative_day_n"),
        "g_table": gtable,
        "SELECTED_STRATEGY_SYMBOL_FILTER": False,
        **pack,
        "_trades": trades,
    }


def attach_incremental(treatment: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    t_pnl = _f(treatment.get("TOTAL_PNL"))
    c_pnl = _f(control.get("TOTAL_PNL"))
    i1 = t_pnl is not None and c_pnl is not None and float(t_pnl) > float(c_pnl)
    t_r = robust_value(treatment)
    c_r = robust_value(control)
    i2 = t_r is not None and c_r is not None and float(t_r) > float(c_r)
    out = dict(treatment)
    out["CONTROL_ID"] = control.get("candidate_id")
    out["CONTROL_TOTAL_PNL"] = control.get("TOTAL_PNL")
    out["CONTROL_ROBUST_VALUE"] = c_r
    out["TREATMENT_ROBUST_VALUE"] = t_r
    out["I1"] = bool(i1)
    out["I2"] = bool(i2)
    out["C4_INCREMENTAL_SUPPORT"] = bool(i1 and i2)
    if out.get("WINNER_ELIGIBLE") and out.get("gate") == "PASS" and not (i1 and i2):
        out["gate"] = "INCREMENTAL_FAIL"
        out["score"] = None
    return out


def ranking_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id"),
        "ENTRY_ID": row.get("ENTRY_ID"),
        "EXIT_ID": row.get("EXIT_ID"),
        "EXECUTION": row.get("EXECUTION"),
        "C4_POLICY": row.get("C4_POLICY"),
        "ARM": row.get("ARM"),
        "WINNER_ELIGIBLE": row.get("WINNER_ELIGIBLE"),
        "signal_n": row.get("signal_n"),
        "order_n": row.get("order_n"),
        "fill_n": row.get("fill_n"),
        "fill_rate": row.get("fill_rate"),
        "trade_n": row.get("TRADE_N"),
        "TRADING_DAY_WITH_FILL_N": row.get("TRADING_DAY_WITH_FILL_N"),
        "trades_per_day": row.get("trades_per_day"),
        "CAP_REJECT_N": row.get("CAP_REJECT_N"),
        "SAME_SYMBOL_REJECT_N": row.get("SAME_SYMBOL_REJECT_N"),
        "slot_release_n": row.get("slot_release_n"),
        "pnl": row.get("TOTAL_PNL"),
        "TOTAL_PNL": row.get("TOTAL_PNL"),
        "PF": ("inf" if row.get("PF") == float("inf") else row.get("PF")),
        "MaxDD": row.get("MAXDD"),
        "WIN_N": row.get("WIN_N"),
        "LOSS_N": row.get("LOSS_N"),
        "FLAT_N": row.get("FLAT_N"),
        "positive_days": row.get("positive_day_n"),
        "negative_days": row.get("negative_day_n"),
        "zero_day_n": row.get("zero_day_n"),
        "best_day": row.get("best_day"),
        "best_day_pnl": row.get("best_day_pnl"),
        "worst_day": row.get("worst_day"),
        "worst_day_pnl": row.get("worst_day_pnl"),
        "EX_BEST": row.get("EX_BEST_DAY_PNL"),
        "exit_reason_distribution": row.get("exit_reason_distribution") or row.get("exit_reasons"),
        "avg_hold_sec": row.get("avg_hold_sec"),
        "median_hold_sec": row.get("median_hold_sec"),
        "daily_pnl": row.get("daily_pnl"),
        "top_symbol": row.get("top_symbol"),
        "TOP_SYMBOL_BASE_TRADE_N": row.get("TOP_SYMBOL_BASE_TRADE_N"),
        "TOP_SYMBOL_BASE_PNL": row.get("TOP_SYMBOL_BASE_PNL"),
        "CAUSAL_EX_TOP1": row.get("CAUSAL_EX_TOP1_PNL"),
        "CAUSAL_EX_TOP1_PF": ("inf" if row.get("CAUSAL_EX_TOP1_PF") == float("inf") else row.get("CAUSAL_EX_TOP1_PF")),
        "CAUSAL_EX_TOP1_TRADE_N": row.get("CAUSAL_EX_TOP1_TRADE_N"),
        "CAUSAL_EX_TOP1_MAXDD": row.get("CAUSAL_EX_TOP1_MAXDD"),
        "NEWLY_ADMITTED_N": row.get("NEWLY_ADMITTED_N"),
        "NEWLY_ADMITTED_PNL": row.get("NEWLY_ADMITTED_PNL"),
        "I1": row.get("I1"),
        "I2": row.get("I2"),
        "C4_INCREMENTAL_SUPPORT": row.get("C4_INCREMENTAL_SUPPORT"),
        "CONTROL_ID": row.get("CONTROL_ID"),
        "CONTROL_TOTAL_PNL": row.get("CONTROL_TOTAL_PNL"),
        "CONTROL_ROBUST_VALUE": row.get("CONTROL_ROBUST_VALUE"),
        "TREATMENT_ROBUST_VALUE": row.get("TREATMENT_ROBUST_VALUE"),
        "gate": row.get("gate"),
        "score": row.get("score"),
        "g_table": row.get("g_table"),
    }


def rank_pass(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passed = [r for r in rows if r.get("gate") == "PASS" and r.get("WINNER_ELIGIBLE") and r.get("C4_INCREMENTAL_SUPPORT")]
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
    hard = all(bool(v) for v in gates.values())
    return {
        "CANARY_ID": CANARY_ID,
        "HARD_PASS": bool(hard),
        "ROW_ID": CANARY_ROW_ID,
        "C4_APPLIED": False,
        "gates": gates,
        "observed": ranking_public(ev),
        "expected": dict(exp),
    }


def train_days_for_block(block_id: str) -> list[str]:
    leave = set(str(d) for d in FOLD_BLOCKS[block_id])
    return [d for d in DEVELOPMENT_DAYS if str(d) not in leave]


def test_days_for_block(block_id: str) -> list[str]:
    return [str(d) for d in FOLD_BLOCKS[block_id]]


def _eval_pair(
    rows_by: dict[str, list[dict[str, Any]]],
    tid: str,
    days: list[str],
    *,
    fold_train: bool,
    run_g6: str,
) -> dict[str, Any]:
    cid_c = matched_control_id(tid)
    t_rows = filter_days(list(rows_by.get(tid) or []), days)
    c_rows = filter_days(list(rows_by.get(cid_c) or []), days)
    t_ev = evaluate_candidate(tid, t_rows, days=days, fold_train=fold_train, run_g6=run_g6)
    c_ev = evaluate_candidate(cid_c, c_rows, days=days, fold_train=fold_train, run_g6="attribution")
    return attach_incremental(t_ev, c_ev)


def blocked_stability(
    rows_by: dict[str, list[dict[str, Any]]],
    winner_id: Optional[str],
    block_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    reject_by: dict[str, dict[str, int]] = {}
    for r in block_rows:
        eid = str(r.get("ENTRY_ID") or "")
        reject_by[eid] = {b: int(r.get(f"C4_REJECT_N_{b}") or 0) for b in ("B1", "B2", "B3", "B4", "B5")}
    folds = []
    top3_n = 0
    test_pnls: list[float] = []
    selected = []
    for bid in ("B1", "B2", "B3", "B4", "B5"):
        train = train_days_for_block(bid)
        test = test_days_for_block(bid)
        ranked = []
        for tid in treatment_ids():
            ranked.append(_eval_pair(rows_by, tid, train, fold_train=True, run_g6="auto"))
        passed = rank_pass(ranked)
        top3 = [r["candidate_id"] for r in passed[:3]]
        win = passed[0]["candidate_id"] if passed else None
        test_ev = None
        test_ctrl = None
        test_pnl = None
        test_ctrl_pnl = None
        test_delta = None
        test_int_n = None
        if win:
            test_ev = evaluate_candidate(
                win,
                filter_days(list(rows_by.get(win) or []), test),
                days=test,
                fold_train=False,
                run_g6="never",
            )
            cid_c = matched_control_id(win)
            test_ctrl = evaluate_candidate(
                cid_c,
                filter_days(list(rows_by.get(cid_c) or []), test),
                days=test,
                fold_train=False,
                run_g6="never",
            )
            test_pnl = float(test_ev.get("TOTAL_PNL") or 0.0)
            test_ctrl_pnl = float(test_ctrl.get("TOTAL_PNL") or 0.0)
            test_delta = test_pnl - test_ctrl_pnl
            test_pnls.append(test_pnl)
            entry = str(parse_arm_id(win)["ENTRY_ID"])
            test_int_n = int((reject_by.get(entry) or {}).get(bid) or 0)
        folds.append(
            {
                "block": bid,
                "test_days": test,
                "train_days": train,
                "winner": win,
                "top3": top3,
                "pass_n": len(passed),
                "train_coverage_pass_n": sum(1 for r in ranked if r.get("gate") != "COVERAGE_FAIL"),
                "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
                "test_pnl": test_pnl,
                "test_trade_n": None if test_ev is None else test_ev.get("TRADE_N"),
                "test_PF": None if test_ev is None else test_ev.get("PF"),
                "test_positive_days": None if test_ev is None else test_ev.get("positive_day_n"),
                "test_negative_days": None if test_ev is None else test_ev.get("negative_day_n"),
                "TEST_TREATMENT_PNL": test_pnl,
                "TEST_CONTROL_PNL": test_ctrl_pnl,
                "TEST_C4_DELTA_PNL": test_delta,
                "TEST_C4_INTERVENTION_N": test_int_n,
                "HELD_OUT_DELTA_DIAGNOSTIC_ONLY": True,
            }
        )
        selected.append(win)
        if winner_id and winner_id in top3:
            top3_n += 1
        print(f"FOLD {bid} winner={win} top3={top3} pass_n={len(passed)} test_pnl={test_pnl}", flush=True)
    total_test = float(sum(test_pnls)) if test_pnls else 0.0
    pos_blocks = sum(1 for v in test_pnls if v > 1e-12)
    best = max(test_pnls) if test_pnls else 0.0
    ex_best_block = total_test - best if test_pnls else 0.0
    return {
        "folds": folds,
        "fold_selected_candidates": selected,
        "TRAIN_TOP3_N": top3_n,
        "FOLD_SELECTED_TEST_TOTAL_PNL": total_test,
        "FOLD_SELECTED_POSITIVE_BLOCK_N": pos_blocks,
        "FOLD_SELECTED_EX_BEST_BLOCK_PNL": ex_best_block,
        "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
        "HELD_OUT_DELTA_USED_AS_GATE": False,
    }


def winner_block_pnls(rows_by: dict[str, list[dict[str, Any]]], winner_id: str) -> dict[str, Any]:
    blocks = []
    pnls: list[float] = []
    for bid in ("B1", "B2", "B3", "B4", "B5"):
        days = test_days_for_block(bid)
        ev = evaluate_candidate(
            winner_id,
            filter_days(list(rows_by.get(winner_id) or []), days),
            days=days,
            run_g6="never",
        )
        pnl = float(ev.get("TOTAL_PNL") or 0.0)
        pnls.append(pnl)
        blocks.append(
            {
                "block": bid,
                "days": days,
                "pnl": ev.get("TOTAL_PNL"),
                "trade_n": ev.get("TRADE_N"),
                "PF": ev.get("PF"),
                "positive_days": ev.get("positive_day_n"),
                "negative_days": ev.get("negative_day_n"),
                "symbol_filter": False,
            }
        )
    pos_n = sum(1 for v in pnls if v > 1e-12)
    total = float(sum(pnls))
    best = max(pnls) if pnls else 0.0
    ex_best = total - best if pnls else 0.0
    return {
        "blocks": blocks,
        "WINNER_BLOCK_SYMBOL_FILTER_N": 0,
        "WINNER_BASE_POSITIVE_BLOCK_N": pos_n,
        "WINNER_EX_BEST_BLOCK_PNL": ex_best,
        "pnls": pnls,
    }


def stability_pass(stab: dict[str, Any], wblocks: dict[str, Any] | None) -> dict[str, Any]:
    s1 = int(stab.get("TRAIN_TOP3_N") or 0) >= 3
    s2 = int((wblocks or {}).get("WINNER_BASE_POSITIVE_BLOCK_N") or 0) >= 3
    s3 = float((wblocks or {}).get("WINNER_EX_BEST_BLOCK_PNL") or 0.0) >= 0.0
    s4 = float(stab.get("FOLD_SELECTED_TEST_TOTAL_PNL") or 0.0) > 0.0
    s5 = int(stab.get("FOLD_SELECTED_POSITIVE_BLOCK_N") or 0) >= 3
    s6 = float(stab.get("FOLD_SELECTED_EX_BEST_BLOCK_PNL") or 0.0) >= 0.0
    gates = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5, "S6": s6}
    out = dict(stab)
    out["stability_gates"] = gates
    out["STABILITY_PASS"] = all(gates.values())
    if wblocks is not None:
        out["WINNER_BASE_POSITIVE_BLOCK_N"] = wblocks.get("WINNER_BASE_POSITIVE_BLOCK_N")
        out["WINNER_EX_BEST_BLOCK_PNL"] = wblocks.get("WINNER_EX_BEST_BLOCK_PNL")
        out["WINNER_BLOCK_SYMBOL_FILTER_N"] = 0
    return out


def attribution_stability(
    rows_by: dict[str, list[dict[str, Any]]],
    winner_id: str,
    block_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    meta = parse_arm_id(winner_id)
    entry = str(meta["ENTRY_ID"])
    cid_c = matched_control_id(winner_id)
    reject = {}
    for r in block_rows:
        if str(r.get("ENTRY_ID") or "") == entry:
            reject = {b: int(r.get(f"C4_REJECT_N_{b}") or 0) for b in ("B1", "B2", "B3", "B4", "B5")}
            break
    blocks = []
    informative_deltas: list[float] = []
    pos_n = 0
    neg_n = 0
    for bid in ("B1", "B2", "B3", "B4", "B5"):
        days = test_days_for_block(bid)
        t_ev = evaluate_candidate(
            winner_id,
            filter_days(list(rows_by.get(winner_id) or []), days),
            days=days,
            run_g6="never",
        )
        c_ev = evaluate_candidate(
            cid_c,
            filter_days(list(rows_by.get(cid_c) or []), days),
            days=days,
            run_g6="never",
        )
        t_pnl = float(t_ev.get("TOTAL_PNL") or 0.0)
        c_pnl = float(c_ev.get("TOTAL_PNL") or 0.0)
        delta = t_pnl - c_pnl
        int_n = int(reject.get(bid) or 0)
        informative = int_n > 0
        cls = "C4_INFORMATIVE_BLOCK" if informative else "C4_NONINFORMATIVE_BLOCK"
        if informative:
            informative_deltas.append(delta)
            if delta > 1e-12:
                pos_n += 1
            elif delta < -1e-12:
                neg_n += 1
        blocks.append(
            {
                "block": bid,
                "C4_REJECT_N_BY_BLOCK": int_n,
                "INFORMATIVE": bool(informative),
                "CLASSIFICATION": cls,
                "TREATMENT_PNL": t_pnl,
                "CONTROL_PNL": c_pnl,
                "C4_DELTA_PNL": delta,
            }
        )
    info_n = len(informative_deltas)
    delta_total = float(sum(informative_deltas)) if informative_deltas else 0.0
    a1 = info_n >= 3
    a2 = delta_total > 0.0
    a3 = pos_n > neg_n
    return {
        "ENTRY_ID": entry,
        "TREATMENT_ID": winner_id,
        "CONTROL_ID": cid_c,
        "blocks": blocks,
        "WINNER_C4_INFORMATIVE_BLOCK_N": info_n,
        "WINNER_C4_DELTA_TOTAL_PNL": delta_total,
        "WINNER_C4_DELTA_POSITIVE_BLOCK_N": pos_n,
        "WINNER_C4_DELTA_NEGATIVE_BLOCK_N": neg_n,
        "A1": bool(a1),
        "A2": bool(a2),
        "A3": bool(a3),
        "C4_ATTRIBUTION_STABILITY_PASS": bool(a1 and a2 and a3),
        "ZERO_INTERVENTION_EXCLUDED_FROM_SIGN_TEST": True,
        "DELTA_MAGNITUDE_THRESHOLD": False,
    }


def _num_range(vals: list[Any]) -> dict[str, Any]:
    vs = []
    for v in vals:
        if v == "inf":
            vs.append(float("inf"))
            continue
        x = _f(v)
        if x is not None:
            vs.append(x)
    if not vs:
        return {"min": None, "max": None}
    return {"min": min(vs), "max": max(vs)}


def entry_exit_interaction(evaluated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in evaluated:
        if r.get("C4_POLICY") != TREATMENT_POLICY_ID:
            continue
        eid = str(r.get("ENTRY_ID") or "")
        if eid:
            by_entry[eid].append(r)
    for entry_id in FROZEN_ELIGIBLE_IDS:
        xs = by_entry.get(entry_id) or []
        exits = []
        for r in xs:
            exits.append(
                {
                    "EXIT_ID": r.get("EXIT_ID"),
                    "candidate_id": r.get("candidate_id"),
                    "PnL": r.get("TOTAL_PNL"),
                    "PF": r.get("PF"),
                    "MaxDD": r.get("MAXDD"),
                    "trade_n": r.get("TRADE_N"),
                    "I1": r.get("I1"),
                    "I2": r.get("I2"),
                    "C4_INCREMENTAL_SUPPORT": r.get("C4_INCREMENTAL_SUPPORT"),
                    "gate": r.get("gate"),
                    "g_table": r.get("g_table"),
                    "score": r.get("score"),
                }
            )
        out.append(
            {
                "ENTRY_ID": entry_id,
                "EXIT_N": len(exits),
                "exits": exits,
                "EXIT_PNL_RANGE": _num_range([r.get("TOTAL_PNL") for r in xs]),
                "EXIT6_CREATED": False,
            }
        )
    return out


def exit_entry_interaction(evaluated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    treat = [r for r in evaluated if r.get("C4_POLICY") == TREATMENT_POLICY_ID]
    for exit_id in KEPT_EXIT_IDS:
        xs = [r for r in treat if r.get("EXIT_ID") == exit_id]
        pnls = [_f(r.get("TOTAL_PNL")) for r in xs]
        pnl_vs = [x for x in pnls if x is not None]
        out.append(
            {
                "EXIT_ID": exit_id,
                "ENTRY_N": len(xs),
                "positive_pair_n": sum(1 for x in pnl_vs if x > 0),
                "coverage_pass_n": sum(1 for r in xs if r.get("gate") != "COVERAGE_FAIL"),
                "economic_pass_n": sum(1 for r in xs if r.get("gate") == "PASS"),
                "incremental_pass_n": sum(1 for r in xs if r.get("C4_INCREMENTAL_SUPPORT")),
            }
        )
    return out


def strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "_trades"}


def decide(
    *,
    parity_ok: bool,
    integrity: bool,
    exec_integ: bool,
    precommit_ok: bool,
    stream_ok: bool,
    canary_ok: bool,
    economics_opened: bool,
    arm_economics_run: bool,
    coverage_pass_n: int,
    pass_n: int,
    winner_id: Optional[str],
    stability: dict[str, Any] | None,
    attribution: dict[str, Any] | None,
    pair_rerun_n: int = 40,
    prior_z3_reuse_n: int = 0,
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
        "ARM_ECONOMICS_RUN": bool(arm_economics_run),
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "NEW_EXIT_CREATED": False,
        "EXIT6_CREATED": False,
        "EXIT_PARAMETER_RETUNE": False,
        "ENTRY_PARAMETER_RETUNE": False,
        "NEW_C4_POLICY": False,
        "NEW_THRESHOLD": False,
        "PAIR_RERUN_N": int(pair_rerun_n),
        "PRIOR_Z3_METRIC_REUSE_N": int(prior_z3_reuse_n),
        "ENTRY_RAW_STREAM_INVARIANCE_PASS": bool(stream_ok),
        "FOLD_LOCAL_ELIGIBILITY_PARITY_PASS": bool(parity_ok),
        "C4_ATTRIBUTION_STABILITY_PASS": None if attribution is None else bool(attribution.get("C4_ATTRIBUTION_STABILITY_PASS")),
    }
    if not parity_ok:
        return {
            **base,
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": NEXT_IF_E,
            "ECONOMICS_OPENED": False,
            "ARM_ECONOMICS_RUN": False,
        }
    if (
        not integrity
        or not exec_integ
        or not precommit_ok
        or not stream_ok
        or int(prior_z3_reuse_n) != 0
        or int(pair_rerun_n) != 40
    ):
        return {
            **base,
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": NEXT_IF_E,
            "ECONOMICS_OPENED": False,
            "ARM_ECONOMICS_RUN": False,
        }
    if not canary_ok:
        return {
            **base,
            "CASE": "CANARY",
            "VERDICT": CASE_CANARY,
            "NEXT": NEXT_IF_CANARY,
            "ECONOMICS_OPENED": False,
            "ARM_ECONOMICS_RUN": False,
        }
    if int(pass_n) <= 0 or winner_id is None:
        return {
            **base,
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": NEXT_IF_B,
        }
    if not bool((stability or {}).get("STABILITY_PASS")) or not bool((attribution or {}).get("C4_ATTRIBUTION_STABILITY_PASS")):
        return {
            **base,
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": NEXT_IF_C,
        }
    return {
        **base,
        "CASE": "A",
        "VERDICT": CASE_A,
        "FULL_STRATEGY_DEV_FROZEN": True,
        "NEXT": NEXT_IF_A,
    }


def frozen_strategy(winner: dict[str, Any] | None) -> dict[str, Any]:
    cid = None if winner is None else winner.get("candidate_id")
    meta = parse_arm_id(str(cid)) if cid else {}
    return {
        "ARM": cid,
        "ENTRY": meta.get("ENTRY_ID"),
        "C4_POLICY": meta.get("POLICY_ID") or TREATMENT_POLICY_ID,
        "EXECUTION": EXECUTION_ID,
        "EXIT": meta.get("EXIT_ID"),
        "SHARES": 100,
        "CAP": 5,
        "same_symbol": True,
        "occupancy": True,
        "slot_release": True,
        "reentry": True,
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "TOP_SYMBOL_EXCLUSION": False,
        "EX_BEST_DAY_EXCLUSION": False,
        "FOLD_SPECIFIC_SYMBOL_EXCLUSION": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    w = dict(report.get("winner") or {})
    can = dict(report.get("canary") or {})
    stab = dict(report.get("stability") or {})
    attr = dict(report.get("attribution") or {})
    pin = dict(report.get("prior_integrity") or {})
    parity = dict(report.get("fold_local_eligibility") or {})
    ranking = list(report.get("ranking") or [])
    stream = dict(report.get("raw_entry_stream_invariance") or {})
    treat = [r for r in ranking if r.get("C4_POLICY") == TREATMENT_POLICY_ID]
    return {
        "ANALYSIS_ID": report.get("ANALYSIS_ID"),
        "PRECOMMIT_ANALYSIS_ID": pin.get("ANALYSIS_ID"),
        "PRECOMMIT_VERDICT": pin.get("VERDICT"),
        "FOLD_LOCAL_ELIGIBILITY_PARITY_PASS": parity.get("FOLD_LOCAL_ELIGIBILITY_PARITY_PASS"),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B1": parity.get("FOLD_LOCAL_ELIGIBLE_ENTRY_N_B1"),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B2": parity.get("FOLD_LOCAL_ELIGIBLE_ENTRY_N_B2"),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B3": parity.get("FOLD_LOCAL_ELIGIBLE_ENTRY_N_B3"),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B4": parity.get("FOLD_LOCAL_ELIGIBLE_ENTRY_N_B4"),
        "FOLD_LOCAL_ELIGIBLE_ENTRY_N_B5": parity.get("FOLD_LOCAL_ELIGIBLE_ENTRY_N_B5"),
        "ANY_HANDOFF_TRAIN_ELIGIBLE": parity.get("ANY_HANDOFF_TRAIN_ELIGIBLE"),
        "CANARY_HARD_PASS": can.get("HARD_PASS"),
        "ARM_RERUN_N": report.get("PAIR_RERUN_N"),
        "PRIOR_Z3_METRIC_REUSE_N": report.get("PRIOR_Z3_METRIC_REUSE_N"),
        "ENTRY_RAW_STREAM_INVARIANCE_PASS": stream.get("ENTRY_RAW_STREAM_INVARIANCE_PASS"),
        "COVERAGE_PASS_N": report.get("coverage_pass_n"),
        "TREATMENT_ABSOLUTE_PASS_N": sum(1 for r in treat if r.get("gate") == "PASS"),
        "C4_INCREMENTAL_SUPPORT_N": sum(1 for r in treat if r.get("C4_INCREMENTAL_SUPPORT")),
        "WINNER": w.get("candidate_id"),
        "WINNER_ENTRY": w.get("ENTRY_ID"),
        "WINNER_EXIT": w.get("EXIT_ID"),
        "STABILITY_PASS": stab.get("STABILITY_PASS"),
        "C4_ATTRIBUTION_STABILITY_PASS": attr.get("C4_ATTRIBUTION_STABILITY_PASS"),
        "ARM_ECONOMICS_RUN": d.get("ARM_ECONOMICS_RUN"),
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "NEW_C4_POLICY": False,
        "NEW_THRESHOLD": False,
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "SIZING": False,
        "STRESS_OPENED": False,
        "SUBMIT_CANCEL_LIVE": "0/0/0",
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
