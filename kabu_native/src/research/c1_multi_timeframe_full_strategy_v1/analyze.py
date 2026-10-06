"""Canary parity then Full Causal 10-candidate ranking. G6 diagnostic only. 5 two-day folds."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

from research.c1_multi_timeframe_full_strategy_v1 import (
    CANARY_EXPECTED,
    CANARY_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_D,
    CASE_E,
    DEVELOPMENT_DAYS,
    FOLD_BLOCKS,
    FOLD_TRAIN_FILL_DAYS_MIN,
    FOLD_TRAIN_TRADE_N_MIN,
    FOLD_TRAIN_TRADES_PER_DAY_MIN,
    MIN_PF,
    MIN_TOTAL_TRADES,
    MIN_TRADES_PER_DAY,
    MIN_TRADING_DAYS_WITH_FILL,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_C,
    NEXT_IF_D,
    NON_VWAP_CANDIDATE_IDS,
    PORTFOLIO_WAIT_SEC,
    POSITION_CAP,
    VWAP_CANDIDATE_IDS,
)
from research.c1_multi_timeframe_full_strategy_v1.harvest import AUDIT, CANARY_ROW_ID
from research.c1_multi_timeframe_full_strategy_v1.spec import candidate_ids, parse_candidate_id
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library
from research.simple_full_strategy_discovery_v1.analyze import pack_trades
from research.simple_tech_entry_family.portfolio import _sym, portfolio_replay


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
        "PARTIAL_HTF_BAR_USED_N",
        "FUTURE_HTF_BAR_N",
        "SAME_BUCKET_CARRYBACK_N",
        "CROSS_SESSION_HTF_BAR_N",
        "FUTURE_SESSION_VWAP_CARRYBACK_N",
        "V7_SAME_BUCKET_JOIN_USED",
        "HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME_N",
        "GLOBAL_VWAP_ENTRY_GATE_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def htf_integrity() -> dict[str, Any]:
    leak = leakage_n()
    return {
        "PARTIAL_HTF_BAR_USED_N": leak["PARTIAL_HTF_BAR_USED_N"],
        "FUTURE_HTF_BAR_N": leak["FUTURE_HTF_BAR_N"],
        "SAME_BUCKET_CARRYBACK_N": leak["SAME_BUCKET_CARRYBACK_N"],
        "CROSS_SESSION_HTF_BAR_N": leak["CROSS_SESSION_HTF_BAR_N"],
        "FUTURE_SESSION_VWAP_CARRYBACK_N": leak["FUTURE_SESSION_VWAP_CARRYBACK_N"],
        "V7_SAME_BUCKET_JOIN_USED": False,
        "HTF_STATE_PUBLISHED_BEFORE_SIGNAL_EVAL": True,
        "HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME": False,
        "HTF_VWAP_USES_CANONICAL_SESSION_VWAP": True,
        "ok": (
            leak["PARTIAL_HTF_BAR_USED_N"] == 0
            and leak["FUTURE_HTF_BAR_N"] == 0
            and leak["SAME_BUCKET_CARRYBACK_N"] == 0
            and leak["CROSS_SESSION_HTF_BAR_N"] == 0
            and leak["FUTURE_SESSION_VWAP_CARRYBACK_N"] == 0
            and leak["V7_SAME_BUCKET_JOIN_USED"] == 0
            and leak["HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME_N"] == 0
        ),
    }


def integrity_ok() -> bool:
    return all(int(v) == 0 for v in leakage_n().values()) and bool(htf_integrity().get("ok"))


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


def _family_of(cid: str) -> str:
    by = {str(r["CANDIDATE_ID"]): str(r["FAMILY"]) for r in build_raw_library()}
    return by.get(cid, "")


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
    cov = coverage_ok(pack, fold_train=fold_train)
    g15 = economic_g1_g5_ok(pack)
    do_g6 = (run_g6 == "always") or (run_g6 == "auto" and cov and g15)
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
    meta = parse_candidate_id(cid) if cid != CANARY_ROW_ID else {"HTF_ID": "", "STATE_ID": "", "VWAP_ENTRY_GATE": False}
    return {
        "candidate_id": cid,
        "FAMILY": "" if cid == CANARY_ROW_ID else _family_of(cid),
        "HTF_ID": meta.get("HTF_ID"),
        "STATE_ID": meta.get("STATE_ID"),
        "VWAP_ENTRY_GATE": bool(meta.get("VWAP_ENTRY_GATE")),
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


def ranking_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id"),
        "FAMILY": row.get("FAMILY"),
        "HTF_ID": row.get("HTF_ID"),
        "STATE_ID": row.get("STATE_ID"),
        "VWAP_ENTRY_GATE": row.get("VWAP_ENTRY_GATE"),
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
        "exit_reasons": row.get("exit_reasons"),
        "avg_hold_sec": row.get("avg_hold_sec"),
        "DOWNSTREAM_ADMISSION_N": row.get("DOWNSTREAM_ADMISSION_N"),
        "DOWNSTREAM_ADMISSION_PNL": row.get("DOWNSTREAM_ADMISSION_PNL"),
        "top_symbol": row.get("top_symbol"),
        "TOP_SYMBOL_BASE_TRADE_N": row.get("TOP_SYMBOL_BASE_TRADE_N"),
        "TOP_SYMBOL_BASE_PNL": row.get("TOP_SYMBOL_BASE_PNL"),
        "CAUSAL_EX_TOP1": row.get("CAUSAL_EX_TOP1_PNL"),
        "CAUSAL_EX_TOP1_PF": ("inf" if row.get("CAUSAL_EX_TOP1_PF") == float("inf") else row.get("CAUSAL_EX_TOP1_PF")),
        "CAUSAL_EX_TOP1_TRADE_N": row.get("CAUSAL_EX_TOP1_TRADE_N"),
        "CAUSAL_EX_TOP1_MAXDD": row.get("CAUSAL_EX_TOP1_MAXDD"),
        "CAUSAL_EX_TOP1_POS_DAYS": row.get("CAUSAL_EX_TOP1_POS_DAYS"),
        "CAUSAL_EX_TOP1_NEG_DAYS": row.get("CAUSAL_EX_TOP1_NEG_DAYS"),
        "NEWLY_ADMITTED_N": row.get("NEWLY_ADMITTED_N"),
        "NEWLY_ADMITTED_PNL": row.get("NEWLY_ADMITTED_PNL"),
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
    hard = all(bool(v) for v in gates.values())
    return {
        "CANARY_ID": CANARY_ID,
        "HARD_PASS": bool(hard),
        "ROW_ID": CANARY_ROW_ID,
        "gates": gates,
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
    selected = []
    for bid in ("B1", "B2", "B3", "B4", "B5"):
        train = train_days_for_block(bid)
        test = test_days_for_block(bid)
        ranked = []
        for cid in candidate_ids():
            rs = filter_days(list(rows_by.get(cid) or []), train)
            ranked.append(evaluate_candidate(cid, rs, days=train, fold_train=True))
        passed = rank_pass(ranked)
        top3 = [r["candidate_id"] for r in passed[:3]]
        win = passed[0]["candidate_id"] if passed else None
        test_ev = None
        test_pnl = None
        if win:
            test_rows = filter_days(list(rows_by.get(win) or []), test)
            test_ev = evaluate_candidate(win, test_rows, days=test, fold_train=False, run_g6="never")
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
                "train_coverage_pass_n": sum(1 for r in ranked if r.get("gate") != "COVERAGE_FAIL"),
                "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
                "test_pnl": test_pnl,
                "test_trade_n": None if test_ev is None else test_ev.get("TRADE_N"),
                "test_PF": None if test_ev is None else test_ev.get("PF"),
                "test_positive_days": None if test_ev is None else test_ev.get("positive_day_n"),
                "test_negative_days": None if test_ev is None else test_ev.get("negative_day_n"),
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


def strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "_trades"}


def decide(
    *,
    integrity: bool,
    exec_integ: bool,
    htf_ok: bool,
    precommit_ok: bool,
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
        "C4_REMAINS_ELIGIBLE": True,
        "C1_CLASS_CLOSED": False,
    }
    if not integrity or not exec_integ or not htf_ok or not precommit_ok:
        return {
            **base,
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": "STOP. Do not interpret economics. Do not advance to C4 until integrity is resolved.",
            "ECONOMICS_OPENED": False,
        }
    if not canary_ok:
        return {
            **base,
            "CASE": "CANARY",
            "VERDICT": CASE_CANARY,
            "NEXT": "STOP. Do not open 10-candidate economics.",
            "ECONOMICS_OPENED": False,
        }
    if int(coverage_pass_n) <= 0:
        return {
            **base,
            "CASE": "D",
            "VERDICT": CASE_D,
            "NEXT": NEXT_IF_D,
            "C1_CLASS_CLOSED": True,
        }
    if int(pass_n) <= 0 or winner_id is None:
        return {
            **base,
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": NEXT_IF_B,
            "C1_CLASS_CLOSED": True,
        }
    if not bool((stability or {}).get("STABILITY_PASS")):
        return {
            **base,
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": NEXT_IF_C,
            "C1_CLASS_CLOSED": True,
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
    meta = parse_candidate_id(str(cid)) if cid else {}
    return {
        "ENTRY": cid,
        "FAMILY": None if winner is None else winner.get("FAMILY"),
        "HTF_ID": meta.get("HTF_ID"),
        "STATE_ID": meta.get("STATE_ID"),
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
        "VWAP_UNLESS_WINNER_IS_VWAP_FAMILY": bool(cid in VWAP_CANDIDATE_IDS) if cid else False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    w = dict(report.get("winner") or {})
    can = dict(report.get("canary") or {})
    stab = dict(report.get("stability") or {})
    pin = dict(report.get("precommit_integrity") or {})
    htf = dict(report.get("htf_integrity") or {})
    wblocks = dict(report.get("winner_blocks") or {})
    ranking = list(report.get("ranking") or [])
    cov_fail = [r["candidate_id"] for r in ranking if r.get("gate") == "COVERAGE_FAIL"]
    eco_pass = [r["candidate_id"] for r in ranking if r.get("gate") == "PASS"]
    vwap_pass = sum(1 for r in ranking if r.get("gate") == "PASS" and r.get("candidate_id") in VWAP_CANDIDATE_IDS)
    non_vwap_pass = sum(1 for r in ranking if r.get("gate") == "PASS" and r.get("candidate_id") in NON_VWAP_CANDIDATE_IDS)
    return {
        "1_precommit_verdict": pin.get("VERDICT"),
        "2_precommit_hashes_all_match": bool(pin.get("hashes_match")),
        "3_candidate_n": 10,
        "4_VWAP_candidate_n": 2,
        "5_non_VWAP_candidate_n": 8,
        "6_global_VWAP_gate": False,
        "7_canary_id": CANARY_ID,
        "8_canary_hard_pass": can.get("HARD_PASS"),
        "9_HTF_integrity_pass": bool(htf.get("ok")),
        "10_PARTIAL_HTF_BAR_USED_N": htf.get("PARTIAL_HTF_BAR_USED_N"),
        "11_FUTURE_HTF_BAR_N": htf.get("FUTURE_HTF_BAR_N"),
        "12_SAME_BUCKET_CARRYBACK_N": htf.get("SAME_BUCKET_CARRYBACK_N"),
        "13_CROSS_SESSION_HTF_BAR_N": htf.get("CROSS_SESSION_HTF_BAR_N"),
        "14_FUTURE_SESSION_VWAP_CARRYBACK_N": htf.get("FUTURE_SESSION_VWAP_CARRYBACK_N"),
        "15_candidate_economics_opened": bool(d.get("ECONOMICS_OPENED")),
        "16_coverage_PASS_n": report.get("coverage_pass_n"),
        "17_coverage_FAIL_ids": cov_fail,
        "18_complete_10_candidate_ranking": ranking,
        "19_economic_PASS_n": report.get("economic_pass_n"),
        "20_economic_PASS_ids": eco_pass,
        "21_provisional_winner": w.get("candidate_id"),
        "22_winner_family": w.get("FAMILY"),
        "23_winner_HTF": w.get("HTF_ID"),
        "24_winner_trade_n": w.get("TRADE_N"),
        "25_winner_PnL": w.get("TOTAL_PNL"),
        "26_winner_PF": w.get("PF"),
        "27_winner_MaxDD": w.get("MAXDD"),
        "28_winner_positive_negative_days": {
            "positive": w.get("positive_day_n"),
            "negative": w.get("negative_day_n"),
        },
        "29_winner_EX_BEST": w.get("EX_BEST_DAY_PNL"),
        "30_winner_top_symbol": w.get("top_symbol"),
        "31_winner_causal_ex_top_PnL_PF": {
            "PnL": w.get("CAUSAL_EX_TOP1_PNL"),
            "PF": w.get("CAUSAL_EX_TOP1_PF"),
        },
        "32_newly_admitted_N_PnL": {
            "N": w.get("NEWLY_ADMITTED_N"),
            "PnL": w.get("NEWLY_ADMITTED_PNL"),
        },
        "33_robust_score": w.get("score"),
        "34_VWAP_candidate_PASS_n": vwap_pass,
        "35_non_VWAP_candidate_PASS_n": non_vwap_pass,
        "36_TRAIN_TOP3_N": stab.get("TRAIN_TOP3_N"),
        "37_winner_block_PnLs": wblocks.get("pnls"),
        "38_winner_positive_block_n": wblocks.get("WINNER_BASE_POSITIVE_BLOCK_N"),
        "39_winner_EX_BEST_BLOCK_PNL": wblocks.get("WINNER_EX_BEST_BLOCK_PNL"),
        "40_fold_selected_candidates": stab.get("fold_selected_candidates"),
        "41_fold_selected_test_total_PnL": stab.get("FOLD_SELECTED_TEST_TOTAL_PNL"),
        "42_fold_selected_positive_block_n": stab.get("FOLD_SELECTED_POSITIVE_BLOCK_N"),
        "43_fold_selected_EX_BEST_BLOCK_PNL": stab.get("FOLD_SELECTED_EX_BEST_BLOCK_PNL"),
        "44_STABILITY_PASS": stab.get("STABILITY_PASS"),
        "45_winner_frozen": bool(d.get("FULL_STRATEGY_DEV_FROZEN")),
        "46_top_symbol_exclusion_frozen": False,
        "47_Stress_opened": False,
        "48_burned_Holdout_read": False,
        "49_Stress_read": False,
        "50_future_used": False,
        "51_Sizing_ran": False,
        "52_C4_remains_eligible": True,
        "53_Runtime_changed": False,
        "54_submit_cancel_live": "0/0/0",
        "55_TRUE_OOS": False,
        "56_CERTIFIED": False,
        "57_VERDICT": d.get("VERDICT"),
        "58_NEXT": d.get("NEXT"),
    }
