"""Full Causal 3-candidate ranking. CAUSAL_EX_TOP1 not posthoc. Fold-local LODO."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.participation_onset_full_strategy_v1 import (
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
    PRIOR_RECOVERY,
    WAIT_SEC,
)
from research.participation_onset_full_strategy_v1.harvest import AUDIT
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
    return portfolio_replay(occupancy_rows(rows), wait_sec=float(WAIT_SEC), position_cap=int(POSITION_CAP))


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
        t["profit_giveback"] = src.get("profit_giveback")
        t["loss_avoided"] = src.get("loss_avoided")
        t["volume_percentile_60s"] = src.get("volume_percentile_60s")
        t["distance_from_vwap_bps"] = src.get("distance_from_vwap_bps")
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
    hold_vals = []
    for t in trades:
        h = _f(t.get("hold_sec"))
        if h is None and isinstance(t.get("src"), dict):
            h = _f(t["src"].get("hold_sec"))
        if h is not None:
            hold_vals.append(h)
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
        "avg_giveback": _mean([t.get("profit_giveback") for t in trades]),
        "avg_loss_avoided": _mean([t.get("loss_avoided") for t in trades]),
        "spread0": _mean([t.get("spread0") for t in trades]),
        "avg_hold_sec": _mean(hold_vals),
        "median_hold_sec": _median(hold_vals),
        "volume_percentile_at_entry": _mean([t.get("volume_percentile_60s") for t in trades]),
        "distance_from_vwap_bps": _mean([t.get("distance_from_vwap_bps") for t in trades]),
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
        "order_n": row.get("order_n"),
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
        "CAUSAL_EX_TOP1_positive_days": row.get("CAUSAL_EX_TOP1_positive_day_n"),
        "CAUSAL_EX_TOP1_negative_days": row.get("CAUSAL_EX_TOP1_negative_day_n"),
        "newly_admitted_n": row.get("newly_admitted_trade_n"),
        "newly_admitted_pnl": row.get("newly_admitted_trade_pnl"),
        "direct_exit_effect": row.get("direct_exit_effect"),
        "slot_release_effect": row.get("downstream_pnl"),
        "avg_hold_sec": row.get("avg_hold_sec"),
        "median_hold_sec": row.get("median_hold_sec"),
        "MFE": row.get("MFE"),
        "MAE": row.get("MAE"),
        "g_table": row.get("g_table"),
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
    fold_econ_n = 0
    for leave in days:
        keep = [d for d in days if d != leave]
        keep_set = set(keep)
        ranked = []
        for cid in CANDIDATE_IDS:
            rs = [r for r in (rows_by.get(cid) or []) if str(r.get("date") or "") in keep_set]
            cs = [r for r in (ctrl_by.get("P1_X1") or []) if str(r.get("date") or "") in keep_set]
            ranked.append(evaluate_candidate(cid, rs, cs, days=keep))
        passed = rank_pass(ranked)
        win = passed[0]["candidate_id"] if passed else None
        winner_row = next((r for r in ranked if r.get("candidate_id") == winner_id), None)
        fold_econ = bool(winner_row and winner_row.get("gate") == "PASS")
        folds.append(
            {
                "leave": leave,
                "winner": win,
                "pass_n": len(passed),
                "winner_gate": None if winner_row is None else winner_row.get("gate"),
                "fold_economic_pass": fold_econ,
            }
        )
        if fold_econ:
            fold_econ_n += 1
        if winner_id and win == winner_id:
            selected_n += 1
        print(f"LODO leave={leave} winner={win} pass_n={len(passed)} fold_econ={fold_econ}", flush=True)
    stable = bool(winner_id) and int(fold_econ_n) >= int(LODO_TOP3_MIN) and int(selected_n) >= int(LODO_TOP3_MIN)
    return {
        "folds": folds,
        "FOLD_ECONOMIC_PASS_N": fold_econ_n,
        "WINNER_SELECTED_N": selected_n,
        "stable": stable,
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
        "RECOVERY_SEQUENCE_CLOSED": True,
    }
    if not integrity or not exec_integ:
        return {**base, "CASE": "E", "VERDICT": CASE_E, "NEXT": "STOP. DO_NOT_INTERPRET_ECONOMICS."}
    if int(coverage_pass_n) <= 0:
        return {
            **base,
            "CASE": "D",
            "VERDICT": CASE_D,
            "NEXT": "Coverage failed. Do not relax the frozen T1 threshold. Do not open Stress.",
        }
    if int(pass_n) <= 0 or winner_id is None:
        return {
            **base,
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": "Participation Onset architecture CLOSE. Do not retune threshold. Do not add VWAP filter. Do not add 4th EXIT. Do not open Stress.",
        }
    if not bool((lodo_pack or {}).get("stable")):
        return {
            **base,
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": "SELECTION_UNSTABLE. Do not open Stress. Do not retune threshold.",
        }
    return {
        **base,
        "CASE": "A",
        "VERDICT": CASE_A,
        "FULL_STRATEGY_DEV_FROZEN": True,
        "NEXT": "PARTICIPATION_ONSET_REUSED_HISTORY_STRESS_V1 for this one frozen candidate only. Not this run.",
    }


def _by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for row in list(report.get("evaluated") or report.get("ranking") or []):
        cid = str(row.get("candidate_id") or "")
        if cid:
            out[cid] = row
    return out


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    w = dict(report.get("winner") or {})
    lodo_p = dict(report.get("lodo") or {})
    dup = dict(report.get("already_executed") or {})
    leak = dict(report.get("leakage") or {})
    by = _by_id(report)
    ev = list(report.get("evaluated_public") or report.get("ranking") or [])
    return {
        "1_prior_Recovery_verdict": PRIOR_RECOVERY["VERDICT"],
        "2_why_Recovery_closed": "6/6 candidates coverage PASS but economic PASS=0. Every CAUSAL_EX_TOP1_PNL < 0.",
        "3_why_R4_not_allowed": "Recovery Sequence architecture CLOSE. Adding R4 would be sequential handmade hopping after CASE B.",
        "4_why_Participation_Onset_selected": "Next architecture after price-bar Recovery failure: reuse frozen E1_X14 T1 volume_percentile_60s activity onset, not a new price-bar pattern.",
        "5_exact_prior_X14_T1_rule": "feature_status==OK AND relative_status==OK AND rs_universe_n>=20 AND finite(volume_percentile_60s) AND volume_percentile_60s>=0.6486486486486487 on the X14 10s grid. ENTRY is FALSE->TRUE onset; previous missing/not-evaluable is FALSE.",
        "6_above_VWAP_part_of_frozen_T1": False,
        "7_threshold": 0.6486486486486487,
        "8_threshold_retuned": False,
        "9_independent_alpha_claim": False,
        "10_RPFE_independence_claim": False,
        "11_exact_ENTRY": "AM 10s grid. Current t1_raw True AND previous grid evaluable with volume_percentile_60s < threshold. SIGNAL_T0=current 10s event time. No VWAP/price/RCI/EMA/BB/symbol/spread filter.",
        "12_exact_X1_execution": "First fresh valid Ask1 at/after t0. 100-share marketable BUY. fill_price=observed Ask1. No queue/mid/print/bar/reprice/chase.",
        "13_exact_Z3": "Completed 1m bars. 2 consecutive Close<Open AND Close<Close[k-1]. First-fire then EXIT_PENDING until first fresh Bid1.",
        "14_exact_ZP": "After fill, first evaluable 10s grid with volume_percentile_60s < 0.6486486486486487. Missing/not-evaluable does not trigger.",
        "15_exact_ZH": "First-fire of Z3 or ZP. No extra condition.",
        "16_candidate_N": 3,
        "17_already_executed_check": dup,
        "18_exact_duplicate": bool(dup.get("duplicate")),
        "19_Development_days": list(DEVELOPMENT_DAYS),
        "20_burned_Holdout_read": False,
        "21_Stress_read": False,
        "22_each_signal_order_fill_trade_N": [
            {
                "candidate_id": r.get("candidate_id"),
                "signal_n": r.get("signal_n"),
                "order_n": r.get("order_n"),
                "fill_n": r.get("fill_n"),
                "TRADE_N": r.get("trade_n") or r.get("TRADE_N"),
            }
            for r in ev
        ],
        "23_each_fill_rate": [{ "candidate_id": r.get("candidate_id"), "fill_rate": r.get("fill_rate") } for r in ev],
        "24_each_PnL": [{ "candidate_id": r.get("candidate_id"), "PnL": r.get("pnl") or r.get("TOTAL_PNL") } for r in ev],
        "25_each_PF": [{ "candidate_id": r.get("candidate_id"), "PF": r.get("PF") } for r in ev],
        "26_each_MaxDD": [{ "candidate_id": r.get("candidate_id"), "MaxDD": r.get("MaxDD") or r.get("MAXDD") } for r in ev],
        "27_each_day_signs": [
            {
                "candidate_id": r.get("candidate_id"),
                "positive": r.get("positive_days") if r.get("positive_days") is not None else by.get(str(r.get("candidate_id") or ""), {}).get("positive_day_n"),
                "negative": r.get("negative_days") if r.get("negative_days") is not None else by.get(str(r.get("candidate_id") or ""), {}).get("negative_day_n"),
            }
            for r in ev
        ],
        "28_each_EX_BEST": [{ "candidate_id": r.get("candidate_id"), "EX_BEST": r.get("EX_BEST") } for r in ev],
        "29_each_top_symbol": [{ "candidate_id": r.get("candidate_id"), "top_symbol": r.get("top_symbol") } for r in ev],
        "30_each_top_symbol_PnL": [{ "candidate_id": r.get("candidate_id"), "top_symbol_pnl": r.get("top_symbol_pnl") } for r in ev],
        "31_each_CAUSAL_EX_TOP1_PnL": [{ "candidate_id": r.get("candidate_id"), "CAUSAL_EX_TOP1": r.get("CAUSAL_EX_TOP1") } for r in ev],
        "32_each_CAUSAL_EX_TOP1_PF": [{ "candidate_id": r.get("candidate_id"), "CAUSAL_EX_TOP1_PF": r.get("CAUSAL_EX_TOP1_PF") } for r in ev],
        "33_each_causal_day_signs": [
            {
                "candidate_id": r.get("candidate_id"),
                "positive": r.get("CAUSAL_EX_TOP1_positive_days"),
                "negative": r.get("CAUSAL_EX_TOP1_negative_days"),
            }
            for r in ev
        ],
        "34_each_newly_admitted_N": [{ "candidate_id": r.get("candidate_id"), "n": r.get("newly_admitted_n") } for r in ev],
        "35_each_newly_admitted_PnL": [{ "candidate_id": r.get("candidate_id"), "pnl": r.get("newly_admitted_pnl") } for r in ev],
        "36_direct_exit_effect": [{ "candidate_id": r.get("candidate_id"), "direct_exit_effect": r.get("direct_exit_effect") } for r in ev],
        "37_slot_release_effect": [{ "candidate_id": r.get("candidate_id"), "slot_release_effect": r.get("slot_release_effect") } for r in ev],
        "38_downstream_PnL": [{ "candidate_id": r.get("candidate_id"), "downstream_pnl": r.get("slot_release_effect") } for r in ev],
        "39_avg_median_hold_time": [
            {
                "candidate_id": r.get("candidate_id"),
                "avg_hold_sec": r.get("avg_hold_sec"),
                "median_hold_sec": r.get("median_hold_sec"),
            }
            for r in ev
        ],
        "40_MFE_MAE": [{ "candidate_id": r.get("candidate_id"), "MFE": r.get("MFE"), "MAE": r.get("MAE") } for r in ev],
        "41_coverage_PASS_N": report.get("coverage_pass_n"),
        "42_economic_PASS_N": report.get("economic_pass_n"),
        "43_G1_G6_table": { cid: (row.get("g_table") or {}) for cid, row in by.items() } if by else { r.get("candidate_id"): r.get("g_table") for r in ev },
        "44_provisional_winner": w.get("candidate_id"),
        "45_LODO_ran": bool(lodo_p),
        "46_fold_economic_pass_N": lodo_p.get("FOLD_ECONOMIC_PASS_N"),
        "47_fold_winner_counts": [f.get("winner") for f in (lodo_p.get("folds") or [])],
        "48_stable": lodo_p.get("stable"),
        "49_FULL_STRATEGY_DEV_FROZEN": bool(d.get("FULL_STRATEGY_DEV_FROZEN")),
        "50_Stress_opened": False,
        "51_Sizing_ran": False,
        "52_verdict": d.get("VERDICT"),
        "53_next": d.get("NEXT"),
        "54_Runtime_changed": False,
        "55_future_used": bool(int(leak.get("FUTURE_DATA_N") or 0)),
        "56_MAX_RESEARCH_DATE": "20260807",
        "57_TRUE_OOS": False,
        "58_CERTIFIED": False,
        "59_submit_cancel_live": "0/0/0",
        "prior_recovery_reference": PRIOR_RECOVERY,
        "WINNER_SELECTED_N": lodo_p.get("WINNER_SELECTED_N"),
    }
