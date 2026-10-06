"""Full Causal ranking, hard gates, LODO. MID is diagnostic only."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.metrics import maxdd
from research.simple_full_strategy_discovery_v1 import (
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
    WAIT_SEC,
)
from research.simple_full_strategy_discovery_v1.harvest import AUDIT
from research.simple_full_strategy_discovery_v1.spec import candidate_ids
from research.simple_tech_entry_family.portfolio import portfolio_replay
from research.simple_tech_redesign.branch_u_causal_analyze import attribution

CASE_A = "SIMPLE_FULL_STRATEGY_CANDIDATE_FOUND"
CASE_B = "SIMPLE_FULL_STRATEGY_NO_ROBUST_CANDIDATE"
CASE_C = "SIMPLE_FULL_STRATEGY_SELECTION_UNSTABLE"
CASE_E = "SIMPLE_FULL_STRATEGY_INTEGRITY_FAILED"


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
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def integrity_ok() -> bool:
    return all(int(v) == 0 for v in leakage_n().values())


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


def _pf(trades: list[dict[str, Any]]) -> Optional[float]:
    gp = sum(float(t.get("pnl_yen_100") or 0.0) for t in trades if float(t.get("pnl_yen_100") or 0.0) > 0)
    gl = -sum(float(t.get("pnl_yen_100") or 0.0) for t in trades if float(t.get("pnl_yen_100") or 0.0) < 0)
    if gl <= 1e-12:
        return float("inf") if gp > 1e-12 else None
    return float(gp / gl)


def parse_cid(cid: str) -> tuple[str, str, str]:
    e, x, z = str(cid).split("_")
    return e, x, z


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
    zero_d = len(days) - pos_d - neg_d
    fill_days = sum(1 for d in days if int(by_day_n.get(d) or 0) > 0)
    best_d = max(days, key=lambda d: float(by_day.get(d) or 0.0)) if days else None
    worst_d = min(days, key=lambda d: float(by_day.get(d) or 0.0)) if days else None
    top_day_pnl = float(by_day.get(best_d) or 0.0) if best_d else 0.0
    ex_best = total - top_day_pnl if best_d else total
    top_sym = max(by_sym.keys(), key=lambda s: by_sym[s]) if by_sym else None
    top_sym_pnl = float(by_sym.get(top_sym) or 0.0) if top_sym else 0.0
    drop_top = total - top_sym_pnl if top_sym else total
    dd = float(maxdd(trades, time_key="exit_time", pnl_key="pnl_yen_100")) if trades else 0.0
    wins = sum(1 for v in pnls if v > 1e-12)
    losses = sum(1 for v in pnls if v < -1e-12)
    reasons = Counter(str(t.get("exit_reason") or "") for t in trades)
    holds = [_f(t.get("hold_sec") or t.get("src", {}).get("hold_sec") if isinstance(t.get("src"), dict) else t.get("hold_sec")) for t in trades]
    # hold_sec may be on src
    hold_vals = []
    for t in trades:
        h = _f(t.get("hold_sec"))
        if h is None and isinstance(t.get("src"), dict):
            h = _f(t["src"].get("hold_sec"))
        if h is not None:
            hold_vals.append(h)
    n_days = max(len(days), 1)
    return {
        "TRADE_N": n,
        "TOTAL_PNL": total,
        "PF": _pf(trades),
        "WIN_N": wins,
        "LOSS_N": losses,
        "FLAT_N": n - wins - losses,
        "AVG_PNL": (total / n) if n else None,
        "MEDIAN_PNL": _median(pnls),
        "MAXDD": dd,
        "positive_day_n": pos_d,
        "negative_day_n": neg_d,
        "zero_day_n": zero_d,
        "TRADING_DAY_WITH_FILL_N": fill_days,
        "trades_per_day": float(n) / float(n_days),
        "best_day": best_d,
        "worst_day": worst_d,
        "best_day_pnl": float(by_day.get(best_d) or 0.0) if best_d else None,
        "worst_day_pnl": float(by_day.get(worst_d) or 0.0) if worst_d else None,
        "EX_BEST_DAY_PNL": ex_best,
        "DROP_TOP_SYMBOL_PNL": drop_top,
        "top_day_contribution": (top_day_pnl / total) if abs(total) > 1e-12 and best_d else None,
        "top_symbol": top_sym,
        "top_symbol_contribution": (top_sym_pnl / total) if abs(total) > 1e-12 and top_sym else None,
        "exit_reasons": dict(reasons),
        "avg_hold_sec": _mean(hold_vals),
    }


def coverage_ok(p: dict[str, Any]) -> bool:
    return (
        int(p.get("TRADE_N") or 0) >= int(MIN_TOTAL_TRADES)
        and int(p.get("TRADING_DAY_WITH_FILL_N") or 0) >= int(MIN_TRADING_DAYS_WITH_FILL)
        and float(p.get("trades_per_day") or 0.0) >= float(MIN_TRADES_PER_DAY)
    )


def economic_ok(p: dict[str, Any]) -> bool:
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
        and _f(p.get("DROP_TOP_SYMBOL_PNL")) is not None
        and float(p["DROP_TOP_SYMBOL_PNL"]) >= 0.0
        and (float(total) + float(dd)) > 0.0
    )


def score_of(p: dict[str, Any]) -> Optional[float]:
    n = int(p.get("TRADE_N") or 0)
    if n <= 0:
        return None
    a = float(p.get("TOTAL_PNL") or 0.0) / float(n)
    b = float(p.get("EX_BEST_DAY_PNL") or 0.0) / float(n)
    c = float(p.get("DROP_TOP_SYMBOL_PNL") or 0.0) / float(n)
    return float(min(a, b, c))


def _pf_sort_key(pf: Any) -> float:
    if pf is None:
        return -1e18
    if pf == float("inf"):
        return 1e18
    return float(pf)


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


def evaluate_candidate(
    cid: str,
    rows: list[dict[str, Any]],
    ctrl_rows: list[dict[str, Any]],
    *,
    days: list[str],
) -> dict[str, Any]:
    e, x, z = parse_cid(cid)
    order_rows = [r for r in rows if r.get("ORDERED")]
    occ = portfolio_replay(order_rows, wait_sec=float(WAIT_SEC), position_cap=int(POSITION_CAP))
    trades = list(occ.get("trades") or [])
    for t in trades:
        src = t.get("src") or {}
        if t.get("hold_sec") is None:
            t["hold_sec"] = src.get("hold_sec")
        t["mfe_yen"] = src.get("mfe_yen")
        t["mae_yen"] = src.get("mae_yen")
        t["profit_giveback"] = src.get("profit_giveback")
        t["loss_avoided"] = src.get("loss_avoided")
        t["MID_60"] = src.get("MID_60")
        t["MID_180"] = src.get("MID_180")
        t["MID_300"] = src.get("MID_300")
        t["spread0"] = src.get("spread0")
    ctrl_order = [r for r in ctrl_rows if r.get("ORDERED")]
    ctrl_occ = portfolio_replay(ctrl_order, wait_sec=float(WAIT_SEC), position_cap=int(POSITION_CAP))
    attr = attribution(list(ctrl_occ.get("trades") or []), trades)
    pack = pack_trades(trades, days=days)
    cov = coverage_ok(pack)
    eco = economic_ok(pack)
    gate = "PASS" if cov and eco else ("COVERAGE_FAIL" if not cov else "ECONOMIC_FAIL")
    sc = score_of(pack) if gate == "PASS" else None
    signal_n = len(rows)
    order_n = int(occ.get("admitted_n") or 0)
    fill_n = int(occ.get("fill_n") or 0)
    inc = set(attr.get("incremental_ids") or [])
    down_trades = []
    for t in trades:
        tid = f"{t.get('date')}|{t.get('symbol')}|{t.get('t0')}"
        if tid in inc:
            down_trades.append(t)
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
        "cap_blocked": occ.get("cap_blocked"),
        "same_symbol_blocked": occ.get("same_symbol_blocked"),
        "slot_release_n": int(occ.get("fill_n") or 0),
        "downstream_trade_n": len(down_trades),
        "downstream_pnl": float(sum(float(t.get("pnl_yen_100") or 0.0) for t in down_trades)),
        "direct_exit_effect": attr.get("DIRECT_EXIT_DELTA"),
        "slot_release_effect": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
        "gate": gate,
        "score": sc,
        "MID_60": _mean([t.get("MID_60") for t in trades]),
        "MID_180": _mean([t.get("MID_180") for t in trades]),
        "MID_300": _mean([t.get("MID_300") for t in trades]),
        "MFE": _mean([t.get("mfe_yen") for t in trades]),
        "MAE": _mean([t.get("mae_yen") for t in trades]),
        "spread0": _mean([t.get("spread0") for t in trades]),
        "avg_giveback": _mean([t.get("profit_giveback") for t in trades]),
        "avg_loss_avoided": _mean([t.get("loss_avoided") for t in trades]),
        **pack,
        "_trades": trades,
    }


def ranking_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id"),
        "entry_id": row.get("entry_id"),
        "execution_id": row.get("execution_id"),
        "exit_id": row.get("exit_id"),
        "signal_n": row.get("signal_n"),
        "fill_n": row.get("fill_n"),
        "trades/day": row.get("trades_per_day"),
        "trades_per_day": row.get("trades_per_day"),
        "pnl": row.get("TOTAL_PNL"),
        "PF": ("inf" if row.get("PF") == float("inf") else row.get("PF")),
        "MaxDD": row.get("MAXDD"),
        "positive_days": row.get("positive_day_n"),
        "negative_days": row.get("negative_day_n"),
        "EX_BEST": row.get("EX_BEST_DAY_PNL"),
        "DROP_TOP_SYMBOL": row.get("DROP_TOP_SYMBOL_PNL"),
        "direct_exit_effect": row.get("direct_exit_effect"),
        "slot_release_effect": row.get("slot_release_effect"),
        "gate": row.get("gate"),
        "score": row.get("score"),
        "TRADE_N": row.get("TRADE_N"),
    }


def lodo(rows_by: dict[str, list[dict[str, Any]]], ctrl_by: dict[str, list[dict[str, Any]]], winner_id: Optional[str]) -> dict[str, Any]:
    days = list(DEVELOPMENT_DAYS)
    fold_winners = []
    top3_n = 0
    for leave in days:
        keep = [d for d in days if d != leave]
        ranked = []
        for cid in candidate_ids():
            e, x, _z = parse_cid(cid)
            rs = [r for r in (rows_by.get(cid) or []) if str(r.get("date") or "") != leave]
            cs = [r for r in (ctrl_by.get(f"{e}_{x}") or []) if str(r.get("date") or "") != leave]
            ev = evaluate_candidate(cid, rs, cs, days=keep)
            ranked.append(ev)
        passed = rank_pass(ranked)
        win = passed[0]["candidate_id"] if passed else None
        top3 = [r["candidate_id"] for r in passed[:3]]
        fold_winners.append({"leave": leave, "winner": win, "top3": top3, "pass_n": len(passed)})
        if winner_id and winner_id in top3:
            top3_n += 1
    return {
        "folds": fold_winners,
        "TOP3_N": top3_n,
        "stable": bool(winner_id) and int(top3_n) >= int(LODO_TOP3_MIN),
    }


def decide(*, integrity: bool, pass_n: int, lodo_pack: dict[str, Any] | None, winner: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "CERTIFIED": False,
        "TRUE_OOS": False,
        "SIZING": False,
        "STRESS_OPENED": False,
        "FULL_STRATEGY_DEV_FROZEN": False,
    }
    if not integrity:
        return {**base, "CASE": "E", "VERDICT": CASE_E, "NEXT": "STOP. DO_NOT_INTERPRET_ECONOMICS."}
    if int(pass_n) <= 0 or winner is None:
        return {
            **base,
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": "Architecture redesign. Do not open Stress. Do not retune thresholds.",
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
        "NEXT": "Open 4-day REUSED_HISTORY_STRESS for this one frozen candidate only.",
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    w = dict(report.get("winner") or {})
    lodo_p = dict(report.get("lodo") or {})
    table = list(report.get("ranking") or [])
    return {
        "1_why_ENTRY_only_abandoned": "ENTRY quality depends on EXIT timing, holding path, execution price, and slot release. Sequential ENTRY-freeze-then-EXIT is withdrawn.",
        "2_strategy_unit": "ENTRY + EXECUTION + EXIT + CAP + SLOT RELEASE. Primary decision = Full Causal Portfolio Economics.",
        "3_ENTRY_candidate_n": 5,
        "4_Execution_candidate_n": 3,
        "5_EXIT_candidate_n": 5,
        "6_total_strategy_n": 75,
        "7_Development_days": list(DEVELOPMENT_DAYS),
        "8_burned_Holdout_read": False,
        "9_final_Stress_read": False,
        "10_Full_Causal_used": True,
        "11_candidate_coverage_pass_n": report.get("coverage_pass_n"),
        "12_economic_gate_pass_n": report.get("economic_pass_n"),
        "13_complete_ranking_table": table,
        "14_winner": w.get("candidate_id"),
        "15_winner_ENTRY": w.get("entry_name") or w.get("entry_id"),
        "16_winner_Execution": w.get("exec_name") or w.get("execution_id"),
        "17_winner_EXIT": w.get("exit_name") or w.get("exit_id"),
        "18_winner_trades": w.get("TRADE_N"),
        "19_winner_pnl": w.get("TOTAL_PNL"),
        "20_winner_PF": w.get("PF"),
        "21_winner_MaxDD": w.get("MAXDD"),
        "22_winner_day_signs": {
            "positive": w.get("positive_day_n"),
            "negative": w.get("negative_day_n"),
            "zero": w.get("zero_day_n"),
        },
        "23_winner_EX_BEST": w.get("EX_BEST_DAY_PNL"),
        "24_winner_DROP_TOP": w.get("DROP_TOP_SYMBOL_PNL"),
        "25_direct_EXIT_effect": w.get("direct_exit_effect"),
        "26_downstream_slot_effect": w.get("slot_release_effect"),
        "27_avg_hold_time": w.get("avg_hold_sec"),
        "28_exit_reason_distribution": w.get("exit_reasons"),
        "29_MID60_180_300_diagnostic": {"60": w.get("MID_60"), "180": w.get("MID_180"), "300": w.get("MID_300")},
        "30_MFE_MAE_diagnostic": {"MFE": w.get("MFE"), "MAE": w.get("MAE")},
        "31_LODO_winners": [f.get("winner") for f in (lodo_p.get("folds") or [])],
        "32_TOP3_N": lodo_p.get("TOP3_N"),
        "33_stable": lodo_p.get("stable"),
        "34_FULL_STRATEGY_DEV_FROZEN": bool(d.get("FULL_STRATEGY_DEV_FROZEN")),
        "35_Stress_opened": False,
        "36_verdict": d.get("VERDICT"),
        "37_next": d.get("NEXT"),
        "38_Sizing_ran": False,
        "39_Runtime_changed": False,
        "40_future_used": bool(int((report.get("leakage") or {}).get("FUTURE_DATA_N") or 0)),
        "41_MAX_RESEARCH_DATE": "20260807",
        "42_TRUE_OOS": False,
        "43_CERTIFIED": False,
        "44_submit_cancel_live": "0/0/0",
    }
