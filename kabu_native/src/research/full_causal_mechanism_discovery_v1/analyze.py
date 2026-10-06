"""Canary parity, Full Causal economics, G6, blocks, five-fold selection transfer."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

from research.full_causal_mechanism_discovery_v1 import (
    CANARY_EXPECTED,
    CANARY_OPTIONAL,
    CANARY_ROW_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_INTEGRITY,
    CASE_PARITY,
    DEVELOPMENT_DAYS,
    FOLD_BLOCKS,
    MIN_PF,
    MIN_TOTAL_TRADES,
    MIN_TRADES_PER_DAY,
    MIN_TRADING_DAYS_WITH_FILL,
    NEXT_A,
    NEXT_B,
    NEXT_FIX,
    OPERATOR_RANK,
    PORTFOLIO_WAIT_SEC,
    POSITION_CAP,
)
from research.full_causal_mechanism_discovery_v1.candidates import freeze_candidate_set, uses_breadth
from research.full_causal_mechanism_discovery_v1.mapping import O1_EXIT_DEFINITION, O2_EXIT_DEFINITION, O3_EXIT_DEFINITION
from research.simple_full_strategy_discovery_v1.analyze import pack_trades
from research.simple_tech_entry_family.portfolio import _sym, portfolio_replay


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


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


def exclude_symbol(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    target = str(symbol).replace(".T", "")
    return [r for r in rows if _sym(r) != target]


def top_symbol_from_trades(trades: list[dict[str, Any]]) -> tuple[Optional[str], float]:
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_sym[_sym(t)] += float(t.get("pnl_yen_100") or 0.0)
    if not by_sym:
        return None, 0.0
    top = max(by_sym.keys(), key=lambda s: by_sym[s])
    return top, float(by_sym[top])


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


def _pf_sort_key(pf: Any) -> float:
    if pf is None:
        return -1e18
    if pf == float("inf"):
        return 1e18
    return float(pf)


def reentry_n(trades: list[dict[str, Any]]) -> int:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by[(str(t.get("date") or ""), _sym(t))].append(t)
    n = 0
    for xs in by.values():
        xs.sort(key=lambda r: float(_f(r.get("fill_time") or r.get("fill_t")) or 0.0))
        n += max(0, len(xs) - 1)
    return n


def coverage_ok(p: dict[str, Any], session_exit_unfilled_n: int) -> bool:
    return (
        int(p.get("TRADE_N") or 0) >= int(MIN_TOTAL_TRADES)
        and int(p.get("TRADING_DAY_WITH_FILL_N") or 0) >= int(MIN_TRADING_DAYS_WITH_FILL)
        and float(p.get("trades_per_day") or 0.0) >= float(MIN_TRADES_PER_DAY)
        and int(session_exit_unfilled_n) == 0
    )


def g1_g5(p: dict[str, Any]) -> dict[str, bool]:
    total = _f(p.get("TOTAL_PNL"))
    pf = p.get("PF")
    pf_ok = pf is not None and (pf == float("inf") or float(pf) > float(MIN_PF))
    dd = _f(p.get("MAXDD")) or 0.0
    return {
        "G1": bool(total is not None and float(total) > 0.0),
        "G2": bool(pf_ok),
        "G3": int(p.get("positive_day_n") or 0) > int(p.get("negative_day_n") or 0),
        "G4": _f(p.get("EX_BEST_DAY_PNL")) is not None and float(p["EX_BEST_DAY_PNL"]) > 0.0,
        "G5": (float(total or 0.0) + float(dd)) > 0.0,
    }


def train_eligible(p: dict[str, Any]) -> bool:
    g = g1_g5(p)
    return int(p.get("TRADE_N") or 0) >= 1 and g["G1"] and g["G2"] and g["G3"] and g["G4"]


def block_days(block_id: str) -> list[str]:
    return [str(d) for d in FOLD_BLOCKS[block_id]]


def train_days_for_block(block_id: str) -> list[str]:
    leave = set(block_days(block_id))
    return [d for d in DEVELOPMENT_DAYS if str(d) not in leave]


def test_days_for_block(block_id: str) -> list[str]:
    return block_days(block_id)


def block_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = []
    pnls = []
    for bid in ("B1", "B2", "B3", "B4", "B5"):
        days = test_days_for_block(bid)
        occ = replay(filter_days(rows, days))
        pack = pack_trades(list(occ.get("trades") or []), days=days)
        pnl = float(pack.get("TOTAL_PNL") or 0.0)
        pnls.append(pnl)
        blocks.append({"block": bid, "days": days, "pnl": pnl, "trade_n": pack.get("TRADE_N"), "PF": pack.get("PF")})
    pos_n = sum(1 for v in pnls if v > 1e-12)
    best = max(pnls) if pnls else 0.0
    remaining = float(sum(pnls) - best) if pnls else 0.0
    return {
        "blocks": blocks,
        "POSITIVE_BLOCK_N": int(pos_n),
        "EX_BEST_BLOCK_PNL": remaining,
        "S1": int(pos_n) >= 3,
        "S2": remaining >= 0.0,
    }


def session_unfilled_n(rows: list[dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        if not r.get("WOULD_FILL"):
            continue
        if _f(r.get("exit_t")) is None or str(r.get("exit_reason") or "") == "EXIT_MISS":
            n += 1
    return n


def evaluate_strategy(
    cid: str,
    rows: list[dict[str, Any]],
    *,
    days: list[str],
    meta: dict[str, Any] | None = None,
    g6_rows: list[dict[str, Any]] | None = None,
    compute_g6: bool = True,
    compute_blocks: bool = True,
) -> dict[str, Any]:
    occ = replay(rows)
    trades = list(occ.get("trades") or [])
    for t in trades:
        src = t.get("src") or {}
        t["hold_sec"] = t.get("hold_sec") if t.get("hold_sec") is not None else src.get("hold_sec")
        t["mfe_yen"] = src.get("mfe_yen")
        t["mae_yen"] = src.get("mae_yen")
        t["spread0"] = src.get("spread0")
        t["exit_reason"] = t.get("exit_reason") or src.get("exit_reason")
    pack = pack_trades(trades, days=days)
    top, top_pnl = top_symbol_from_trades(trades)
    unfilled = session_unfilled_n(rows)
    reasons = pack.get("exit_reasons") or {}
    fill_n = int(occ.get("fill_n") or 0)
    x1_fill_n = sum(1 for r in rows if r.get("WOULD_FILL"))
    signal_n = len(rows)
    causal_pnl = None
    causal_pack: dict[str, Any] = {}
    if compute_g6 and top:
        src_rows = g6_rows if g6_rows is not None else exclude_symbol(rows, top)
        c_occ = replay(src_rows)
        causal_pack = pack_trades(list(c_occ.get("trades") or []), days=days)
        causal_pnl = float(causal_pack.get("TOTAL_PNL") or 0.0)
    gtab = g1_g5(pack)
    gtab["G6"] = causal_pnl is not None and float(causal_pnl) >= 0.0
    cov = coverage_ok(pack, unfilled)
    blocks = block_pack(rows) if compute_blocks else {}
    s1 = bool(blocks.get("S1")) if blocks else False
    s2 = bool(blocks.get("S2")) if blocks else False
    base_q = bool(cov and all(gtab[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6")) and s1 and s2)
    meta = meta or {}
    daily = {d: 0.0 for d in days}
    for t in trades:
        d = str(t.get("date") or "")
        if d in daily:
            daily[d] += float(t.get("pnl_yen_100") or 0.0)
    return {
        "STRATEGY_ID": cid,
        "MECHANISM_ID": meta.get("MECHANISM_ID"),
        "OPERATOR": meta.get("OPERATOR"),
        "SELECTABLE": bool(meta.get("SELECTABLE", True)),
        "EXACT_CLOSED_ENTRY_IDENTITY": bool(meta.get("EXACT_CLOSED_ENTRY_IDENTITY")),
        "CONTROL_OR_CLOSED_REFERENCE": bool(meta.get("CONTROL_OR_CLOSED_REFERENCE")),
        "USES_BREADTH": uses_breadth(meta) if meta else False,
        "signal_n": signal_n,
        "X1_fill_n": int(x1_fill_n),
        "trade_n": pack.get("TRADE_N"),
        "fill_day_n": pack.get("TRADING_DAY_WITH_FILL_N"),
        "trades_per_day": pack.get("trades_per_day"),
        "entry_no_fill_n": int(signal_n - x1_fill_n),
        "CAP_reject_n": int(occ.get("cap_blocked") or 0),
        "same_symbol_reject_n": int(occ.get("same_symbol_blocked") or 0),
        "technical_exit_n": int(sum(int(reasons.get(k) or 0) for k in reasons if str(k) not in ("SESSION_CLOSE", "EXIT_MISS", ""))),
        "session_exit_n": int(reasons.get("SESSION_CLOSE") or 0),
        "session_exit_unfilled_n": int(unfilled),
        "slot_release_n": int(len(trades)),
        "reentry_n": int(reentry_n(trades)),
        "TOTAL_PNL": pack.get("TOTAL_PNL"),
        "PF": pack.get("PF"),
        "MaxDD": pack.get("MAXDD"),
        "positive_day_n": pack.get("positive_day_n"),
        "negative_day_n": pack.get("negative_day_n"),
        "zero_day_n": pack.get("zero_day_n"),
        "best_day": pack.get("best_day"),
        "best_day_pnl": pack.get("best_day_pnl"),
        "worst_day": pack.get("worst_day"),
        "worst_day_pnl": pack.get("worst_day_pnl"),
        "EX_BEST_DAY_PNL": pack.get("EX_BEST_DAY_PNL"),
        "top_symbol": top,
        "top_symbol_pnl": top_pnl,
        "CAUSAL_EX_TOP1_PNL": causal_pnl,
        "CAUSAL_EX_TOP1_PF": causal_pack.get("PF"),
        "CAUSAL_EX_TOP1_MAXDD": causal_pack.get("MAXDD"),
        "CAUSAL_EX_TOP1_TRADE_N": causal_pack.get("TRADE_N"),
        "g_table": gtab,
        "coverage_ok": cov,
        "C1": int(pack.get("TRADE_N") or 0) >= int(MIN_TOTAL_TRADES),
        "C2": int(pack.get("TRADING_DAY_WITH_FILL_N") or 0) >= int(MIN_TRADING_DAYS_WITH_FILL),
        "C3": float(pack.get("trades_per_day") or 0.0) >= float(MIN_TRADES_PER_DAY),
        "C4": int(unfilled) == 0,
        "blocks": blocks,
        "BASE_QUALIFIED": base_q,
        "fill_n": fill_n,
        **{k: pack[k] for k in pack if k not in {"exit_reasons"}},
        "exit_reasons": reasons,
        "_trades": trades,
        "daily": daily,
    }


def canary_parity(ev: dict[str, Any]) -> dict[str, Any]:
    exp = CANARY_EXPECTED
    gates = {
        "CANARY_SIGNAL_PARITY": int(ev.get("signal_n") or 0) == int(exp["signal_n"]),
        "CANARY_FILL_PARITY": int(ev.get("fill_n") or 0) == int(exp["fill_n"]),
        "CANARY_TRADE_PARITY": int(ev.get("trade_n") or ev.get("TRADE_N") or 0) == int(exp["trade_n"]),
        "CANARY_PNL_PARITY": _yen_eq(ev.get("TOTAL_PNL"), exp["PnL"]),
        "CANARY_PF_PARITY": _pf_eq(ev.get("PF"), exp["PF"]),
        "CANARY_DD_PARITY": _yen_eq(ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD"), exp["MaxDD"]),
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
        "CANARY_ID": CANARY_ROW_ID,
        "HARD_PASS": bool(hard),
        "gates": gates,
        "optional": opt,
        "observed": {
            "signal_n": ev.get("signal_n"),
            "fill_n": ev.get("fill_n"),
            "trade_n": ev.get("trade_n") or ev.get("TRADE_N"),
            "TOTAL_PNL": ev.get("TOTAL_PNL"),
            "PF": ev.get("PF"),
            "MaxDD": ev.get("MaxDD") if ev.get("MaxDD") is not None else ev.get("MAXDD"),
        },
        "expected": dict(exp),
    }


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    skip = {"_trades", "daily"}
    out = {}
    for k, v in row.items():
        if k in skip:
            continue
        if k == "PF" and v == float("inf"):
            out[k] = "inf"
        elif k == "CAUSAL_EX_TOP1_PF" and v == float("inf"):
            out[k] = "inf"
        else:
            out[k] = v
    return out


def apply_g6(
    ev: dict[str, Any],
    rows: list[dict[str, Any]],
    days: list[str],
    g6_cache: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    top = ev.get("top_symbol")
    if not top:
        ev["g_table"]["G6"] = False
        ev["CAUSAL_EX_TOP1_PNL"] = None
        ev["BASE_QUALIFIED"] = False
        return ev
    if ev.get("USES_BREADTH"):
        from research.full_causal_mechanism_discovery_v1.harvest import harvest_g6_universe

        key = _sym({"symbol": top})
        if key not in g6_cache:
            har = harvest_g6_universe(key)
            if not har.get("ok"):
                ev["g_table"]["G6"] = False
                ev["CAUSAL_EX_TOP1_PNL"] = None
                ev["G6_BLOCKER"] = har.get("blocker")
                ev["BASE_QUALIFIED"] = False
                return ev
            g6_cache[key] = dict(har.get("rows_by") or {})
        g6_rows = list((g6_cache[key] or {}).get(str(ev["STRATEGY_ID"])) or [])
        c_occ = replay(filter_days(g6_rows, days))
        c_pack = pack_trades(list(c_occ.get("trades") or []), days=days)
        causal_pnl = float(c_pack.get("TOTAL_PNL") or 0.0)
        ev["CAUSAL_EX_TOP1_PNL"] = causal_pnl
        ev["CAUSAL_EX_TOP1_PF"] = c_pack.get("PF")
        ev["CAUSAL_EX_TOP1_METHOD"] = "UNIVERSE_REMOVAL_BEFORE_SIGNAL"
    else:
        c_occ = replay(exclude_symbol(rows, str(top)))
        c_pack = pack_trades(list(c_occ.get("trades") or []), days=days)
        causal_pnl = float(c_pack.get("TOTAL_PNL") or 0.0)
        ev["CAUSAL_EX_TOP1_PNL"] = causal_pnl
        ev["CAUSAL_EX_TOP1_PF"] = c_pack.get("PF")
        ev["CAUSAL_EX_TOP1_METHOD"] = "UNIVERSE_SYMBOL_REMOVED_FROM_SIGNALS"
    ev["g_table"]["G6"] = causal_pnl >= 0.0
    cov = bool(ev.get("coverage_ok"))
    gtab = ev["g_table"]
    blocks = ev.get("blocks") or {}
    ev["BASE_QUALIFIED"] = bool(
        cov and all(gtab.get(k) for k in ("G1", "G2", "G3", "G4", "G5", "G6")) and blocks.get("S1") and blocks.get("S2")
    )
    return ev


def full_dev_rank_key(row: dict[str, Any]) -> tuple:
    ex_block = _f((row.get("blocks") or {}).get("EX_BEST_BLOCK_PNL")) or 0.0
    dd = abs(float(_f(row.get("MaxDD") if row.get("MaxDD") is not None else row.get("MAXDD")) or 0.0))
    return (
        -float(_f(row.get("EX_BEST_DAY_PNL")) or 0.0),
        -float(ex_block),
        float(dd),
        -float(_f(row.get("TOTAL_PNL")) or 0.0),
        -_pf_sort_key(row.get("PF")),
        int(OPERATOR_RANK.get(str(row.get("OPERATOR") or ""), 9)),
        str(row.get("STRATEGY_ID") or ""),
    )


def train_rank_key(row: dict[str, Any]) -> tuple:
    dd = abs(float(_f(row.get("MaxDD") if row.get("MaxDD") is not None else row.get("MAXDD")) or 0.0))
    return (
        -float(_f(row.get("TOTAL_PNL")) or 0.0),
        -_pf_sort_key(row.get("PF")),
        float(dd),
        str(row.get("STRATEGY_ID") or ""),
    )


def selection_transfer(
    rows_by: dict[str, list[dict[str, Any]]],
    selectable: list[dict[str, Any]],
    full_dev_id: Optional[str],
) -> dict[str, Any]:
    meta = {str(c["STRATEGY_ID"]): c for c in selectable}
    folds = []
    test_pnls: list[float] = []
    pos_n = neg_n = zero_n = failed_n = 0
    top3_n = 0
    for bid in ("B1", "B2", "B3", "B4", "B5"):
        train = train_days_for_block(bid)
        test = test_days_for_block(bid)
        ranked = []
        for cid, cand in meta.items():
            rs = filter_days(list(rows_by.get(cid) or []), train)
            ev = evaluate_strategy(
                cid,
                rs,
                days=train,
                meta=cand,
                compute_g6=False,
                compute_blocks=False,
            )
            ranked.append(ev)
        eligible = [r for r in ranked if train_eligible(r)]
        eligible.sort(key=train_rank_key)
        win = str(eligible[0]["STRATEGY_ID"]) if eligible else None
        failed = win is None
        test_ev = None
        test_pnl = None
        test_pf = None
        test_n = None
        test_dd = None
        if win:
            test_rows = filter_days(list(rows_by.get(win) or []), test)
            test_ev = evaluate_strategy(
                win,
                test_rows,
                days=test,
                meta=meta[win],
                compute_g6=False,
                compute_blocks=False,
            )
            test_pnl = float(test_ev.get("TOTAL_PNL") or 0.0)
            test_pf = test_ev.get("PF")
            test_n = test_ev.get("trade_n")
            test_dd = test_ev.get("MaxDD") if test_ev.get("MaxDD") is not None else test_ev.get("MAXDD")
            test_pnls.append(test_pnl)
            if test_pnl > 1e-12:
                pos_n += 1
            elif test_pnl < -1e-12:
                neg_n += 1
            else:
                zero_n += 1
        else:
            failed_n += 1
        train_top3 = [str(r["STRATEGY_ID"]) for r in eligible[:3]]
        if full_dev_id and full_dev_id in train_top3:
            top3_n += 1
        folds.append(
            {
                "FOLD_ID": bid,
                "TRAIN_BLOCKS": [b for b in ("B1", "B2", "B3", "B4", "B5") if b != bid],
                "TEST_BLOCK": bid,
                "test_days": test,
                "train_days": train,
                "FOLD_SELECTED_ID": win,
                "FOLD_SELECTION_FAILED": failed,
                "TRAIN_TOTAL_PNL": None if not win else eligible[0].get("TOTAL_PNL"),
                "TRAIN_PF": None if not win else eligible[0].get("PF"),
                "TRAIN_MaxDD": None if not win else (eligible[0].get("MaxDD") if eligible[0].get("MaxDD") is not None else eligible[0].get("MAXDD")),
                "TEST_TRADE_N": test_n,
                "TEST_TOTAL_PNL": test_pnl,
                "TEST_PF": test_pf,
                "TEST_MaxDD": test_dd,
                "TRAIN_TOP3": train_top3,
                "TRAIN_ELIGIBLE_N": len(eligible),
            }
        )
        print(f"FOLD {bid} selected={win} failed={failed} test_pnl={test_pnl} top3={train_top3}", flush=True)
    total_test = float(sum(test_pnls)) if test_pnls else 0.0
    st1 = total_test > 0.0
    st2 = int(pos_n) >= 3
    st3 = int(failed_n) <= 1
    st4 = int(top3_n) >= 3
    return {
        "folds": folds,
        "FOLD_SELECTED_TEST_TOTAL_PNL": total_test,
        "FOLD_SELECTED_TEST_POSITIVE_N": int(pos_n),
        "FOLD_SELECTED_TEST_NEGATIVE_N": int(neg_n),
        "FOLD_SELECTED_TEST_ZERO_N": int(zero_n),
        "FOLD_SELECTION_FAILED_N": int(failed_n),
        "FULL_DEV_WINNER_TRAIN_TOP3_N": int(top3_n),
        "ST1": bool(st1),
        "ST2": bool(st2),
        "ST3": bool(st3),
        "ST4": bool(st4),
        "ST_ALL": bool(st1 and st2 and st3 and st4),
    }


def decide(
    *,
    integrity_pass: bool,
    canary_ok: bool,
    economics_opened: bool,
    base_qualified: list[dict[str, Any]],
    closed_qualified: list[dict[str, Any]],
    transfer: dict[str, Any] | None,
    full_dev_id: Optional[str],
) -> dict[str, Any]:
    base = {
        "CERTIFIED": False,
        "TRUE_OOS": False,
        "SIZING": False,
        "ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS": False,
        "ADMISSION_COMPONENT_CAUSAL_ATTRIBUTION_CLAIMED": False,
        "CAP_ALONE_CAUSES_EDGE": False,
        "RAW_SIGNAL_SCREENING_AS_PRIMARY_DECISION_UNIT": False,
        "POST_RESULT_MECHANISM_CHANGE": False,
        "POST_RESULT_EXIT_CHANGE": False,
        "POST_RESULT_CAP_CHANGE": False,
        "POST_RESULT_THRESHOLD_CHANGE": False,
        "POST_RESULT_TIMEFRAME_CHANGE": False,
        "POST_RESULT_CANDIDATE_ADD": False,
        "EXIT_SEARCH": False,
        "ENTRY_EXIT_GRID": False,
        "FULL_STRATEGY_DEV_FROZEN": False,
        "DEV_CANDIDATE": None,
    }
    if not integrity_pass:
        return {
            **base,
            "CASE": "INTEGRITY",
            "VERDICT": CASE_INTEGRITY,
            "NEXT": NEXT_FIX,
            "ECONOMICS_OPENED": False,
        }
    if not canary_ok:
        return {
            **base,
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_FIX,
            "ECONOMICS_OPENED": False,
        }
    n = int(len(base_qualified))
    if n <= 0:
        if closed_qualified:
            return {
                **base,
                "CASE": "D",
                "VERDICT": CASE_D,
                "NEXT": NEXT_B,
                "ECONOMICS_OPENED": True,
                "BASE_QUALIFIED_N": 0,
            }
        return {
            **base,
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": NEXT_B,
            "ECONOMICS_OPENED": True,
            "BASE_QUALIFIED_N": 0,
        }
    if not bool((transfer or {}).get("ST_ALL")):
        return {
            **base,
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": NEXT_B,
            "ECONOMICS_OPENED": True,
            "BASE_QUALIFIED_N": n,
            "FULL_DEV_SELECTED_ID": full_dev_id,
        }
    return {
        **base,
        "CASE": "A",
        "VERDICT": CASE_A,
        "NEXT": NEXT_A,
        "ECONOMICS_OPENED": True,
        "FULL_STRATEGY_DEV_FROZEN": True,
        "DEV_CANDIDATE": full_dev_id,
        "BASE_QUALIFIED_N": n,
        "FULL_DEV_SELECTED_ID": full_dev_id,
        "Classification": "DEV_CANDIDATE",
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    pin = dict(report.get("parent") or {})
    d = dict(report.get("decision") or {})
    freeze = dict(report.get("candidate_set") or {})
    mapping = dict(report.get("exit_mapping") or {})
    integ = dict(report.get("integrity") or {})
    can = dict(report.get("canary") or {})
    trans = dict(report.get("selection_transfer") or {})
    sel = dict(report.get("selected") or {})
    folds = list(trans.get("folds") or [])
    obs = dict(can.get("observed") or {})
    return {
        "1_parent_unit_mismatch_verdict_pinned": bool(pin.get("ok")) and str(pin.get("observed_verdict") or "") == str(pin.get("REQUIRED_PARENT_VERDICT") or ""),
        "2_R2_raw_signal_N": pin.get("R2_RAW_SIGNAL_N"),
        "3_R2_raw_H5_mean": pin.get("R2_RAW_H5_MEAN"),
        "4_R2_executed_trade_N": pin.get("R2_TRADE_N"),
        "5_R2_executed_H5_mean": pin.get("R2_EXECUTED_H5_MEAN"),
        "6_PORTFOLIO_ADMISSION_MATTERS": pin.get("PORTFOLIO_ADMISSION_MATTERS"),
        "7_CAP_alone_causal_attribution_claimed": False,
        "8_raw_signal_screening_used_for_candidate_rejection": False,
        "9_mechanism_library_N": 42,
        "10_mechanism_library_SHA": pin.get("LIBRARY_SHA256"),
        "11_mechanism_library_unchanged": True,
        "12_O1_technical_EXIT_definition": O1_EXIT_DEFINITION,
        "13_O2_technical_EXIT_definition": O2_EXIT_DEFINITION,
        "14_O3_technical_EXIT_definition": O3_EXIT_DEFINITION,
        "15_EXIT_search": False,
        "16_ENTRY_x_EXIT_grid": False,
        "17_FULL_STRATEGY_MAPPING_SHA256": mapping.get("FULL_STRATEGY_MAPPING_SHA256"),
        "18_mapping_frozen_before_economics": True,
        "19_FULL_CAUSAL_CANDIDATE_N": freeze.get("FULL_CAUSAL_CANDIDATE_N"),
        "20_CLOSED_REFERENCE_N": freeze.get("CLOSED_REFERENCE_N"),
        "21_SELECTABLE_FULL_CAUSAL_CANDIDATE_N": freeze.get("SELECTABLE_FULL_CAUSAL_CANDIDATE_N"),
        "22_FULL_CAUSAL_CANDIDATE_SET_SHA256": freeze.get("FULL_CAUSAL_CANDIDATE_SET_SHA256"),
        "23_candidate_set_frozen_before_economics": True,
        "24_integrity_PASS_N_total": f"{integ.get('PASS_N')}/{integ.get('TOTAL_N')}",
        "25_economics_visible_before_integrity": False,
        "26_R2_canary_parity_signal_n": obs.get("signal_n"),
        "27_R2_canary_parity_trade_n": obs.get("trade_n"),
        "28_R2_canary_total_PnL": obs.get("TOTAL_PNL"),
        "29_R2_canary_PF": obs.get("PF"),
        "30_R2_canary_parity_PASS": can.get("HARD_PASS"),
        "31_coverage_PASS_strategy_N": report.get("coverage_pass_n"),
        "32_G1_G5_survivor_N": report.get("g1_g5_survivor_n"),
        "33_G6_survivor_N": report.get("g6_survivor_n"),
        "34_S1_S2_survivor_N": report.get("s1_s2_survivor_n"),
        "35_BASE_QUALIFIED_N": report.get("BASE_QUALIFIED_N"),
        "36_BASE_QUALIFIED_IDS": report.get("BASE_QUALIFIED_IDS"),
        "37_fold_ids": [f.get("FOLD_ID") for f in folds],
        "38_FOLD_SELECTED_ID": [f.get("FOLD_SELECTED_ID") for f in folds],
        "39_TRAIN_TOTAL_PNL": [f.get("TRAIN_TOTAL_PNL") for f in folds],
        "40_TRAIN_PF": [f.get("TRAIN_PF") for f in folds],
        "41_TEST_TRADE_N": [f.get("TEST_TRADE_N") for f in folds],
        "42_TEST_TOTAL_PNL": [f.get("TEST_TOTAL_PNL") for f in folds],
        "43_TEST_PF": [f.get("TEST_PF") for f in folds],
        "44_FOLD_SELECTED_TEST_TOTAL_PNL": trans.get("FOLD_SELECTED_TEST_TOTAL_PNL"),
        "45_FOLD_SELECTED_TEST_POSITIVE_N": trans.get("FOLD_SELECTED_TEST_POSITIVE_N"),
        "46_FOLD_SELECTED_TEST_NEGATIVE_N": trans.get("FOLD_SELECTED_TEST_NEGATIVE_N"),
        "47_FOLD_SELECTION_FAILED_N": trans.get("FOLD_SELECTION_FAILED_N"),
        "48_FULL_DEV_SELECTED_ID": report.get("FULL_DEV_SELECTED_ID"),
        "49_FULL_DEV_WINNER_TRAIN_TOP3_N": trans.get("FULL_DEV_WINNER_TRAIN_TOP3_N"),
        "50_ST1_PASS": trans.get("ST1"),
        "51_ST2_PASS": trans.get("ST2"),
        "52_ST3_PASS": trans.get("ST3"),
        "53_ST4_PASS": trans.get("ST4"),
        "54_final_qualifying_candidate_N": 1 if d.get("DEV_CANDIDATE") else 0,
        "55_selected_DEV_candidate_ID": d.get("DEV_CANDIDATE"),
        "56_selected_trade_n": sel.get("trade_n"),
        "57_selected_fill_day_n": sel.get("fill_day_n"),
        "58_selected_trades_per_day": sel.get("trades_per_day"),
        "59_selected_TOTAL_PNL": sel.get("TOTAL_PNL"),
        "60_selected_PF": sel.get("PF"),
        "61_selected_MaxDD": sel.get("MaxDD") if sel.get("MaxDD") is not None else sel.get("MAXDD"),
        "62_selected_pos_neg_zero_days": [
            sel.get("positive_day_n"),
            sel.get("negative_day_n"),
            sel.get("zero_day_n"),
        ],
        "63_selected_EX_BEST_DAY_PNL": sel.get("EX_BEST_DAY_PNL"),
        "64_selected_causal_ex_top1_PnL": sel.get("CAUSAL_EX_TOP1_PNL"),
        "65_selected_positive_block_N": (sel.get("blocks") or {}).get("POSITIVE_BLOCK_N"),
        "66_selected_ex_best_block_PnL": (sel.get("blocks") or {}).get("EX_BEST_BLOCK_PNL"),
        "67_post_result_mechanism_change": False,
        "68_post_result_EXIT_change": False,
        "69_post_result_CAP_change": False,
        "70_post_result_threshold_change": False,
        "71_Sizing_run": False,
        "72_Holdout_read": False,
        "73_Stress_read": False,
        "74_future_read": False,
        "75_20260903_plus_research_data_read": False,
        "76_Runtime_changed": False,
        "77_Capture_changed": False,
        "78_submit_cancel_live": "0/0/0",
        "79_TRUE_OOS": False,
        "80_CERTIFIED": False,
        "81_VERDICT": d.get("VERDICT"),
        "82_NEXT": d.get("NEXT"),
    }


def evaluate_all_selectable(
    rows_by: dict[str, list[dict[str, Any]]],
    freeze: dict[str, Any],
    *,
    open_economics: bool,
) -> dict[str, Any]:
    if not open_economics:
        return {
            "evaluated": [],
            "coverage_pass_n": None,
            "g1_g5_survivor_n": None,
            "g6_survivor_n": None,
            "s1_s2_survivor_n": None,
            "BASE_QUALIFIED_N": 0,
            "BASE_QUALIFIED_IDS": [],
            "closed_qualified": [],
        }
    days = list(DEVELOPMENT_DAYS)
    g6_cache: dict[str, dict[str, list[dict[str, Any]]]] = {}
    evaluated = []
    closed_eval = []
    for cand in freeze["candidates"]:
        cid = str(cand["STRATEGY_ID"])
        rows = list(rows_by.get(cid) or [])
        ev = evaluate_strategy(cid, rows, days=days, meta=cand, compute_g6=False, compute_blocks=True)
        gtab = ev.get("g_table") or {}
        g15 = all(gtab.get(k) for k in ("G1", "G2", "G3", "G4", "G5"))
        if bool(ev.get("coverage_ok")) and g15:
            ev = apply_g6(ev, rows, days, g6_cache)
        else:
            ev["g_table"]["G6"] = False
            ev["BASE_QUALIFIED"] = False
        print(
            f"{cid} selectable={cand.get('SELECTABLE')} trades={ev.get('trade_n')} pnl={ev.get('TOTAL_PNL')} "
            f"pf={ev.get('PF')} cov={ev.get('coverage_ok')} g6={ev.get('CAUSAL_EX_TOP1_PNL')} q={ev.get('BASE_QUALIFIED')}",
            flush=True,
        )
        if cand.get("SELECTABLE"):
            evaluated.append(ev)
        else:
            closed_eval.append(ev)
    cov_n = sum(1 for r in evaluated if r.get("coverage_ok"))
    g15_n = sum(1 for r in evaluated if r.get("coverage_ok") and all((r.get("g_table") or {}).get(k) for k in ("G1", "G2", "G3", "G4", "G5")))
    g6_n = sum(1 for r in evaluated if r.get("coverage_ok") and all((r.get("g_table") or {}).get(k) for k in ("G1", "G2", "G3", "G4", "G5", "G6")))
    s_n = sum(1 for r in evaluated if r.get("BASE_QUALIFIED"))
    qualified = [r for r in evaluated if r.get("BASE_QUALIFIED") and r.get("SELECTABLE")]
    closed_q = [r for r in closed_eval if r.get("BASE_QUALIFIED")]
    return {
        "evaluated": evaluated,
        "closed_evaluated": closed_eval,
        "coverage_pass_n": cov_n,
        "g1_g5_survivor_n": g15_n,
        "g6_survivor_n": g6_n,
        "s1_s2_survivor_n": s_n,
        "BASE_QUALIFIED_N": int(len(qualified)),
        "BASE_QUALIFIED_IDS": [r["STRATEGY_ID"] for r in qualified],
        "closed_qualified": closed_q,
        "qualified": qualified,
    }
