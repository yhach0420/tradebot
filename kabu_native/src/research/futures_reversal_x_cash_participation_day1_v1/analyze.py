"""Cash-participation evaluation on the frozen 9 confirmations. No Day2 rewrite."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1.engine import mean, median
from research.futures_reversal_x_cash_participation_day1_v1 import (
    ANALYSIS_ID,
    BINDING_LOSER,
    BINDING_WINNER,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    DAY2_PRIMARY,
    DAY2_PRIMARY_ID,
    KIND,
    MIN_TRUE_N,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    NEXT_D,
    PARENT_ID,
    TRADING_DATE,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
    VERDICT_D,
)
from research.futures_reversal_x_cash_participation_day1_v1.engine import attach_cash
from research.futures_reversal_x_cash_participation_day1_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")


def _pack(ev: dict[str, Any], side: str, hz: str, key: str) -> Optional[float]:
    v = ((ev.get(side) or {}).get(hz) or {}).get(key)
    return None if v is None else float(v)


def _event_stats(rows: list[dict[str, Any]], side: str, hz: str, key: str) -> dict[str, Any]:
    xs = []
    for r in rows:
        v = _pack(r, side, hz, key)
        if v is not None:
            xs.append(v)
    return {
        "n": len(xs),
        "mean": mean(xs),
        "median": median(xs),
        "pos_n": sum(1 for v in xs if v > 0),
        "neg_n": sum(1 for v in xs if v < 0),
    }


def _by_hm(events: list[dict[str, Any]], hm: str) -> Optional[dict[str, Any]]:
    for e in events:
        if e.get("trigger_hm") == hm:
            return e
    return None


def _drop_event_stats(rows: list[dict[str, Any]], *, which: str) -> dict[str, Any]:
    xs = [(i, _pack(r, "TOP", "10m", "LONG_mean")) for i, r in enumerate(rows)]
    xs = [(i, v) for i, v in xs if v is not None]
    if len(xs) < 2:
        return {"n": 0, "mean": None, "median": None, "dropped": None}
    if which == "best":
        drop_i = max(xs, key=lambda t: t[1])[0]
    else:
        drop_i = min(xs, key=lambda t: t[1])[0]
    kept = [r for i, r in enumerate(rows) if i != drop_i]
    st = _event_stats(kept, "TOP", "10m", "LONG_mean")
    st["dropped"] = rows[drop_i].get("trigger_hm")
    st["dropped_top_long_10m"] = _pack(rows[drop_i], "TOP", "10m", "LONG_mean")
    return st


def _pooled_symbol_longs(rows: list[dict[str, Any]]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for r in rows:
        by_sym = r.get("top_long_10m_by_symbol") or {}
        for sym, v in by_sym.items():
            if v is None:
                continue
            out.append((str(sym), float(v)))
    return out


def _drop_symbols(pairs: list[tuple[str, float]], k: int) -> dict[str, Any]:
    if not pairs:
        return {"n": 0, "mean": None, "median": None, "dropped": []}
    sums: dict[str, float] = {}
    for sym, v in pairs:
        sums[sym] = sums.get(sym, 0.0) + v
    ranked = sorted(sums.items(), key=lambda kv: (-kv[1], kv[0]))
    drop = [s for s, _ in ranked[:k]]
    kept = [v for s, v in pairs if s not in set(drop)]
    return {
        "n": len(kept),
        "mean": mean(kept),
        "median": median(kept),
        "dropped": drop,
        "dropped_contribution_sum": {s: sums.get(s) for s in drop},
    }


def _classify(
    events: list[dict[str, Any]],
    true_rows: list[dict[str, Any]],
    winner: Optional[dict[str, Any]],
    loser: Optional[dict[str, Any]],
    true10: dict[str, Any],
    drop_best: dict[str, Any],
    drop_s1: dict[str, Any],
    drop_s2: dict[str, Any],
) -> tuple[str, str, str, dict[str, Any]]:
    w_cf = None if winner is None else bool(winner.get("CASH_CONFIRM"))
    l_cf = None if loser is None else bool(loser.get("CASH_CONFIRM"))
    rejects = l_cf is False
    same = w_cf is not None and l_cf is not None and w_cf == l_cf
    n_true = len(true_rows)
    abs_ok = true10.get("mean") is not None and true10.get("median") is not None and float(true10["mean"]) > 0 and float(true10["median"]) > 0
    drop_best_ok = drop_best.get("mean") is not None and float(drop_best["mean"]) > 0
    sym_ok = (
        drop_s1.get("mean") is not None
        and drop_s2.get("mean") is not None
        and float(drop_s1["mean"]) > 0
        and float(drop_s2["mean"]) > 0
    )
    tb = None
    bot = _event_stats(true_rows, "BOTTOM", "10m", "LONG_mean")
    if true10.get("mean") is not None and bot.get("mean") is not None:
        tb = float(true10["mean"]) - float(bot["mean"])
    rel_ok = tb is not None and tb > 0
    n_ok = n_true >= MIN_TRUE_N
    flags = {
        "rejects_1030": rejects,
        "same_confirm_state": same,
        "n_true": n_true,
        "n_sufficient": n_ok,
        "abs_ok": abs_ok,
        "drop_best_ok": drop_best_ok,
        "symbol_exclusion_holds": sym_ok,
        "relative_ok": rel_ok,
        "single_event_only": n_true == 1,
        "CASH_CONFIRM_NOT_SUFFICIENT": bool(l_cf is True),
    }
    if same or (l_cf is True) or (w_cf is False and l_cf is False):
        return CASE_C, VERDICT_C, NEXT_C, flags
    if not n_ok:
        return CASE_D, VERDICT_D, NEXT_D, flags
    if abs_ok and drop_best_ok and sym_ok and rejects:
        return CASE_A, VERDICT_A, NEXT_A, flags
    if rel_ok:
        return CASE_B, VERDICT_B, NEXT_B, flags
    return CASE_C, VERDICT_C, NEXT_C, flags


def run_cash_participation(*, native_root: Optional[Path] = None, trading_date: str = TRADING_DATE) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    attached = attach_cash(native_root=root, day=day)
    events = list(attached["events"])
    true_rows = [e for e in events if e.get("CASH_CONFIRM")]
    false_rows = [e for e in events if not e.get("CASH_CONFIRM")]
    winner = _by_hm(events, BINDING_WINNER)
    loser = _by_hm(events, BINDING_LOSER)
    true10 = _event_stats(true_rows, "TOP", "10m", "LONG_mean")
    false10 = _event_stats(false_rows, "TOP", "10m", "LONG_mean")
    drop_best = _drop_event_stats(true_rows, which="best")
    drop_worst = _drop_event_stats(true_rows, which="worst")
    pooled = _pooled_symbol_longs(true_rows)
    drop_s1 = _drop_symbols(pooled, 1)
    drop_s2 = _drop_symbols(pooled, 2)
    case, verdict, nxt, flags = _classify(events, true_rows, winner, loser, true10, drop_best, drop_s1, drop_s2)
    true1 = _event_stats(true_rows, "TOP", "1m", "LONG_mean")
    true3 = _event_stats(true_rows, "TOP", "3m", "LONG_mean")
    true5 = _event_stats(true_rows, "TOP", "5m", "LONG_mean")
    true_bot = _event_stats(true_rows, "BOTTOM", "10m", "LONG_mean")
    true_all = _event_stats(true_rows, "ALL48", "10m", "LONG_mean")
    tb = None
    if true10.get("mean") is not None and true_bot.get("mean") is not None:
        tb = float(true10["mean"]) - float(true_bot["mean"])
    stock_vals = [v for _s, v in pooled]
    stock_win = {
        "n": len(stock_vals),
        "pos_n": sum(1 for v in stock_vals if v > 0),
        "neg_n": sum(1 for v in stock_vals if v < 0),
        "mean": mean(stock_vals),
        "median": median(stock_vals),
    }
    orders = live_order_counts()

    def _cash_view(ev: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if ev is None:
            return None
        return {
            "trigger_hm": ev.get("trigger_hm"),
            "CASH1": ev.get("CASH1"),
            "CASH2": ev.get("CASH2"),
            "CASH3": ev.get("CASH3"),
            "CASH_CONFIRM": ev.get("CASH_CONFIRM"),
            "ew_past_180": ev.get("ew_past_180"),
            "top_past_180": ev.get("top_past_180"),
            "bottom_past_180": ev.get("bottom_past_180"),
            "top_bottom_past_180": ev.get("top_bottom_past_180"),
            "TOP_LONG_10m": _pack(ev, "TOP", "10m", "LONG_mean"),
        }

    sentence = (
        f"At each frozen BOTH_UP confirmation, CASH_CONFIRM is CASH1 (48-stock "
        f"EW PAST_MID_RET_180S>0) AND CASH2 (TOP16 EW>0) AND CASH3 (TOP16 EW − "
        f"BOTTOM16 EW>0), all received_at<=T. TRUE N={len(true_rows)}. "
        f"09:21:46 confirm={None if winner is None else winner.get('CASH_CONFIRM')} "
        f"10:32:39 confirm={None if loser is None else loser.get('CASH_CONFIRM')}. "
        f"TRUE TOP LONG 10m mean={true10.get('mean')} median={true10.get('median')}."
    )
    thesis = case == CASE_A
    answers = {
        "1_trigger_N": len(events),
        "2_future_leakage_N": attached["future_leakage_n"],
        "3_CASH1_TRUE_N": sum(1 for e in events if e.get("CASH1")),
        "4_CASH2_TRUE_N": sum(1 for e in events if e.get("CASH2")),
        "5_CASH3_TRUE_N": sum(1 for e in events if e.get("CASH3")),
        "6_CASH_CONFIRM_TRUE_N": len(true_rows),
        "7_09:21:46_CASH_states": _cash_view(winner),
        "8_10:32:39_CASH_states": _cash_view(loser),
        "9_CASH_CONFIRM_rejects_10:30": flags["rejects_1030"],
        "10_TRUE_event_timestamps": [e.get("trigger_hm") for e in true_rows],
        "11_FALSE_event_timestamps": [e.get("trigger_hm") for e in false_rows],
        "12_TRUE_TOP_LONG_1m": {"mean": true1["mean"], "median": true1["median"]},
        "13_TRUE_TOP_LONG_3m": {"mean": true3["mean"], "median": true3["median"]},
        "14_TRUE_TOP_LONG_5m": {"mean": true5["mean"], "median": true5["median"]},
        "15_TRUE_TOP_LONG_10m_mean_median": {"mean": true10["mean"], "median": true10["median"]},
        "16_FALSE_TOP_LONG_10m": {"mean": false10["mean"], "median": false10["median"]},
        "17_TRUE_BOTTOM_LONG_10m": {"mean": true_bot["mean"], "median": true_bot["median"]},
        "18_TRUE_TOP_BOTTOM_LONG_10m": tb,
        "19_TRUE_ALL48_LONG_10m": {"mean": true_all["mean"], "median": true_all["median"]},
        "20_TRUE_event_win_rate": {
            "pos_n": true10["pos_n"],
            "neg_n": true10["neg_n"],
            "n": true10["n"],
        },
        "21_TRUE_stock_level_win_rate": stock_win,
        "22_drop_best_event": drop_best,
        "23_drop_worst_event": drop_worst,
        "24_drop_top_symbol": drop_s1,
        "25_drop_top2_symbols": drop_s2,
        "26_N_sufficient": flags["n_sufficient"],
        "27_exact_causal_mechanism": sentence,
        "28_ENTRY_thesis_plausible": thesis,
        "29_ENTRY_built": False,
        "30_EXIT_built": False,
        "31_Day2_primary_changed": False,
        "32_Runtime_changed": False,
        "33_Paper_changed": False,
        "34_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "35_CASE": case,
        "36_VERDICT": verdict,
        "37_NEXT": nxt,
    }
    table = []
    for e in events:
        table.append(
            {
                "event_time": e.get("trigger_hm"),
                "CASH1": e.get("CASH1"),
                "CASH2": e.get("CASH2"),
                "CASH3": e.get("CASH3"),
                "CASH_CONFIRM": e.get("CASH_CONFIRM"),
                "ew_past_180": e.get("ew_past_180"),
                "top_past_180": e.get("top_past_180"),
                "bottom_past_180": e.get("bottom_past_180"),
                "top_bottom_past_180": e.get("top_bottom_past_180"),
                "TOP_LONG_1m": _pack(e, "TOP", "1m", "LONG_mean"),
                "TOP_LONG_3m": _pack(e, "TOP", "3m", "LONG_mean"),
                "TOP_LONG_5m": _pack(e, "TOP", "5m", "LONG_mean"),
                "TOP_LONG_10m": _pack(e, "TOP", "10m", "LONG_mean"),
                "BOTTOM_LONG_10m": _pack(e, "BOTTOM", "10m", "LONG_mean"),
                "ALL48_LONG_10m": _pack(e, "ALL48", "10m", "LONG_mean"),
            }
        )
    decision = {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "ENTRY_built": False,
        "EXIT_built": False,
        "Day2_primary_changed": False,
        "Day2_substitutions_allowed": False,
        "usable_ENTRY_thesis": thesis,
        "CASH_CONFIRM_NOT_SUFFICIENT": flags["CASH_CONFIRM_NOT_SUFFICIENT"],
        "SINGLE_EVENT_ONLY": flags["single_event_only"],
        "submit_cancel_live": answers["34_submit_cancel_live"],
        "day2_primary": dict(DAY2_PRIMARY),
        "day2_primary_id": DAY2_PRIMARY_ID,
        "close_day1_feature_mining": case != CASE_A,
    }
    return {
        "analysis_id": ANALYSIS_ID,
        "parent_id": PARENT_ID,
        "kind": KIND,
        "trading_date": day,
        "built_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "future_leakage_n": attached["future_leakage_n"],
        "trigger_n": len(events),
        "event_table": table,
        "true_n": len(true_rows),
        "false_n": len(false_rows),
        "binding": {"winner": _cash_view(winner), "loser": _cash_view(loser)},
        "true_stats": {
            "1m": true1,
            "3m": true3,
            "5m": true5,
            "10m": true10,
            "bottom10": true_bot,
            "all10": true_all,
            "top_bottom_10m": tb,
        },
        "false_stats": {"10m": false10},
        "robustness": {
            "drop_best_event": drop_best,
            "drop_worst_event": drop_worst,
            "drop_top_symbol": drop_s1,
            "drop_top2_symbols": drop_s2,
            "stock_win": stock_win,
        },
        "flags": flags,
        "answers": answers,
        "decision": decision,
    }
