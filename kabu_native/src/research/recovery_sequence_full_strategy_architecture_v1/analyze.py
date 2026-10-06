"""Full Causal 6-candidate ranking. CAUSAL_EX_TOP1 not posthoc. Fold-local LODO."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.recovery_sequence_full_strategy_architecture_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    CANDIDATE_IDS,
    DEVELOPMENT_DAYS,
    ENTRY_NAMES,
    EXEC_NAMES,
    EXIT_NAMES,
    LODO_TOP3_MIN,
    MIN_PF,
    MIN_TOTAL_TRADES,
    MIN_TRADES_PER_DAY,
    MIN_TRADING_DAYS_WITH_FILL,
    POSITION_CAP,
    PRIOR_E4,
)
from research.recovery_sequence_full_strategy_architecture_v1.harvest import AUDIT
from research.simple_full_strategy_discovery_v1.analyze import pack_trades
from research.simple_tech_entry_family.portfolio import _sym, portfolio_replay
from research.simple_tech_redesign.branch_u_causal_analyze import attribution


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


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [x for x in (_f(v) for v in xs) if x is not None]
    return float(np.mean(vs)) if vs else None


def _median(xs: list[Any]) -> Optional[float]:
    vs = [x for x in (_f(v) for v in xs) if x is not None]
    return float(np.median(vs)) if vs else None


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


def parse_cid(cid: str) -> tuple[str, str, str]:
    e, x, z = str(cid).split("_")
    return e, x, z


def tid(t: dict[str, Any]) -> tuple[str, str, float]:
    return (str(t.get("date") or ""), _sym(t), float(t.get("t0") or 0.0))


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
    return portfolio_replay(occupancy_rows(rows), wait_sec=0.05, position_cap=int(POSITION_CAP))


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


def evaluate_candidate(cid: str, rows: list[dict[str, Any]], ctrl_rows: list[dict[str, Any]], *, days: list[str]) -> dict[str, Any]:
    e, x, z = parse_cid(cid)
    occ = replay(rows)
    trades = list(occ.get("trades") or [])
    for t in trades:
        src = t.get("src") or {}
        t["hold_sec"] = t.get("hold_sec") if t.get("hold_sec") is not None else src.get("hold_sec")
        t["mfe_yen"] = src.get("mfe_yen")
        t["mae_yen"] = src.get("mae_yen")
        t["MID_60"] = src.get("MID_60")
        t["MID_180"] = src.get("MID_180")
        t["MID_300"] = src.get("MID_300")
        t["spread0"] = src.get("spread0")
    ctrl_occ = replay(ctrl_rows)
    attr = attribution(list(ctrl_occ.get("trades") or []), trades)
    pack = pack_trades(trades, days=days)
    top, top_pnl = top_symbol_from_trades(trades)
    causal_pnl = None
    causal_pack: dict[str, Any] = {}
    newly_n = None
    newly_pnl = None
    if top:
        c_rows = exclude_symbol(rows, top)
        c_ctrl = exclude_symbol(ctrl_rows, top)
        c_occ = replay(c_rows)
        c_trades = list(c_occ.get("trades") or [])
        causal_pack = pack_trades(c_trades, days=days)
        causal_pnl = float(causal_pack.get("TOTAL_PNL") or 0.0)
        base_ids = {tid(t) for t in trades}
        newly = [t for t in c_trades if tid(t) not in base_ids]
        newly_n = len(newly)
        newly_pnl = float(sum(float(t.get("pnl_yen_100") or 0.0) for t in newly))
    cov = coverage_ok(pack)
    eco = economic_base_ok(pack) and causal_pnl is not None and float(causal_pnl) >= 0.0
    if not cov:
        gate = "COVERAGE_FAIL"
    elif not eco:
        gate = "ECONOMIC_FAIL"
    else:
        gate = "PASS"
    sc = corrected_score(pack, float(causal_pnl)) if gate == "PASS" and causal_pnl is not None else None
    signal_n = len(rows)
    order_n = sum(1 for r in rows if r.get("ORDERED"))
    fill_n = int(occ.get("fill_n") or 0)
    gtable = {
        "G1_TOTAL_PNL": bool(_f(pack.get("TOTAL_PNL")) is not None and float(pack["TOTAL_PNL"]) > 0),
        "G2_PF": pack.get("PF") is not None and (pack.get("PF") == float("inf") or float(pack["PF"]) > float(MIN_PF)),
        "G3_DAY_SIGNS": int(pack.get("positive_day_n") or 0) > int(pack.get("negative_day_n") or 0),
        "G4_EX_BEST": _f(pack.get("EX_BEST_DAY_PNL")) is not None and float(pack["EX_BEST_DAY_PNL"]) > 0,
        "G5_PNL_PLUS_MAXDD": (_f(pack.get("TOTAL_PNL")) or 0.0) + (_f(pack.get("MAXDD")) or 0.0) > 0,
        "G6_CAUSAL_EX_TOP1": causal_pnl is not None and float(causal_pnl) >= 0.0,
    }
    return {
        "candidate_id": cid,
        "entry_id": e,
        "execution_id": x,
        "exit_id": z,
        "entry_name": ENTRY_NAMES.get(e),
        "exec_name": EXEC_NAMES.get(x),
        "exit_name": EXIT_NAMES.get(z),
        "signal_n": signal_n,
        "order_n": order_n,
        "fill_n": fill_n,
        "fill_rate": (float(fill_n) / float(order_n)) if order_n else None,
        "slot_release_n": len(trades),
        "downstream_trade_n": len(attr.get("incremental_ids") or []),
        "downstream_pnl": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
        "direct_exit_effect": attr.get("DIRECT_EXIT_DELTA"),
        "gate": gate,
        "score": sc,
        "MID_60": _mean([t.get("MID_60") for t in trades]),
        "MID_180": _mean([t.get("MID_180") for t in trades]),
        "MID_300": _mean([t.get("MID_300") for t in trades]),
        "MFE": _mean([t.get("mfe_yen") for t in trades]),
        "MAE": _mean([t.get("mae_yen") for t in trades]),
        "spread0": _mean([t.get("spread0") for t in trades]),
        "top_symbol": top,
        "top_symbol_pnl": top_pnl,
        "CAUSAL_EX_TOP1_PNL": causal_pnl,
        "CAUSAL_EX_TOP1_PF": causal_pack.get("PF"),
        "CAUSAL_EX_TOP1_MAXDD": causal_pack.get("MAXDD"),
        "CAUSAL_EX_TOP1_TRADE_N": causal_pack.get("TRADE_N"),
        "CAUSAL_EX_TOP1_positive_day_n": causal_pack.get("positive_day_n"),
        "CAUSAL_EX_TOP1_negative_day_n": causal_pack.get("negative_day_n"),
        "CAUSAL_EX_TOP1_zero_day_n": causal_pack.get("zero_day_n"),
        "newly_admitted_trade_n": newly_n,
        "newly_admitted_trade_pnl": newly_pnl,
        "g_table": gtable,
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


def lodo(rows_by: dict[str, list[dict[str, Any]]], ctrl_by: dict[str, list[dict[str, Any]]], winner_id: Optional[str]) -> dict[str, Any]:
    days = list(DEVELOPMENT_DAYS)
    folds = []
    selected_n = 0
    top3_n = 0
    dayset_all = set(days)
    for leave in days:
        keep = [d for d in days if d != leave]
        keep_set = set(keep)
        ranked = []
        for cid in CANDIDATE_IDS:
            e, x, _z = parse_cid(cid)
            rs = [r for r in (rows_by.get(cid) or []) if str(r.get("date") or "") in keep_set]
            cs = [r for r in (ctrl_by.get(f"{e}_{x}") or []) if str(r.get("date") or "") in keep_set]
            ranked.append(evaluate_candidate(cid, rs, cs, days=keep))
        passed = rank_pass(ranked)
        win = passed[0]["candidate_id"] if passed else None
        top3 = [r["candidate_id"] for r in passed[:3]]
        folds.append({"leave": leave, "winner": win, "top3": top3, "pass_n": len(passed)})
        if winner_id and win == winner_id:
            selected_n += 1
        if winner_id and winner_id in top3:
            top3_n += 1
        print(f"LODO leave={leave} winner={win} top3={top3} pass_n={len(passed)}", flush=True)
        _ = dayset_all
    return {
        "folds": folds,
        "WINNER_SELECTED_N": selected_n,
        "WINNER_TOP3_N": top3_n,
        "TOP3_N": top3_n,
        "stable": bool(winner_id) and int(top3_n) >= int(LODO_TOP3_MIN),
    }


def strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "_trades"}


def decide(*, integrity: bool, exec_integ: bool, coverage_pass_n: int, pass_n: int, winner_id: Optional[str], lodo_pack: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "CERTIFIED": False,
        "TRUE_OOS": False,
        "SIZING": False,
        "STRESS_OPENED": False,
        "FULL_STRATEGY_DEV_FROZEN": False,
        "E4_X2_Z3_CLOSED": True,
    }
    if not integrity or not exec_integ:
        return {**base, "CASE": "E", "VERDICT": CASE_E, "NEXT": "STOP. DO_NOT_INTERPRET_ECONOMICS."}
    if int(coverage_pass_n) <= 0:
        return {
            **base,
            "CASE": "D",
            "VERDICT": CASE_D,
            "NEXT": "Coverage failed. Do not relax rules. Do not open Stress.",
        }
    if int(pass_n) <= 0 or winner_id is None:
        return {
            **base,
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": "Recovery Sequence architecture CLOSE. Do not add R4. Architecture redesign. Do not open Stress.",
        }
    if not bool((lodo_pack or {}).get("stable")):
        return {
            **base,
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": "SELECTION_UNSTABLE. Do not open Stress. Do not retune thresholds.",
        }
    return {
        **base,
        "CASE": "A",
        "VERDICT": CASE_A,
        "FULL_STRATEGY_DEV_FROZEN": True,
        "NEXT": "RECOVERY_SEQUENCE_REUSED_HISTORY_STRESS_V1 for this one frozen candidate only. Not this run.",
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    w = dict(report.get("winner") or {})
    lodo_p = dict(report.get("lodo") or {})
    dup = dict(report.get("already_executed") or {})
    leak = dict(report.get("leakage") or {})
    return {
        "1_prior_E4_verdict": PRIOR_E4["CLOSED"] and "E4_X2_Z3_TRUE_SYMBOL_DEPENDENCE_CONFIRMED",
        "2_why_E4_stays_closed": "CAUSAL_EX_TOP1 without 285A was PnL=-380140 PF=0.5576 2/8 days. Exact E4 not revived. No 285A filter.",
        "3_why_Sizing_not_next": "285A was positive in bps and rest-of-book negative. Sizing would hide strategy failure. 100 shares fixed.",
        "4_why_single_handmade_R1_rejected": "No sequential pattern hopping. Three generic recovery mechanisms pre-fixed and compared once.",
        "5_new_architecture_library": ["R1_PRICE_FOLLOWTHROUGH", "R2_FULL_ACCEPTANCE", "R3_PARTICIPATION_CONFIRM"],
        "6_already_executed_check": dup,
        "7_duplicate": bool(dup.get("duplicate")),
        "8_exact_R1": "RECLAIM_ARM Close[r-1]<VWAP[r-1] AND Close[r]>VWAP[r]; next bar Close[c]>VWAP[c] AND Close[c]>Close[r].",
        "9_exact_R2": "Same ARM; next bar Low[c]>VWAP[c] AND Close[c]>Open[c].",
        "10_exact_R3": "Same ARM; next bar Close[c]>VWAP[c] AND Volume[c]>median(Volume[c-10:c]). No multiplier.",
        "11_exact_X1": "As-of t0 first fresh valid Ask1 marketable 100-share BUY. fill=Ask1.",
        "12_exact_X2": "reference_mid=(Bid1+Ask1)/2 at t0; first fresh Ask1<=mid within 5s; marketable BUY; fill=observed Ask1; not resting.",
        "13_queue_assumed_fill_n": int(leak.get("QUEUE_ASSUMED_FILL_N") or 0),
        "14_OHLC_fill_n": int(leak.get("BAR_OHLC_FILL_N") or 0),
        "15_lookahead_fill_n": int(leak.get("LOOKAHEAD_FILL_N") or 0),
        "16_exact_Z3_EXIT": "2 consecutive completed 1m bars Close<Open AND Close<Close[k-1]. First-fire EXIT_TRIGGER.",
        "17_EXIT_PENDING_semantics": "After trigger, stay EXIT_PENDING until first fresh valid Bid1 SELL 100. No 5s cancel. Slot release on actual exit fill. Else SESSION_CLOSE_OPERATIONAL_EXIT.",
        "18_Development_days": list(DEVELOPMENT_DAYS),
        "19_burned_Holdout_read": False,
        "20_Stress_read": False,
        "21_candidate_n": 6,
        "22_complete_ranking_table": list(report.get("ranking") or []),
        "23_coverage_PASS_n": report.get("coverage_pass_n"),
        "24_economic_PASS_n": report.get("economic_pass_n"),
        "25_provisional_winner": w.get("candidate_id"),
        "26_winner_ENTRY": w.get("entry_name") or w.get("entry_id"),
        "27_winner_execution": w.get("exec_name") or w.get("execution_id"),
        "28_winner_trade_n": w.get("TRADE_N"),
        "29_winner_PnL": w.get("TOTAL_PNL"),
        "30_winner_PF": w.get("PF"),
        "31_winner_MaxDD": w.get("MAXDD"),
        "32_winner_day_signs": {
            "positive": w.get("positive_day_n"),
            "negative": w.get("negative_day_n"),
            "zero": w.get("zero_day_n"),
        },
        "33_winner_EX_BEST": w.get("EX_BEST_DAY_PNL"),
        "34_winner_top_symbol": w.get("top_symbol"),
        "35_winner_top_symbol_PnL": w.get("top_symbol_pnl"),
        "36_winner_CAUSAL_EX_TOP1_PnL": w.get("CAUSAL_EX_TOP1_PNL"),
        "37_winner_CAUSAL_EX_TOP1_PF": w.get("CAUSAL_EX_TOP1_PF"),
        "38_winner_causal_day_signs": {
            "positive": w.get("CAUSAL_EX_TOP1_positive_day_n"),
            "negative": w.get("CAUSAL_EX_TOP1_negative_day_n"),
            "zero": w.get("CAUSAL_EX_TOP1_zero_day_n"),
        },
        "39_newly_admitted_n": w.get("newly_admitted_trade_n"),
        "40_newly_admitted_PnL": w.get("newly_admitted_trade_pnl"),
        "41_avg_median_PnL": {"avg": w.get("AVG_PNL"), "median": w.get("MEDIAN_PNL")},
        "42_hold_time": w.get("avg_hold_sec"),
        "43_MFE": w.get("MFE"),
        "44_MAE": w.get("MAE"),
        "45_slot_release": w.get("slot_release_n"),
        "46_downstream_PnL": w.get("downstream_pnl"),
        "47_G1_G6_table": w.get("g_table"),
        "48_LODO_ran": bool(lodo_p),
        "49_fold_winners": [f.get("winner") for f in (lodo_p.get("folds") or [])],
        "50_fold_top3": [f.get("top3") for f in (lodo_p.get("folds") or [])],
        "51_winner_TOP3_N": lodo_p.get("WINNER_TOP3_N"),
        "52_stable": lodo_p.get("stable"),
        "53_FULL_STRATEGY_DEV_FROZEN": bool(d.get("FULL_STRATEGY_DEV_FROZEN")),
        "54_Stress_opened": False,
        "55_Sizing_ran": False,
        "56_verdict": d.get("VERDICT"),
        "57_next": d.get("NEXT"),
        "58_Runtime_changed": False,
        "59_future_used": bool(int(leak.get("FUTURE_DATA_N") or 0)),
        "60_MAX_RESEARCH_DATE": "20260807",
        "61_TRUE_OOS": False,
        "62_CERTIFIED": False,
        "63_submit_cancel_live": "0/0/0",
        "prior_E4_reference": PRIOR_E4,
    }
