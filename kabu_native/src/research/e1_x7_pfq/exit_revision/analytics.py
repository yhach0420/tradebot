"""Economics, mechanism efficacy, robustness for EXIT revision."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Optional

from . import KNOWN_GIVEBACK_N, MECH_GIVEBACK


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pass_tr = [t for t in trades if t.get("integrity_status") == "PASS" and t.get("net_pnl_yen") is not None]
    pnls = [float(t["net_pnl_yen"]) for t in pass_tr]
    day_pnl: dict[str, float] = defaultdict(float)
    sym_pnl: dict[str, float] = defaultdict(float)
    for t in pass_tr:
        day_pnl[t["day"]] += float(t["net_pnl_yen"])
        sym_pnl[t["symbol"]] += float(t["net_pnl_yen"])
    gains = sum(x for x in pnls if x > 0)
    losses = sum(-x for x in pnls if x < 0)
    wins = sum(1 for x in pnls if x > 0)
    # max drawdown on cumulative day-sorted trade sequence
    ordered = sorted(pass_tr, key=lambda x: (x["day"], float(x.get("entry_time") or 0), x["episode_id"]))
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for t in ordered:
        cum += float(t["net_pnl_yen"])
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    day_vals = list(day_pnl.values())
    return {
        "n_trades": len(pass_tr),
        "total_pnl_yen_100": sum(pnls) if pnls else 0.0,
        "profit_factor": (gains / losses) if losses > 1e-12 else None,
        "win_rate": (wins / len(pnls)) if pnls else None,
        "average_trade": (sum(pnls) / len(pnls)) if pnls else None,
        "median_trade": float(median(pnls)) if pnls else None,
        "max_drawdown": mdd,
        "positive_days": sum(1 for v in day_vals if v > 0),
        "negative_days": sum(1 for v in day_vals if v < 0),
        "n_days": len(day_vals),
        "daily_median_pnl": float(median(day_vals)) if day_vals else None,
        "best_trade": max(pnls) if pnls else None,
        "worst_trade": min(pnls) if pnls else None,
        "best_day": max(day_pnl.items(), key=lambda x: x[1])[0] if day_pnl else None,
        "worst_day": min(day_pnl.items(), key=lambda x: x[1])[0] if day_pnl else None,
        "day_pnl": dict(day_pnl),
        "symbol_pnl": dict(sym_pnl),
        "exit_reason_counts": dict(Counter(t.get("exit_reason") for t in pass_tr)),
    }


def concentration(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pass_tr = [t for t in trades if t.get("integrity_status") == "PASS" and t.get("net_pnl_yen") is not None]
    n = len(pass_tr)
    day_c = Counter(t["day"] for t in pass_tr)
    sym_c = Counter(t["symbol"] for t in pass_tr)
    pnls = sorted(pass_tr, key=lambda t: float(t["net_pnl_yen"]), reverse=True)
    top_trade = pnls[0] if pnls else None
    top_sym, _ = sym_c.most_common(1)[0] if sym_c else (None, 0)
    top_day, _ = day_c.most_common(1)[0] if day_c else (None, 0)
    total = sum(float(t["net_pnl_yen"]) for t in pass_tr)
    ex_top1_trade = total - float(top_trade["net_pnl_yen"]) if top_trade else None
    ex_top1_sym = sum(float(t["net_pnl_yen"]) for t in pass_tr if t["symbol"] != top_sym) if top_sym else None
    ex_top1_day = sum(float(t["net_pnl_yen"]) for t in pass_tr if t["day"] != top_day) if top_day else None
    lodo = {}
    for d in sorted(day_c):
        lodo[d] = sum(float(t["net_pnl_yen"]) for t in pass_tr if t["day"] != d)
    loso = {}
    for s in sorted(sym_c):
        loso[s] = sum(float(t["net_pnl_yen"]) for t in pass_tr if t["symbol"] != s)
    return {
        "max_day_share": (max(day_c.values()) / n) if n else None,
        "max_symbol_share": (max(sym_c.values()) / n) if n else None,
        "top_day": top_day,
        "top_symbol": top_sym,
        "top_trade_episode": top_trade["episode_id"] if top_trade else None,
        "ex_top1_trade_pnl": ex_top1_trade,
        "ex_top1_symbol_pnl": ex_top1_sym,
        "ex_top1_day_pnl": ex_top1_day,
        "leave_one_day_out": lodo,
        "leave_one_symbol_out": loso,
        "lodo_all_nonneg": all(v >= 0 for v in lodo.values()) if lodo else False,
        "day_counts": dict(day_c),
        "symbol_counts": dict(sym_c),
    }


def mechanism_efficacy(
    *,
    giveback_eids: list[str],
    baseline_by_eid: dict[str, dict[str, Any]],
    revision_by_eid: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    prevented = 0
    still_neg = 0
    gap = 0
    armed_n = 0
    floor_n = 0
    hard_before = 0
    for eid in giveback_eids:
        b = baseline_by_eid[eid]
        r = revision_by_eid[eid]
        b_net = float(b["net_bps"])
        r_net = float(r["net_bps"])
        armed = bool(r.get("profit_floor_armed"))
        floor = r.get("exit_reason") == "PLUS5_BREAKEVEN_FLOOR"
        hard = r.get("exit_reason") in {
            "RECLAIM_LEVEL_BREAK", "PULLBACK_LOW_BREAK", "HARD_STOP", "MAX_HOLD", "SESSION_END",
        }
        prevented_flag = bool(floor) and r_net > b_net
        still_flag = bool(floor) and r_net <= 0
        # gap: armed floor exit with realized strictly below 0 (not filled at exactly 0)
        gap_flag = bool(floor) and r_net < 0
        if hard and (not floor) and armed and r_net <= 0:
            hard_before += 1
        if armed:
            armed_n += 1
        if floor:
            floor_n += 1
        if prevented_flag:
            prevented += 1
        if still_flag:
            still_neg += 1
        if gap_flag:
            gap += 1
        rows.append({
            "baseline_episode_id": eid,
            "baseline_realized_net_bps": b_net,
            "baseline_exit_reason": b.get("exit_reason"),
            "revision_armed": armed,
            "revision_exit_reason": r.get("exit_reason"),
            "revision_realized_net_bps": r_net,
            "prevented_nonpositive_giveback": prevented_flag,
            "still_nonpositive_after_floor": still_flag,
            "hard_exit_before_floor": hard and armed and (not floor),
            "gap_through_floor": gap_flag,
        })
    return {
        "original_giveback_n": len(giveback_eids),
        "giveback_n_reproduced": len(giveback_eids) == KNOWN_GIVEBACK_N,
        "armed_n": armed_n,
        "floor_triggered_n": floor_n,
        "prevented_nonpositive_giveback_n": prevented,
        "still_nonpositive_after_floor_n": still_neg,
        "gap_through_floor_n": gap,
        "hard_exit_before_floor_n": hard_before,
        "mechanism_efficacy": (prevented / len(giveback_eids)) if giveback_eids else 0.0,
        "rows": rows,
    }


def side_effects(
    baseline_trades: list[dict[str, Any]],
    revision_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    b_by = {t["episode_id"]: t for t in baseline_trades if t.get("integrity_status") == "PASS"}
    r_by = {t["episode_id"]: t for t in revision_trades if t.get("integrity_status") == "PASS"}
    pos_base = [eid for eid, t in b_by.items() if float(t["net_bps"]) > 0]
    changed = 0
    pos_to_nonpos = 0
    reductions = []
    detail = []
    for eid in pos_base:
        b = b_by[eid]
        r = r_by[eid]
        bn, rn = float(b["net_pnl_yen"]), float(r["net_pnl_yen"])
        if abs(bn - rn) > 1e-9 or b.get("exit_reason") != r.get("exit_reason"):
            changed += 1
        if rn <= 0:
            pos_to_nonpos += 1
            detail.append({"episode_id": eid, "baseline_net_yen": bn, "revision_net_yen": rn})
        if rn < bn:
            reductions.append(bn - rn)
    return {
        "baseline_positive_trade_n": len(pos_base),
        "revision_changed_positive_trade_n": changed,
        "revision_positive_to_nonpositive_n": pos_to_nonpos,
        "revision_profit_reduction_total": sum(reductions) if reductions else 0.0,
        "revision_profit_reduction_median": float(median(reductions)) if reductions else None,
        "positive_to_nonpositive_details": detail,
    }


def mechanism_gate(mech: dict[str, Any], side: dict[str, Any], *, baseline_ok: bool, revision_ok: bool, ab_ok: bool) -> dict[str, Any]:
    checks = {
        "giveback_31_reproduced": bool(mech.get("giveback_n_reproduced")) and mech["original_giveback_n"] == KNOWN_GIVEBACK_N,
        "prevented_ge_16": mech["prevented_nonpositive_giveback_n"] >= 16,
        "positive_to_nonpositive_eq_0": side["revision_positive_to_nonpositive_n"] == 0,
        "baseline_identity_pass": baseline_ok,
        "revision_integrity_pass": revision_ok,
        "ab_determinism_pass": ab_ok,
    }
    return {"pass": all(checks.values()), "checks": checks}


def economic_gate(econ: dict[str, Any], conc: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "total_pnl_gt_0": (econ["total_pnl_yen_100"] or 0) > 0,
        "pf_ge_1_10": econ["profit_factor"] is not None and econ["profit_factor"] >= 1.10,
        "positive_days_ge_5": econ["positive_days"] >= 5,
        "daily_median_gt_0": econ["daily_median_pnl"] is not None and econ["daily_median_pnl"] > 0,
        "lodo_all_ge_0": bool(conc.get("lodo_all_nonneg")),
        "ex_top1_trade_gt_0": conc["ex_top1_trade_pnl"] is not None and conc["ex_top1_trade_pnl"] > 0,
        "ex_top1_symbol_gt_0": conc["ex_top1_symbol_pnl"] is not None and conc["ex_top1_symbol_pnl"] > 0,
        "ex_top1_day_ge_0": conc["ex_top1_day_pnl"] is not None and conc["ex_top1_day_pnl"] >= 0,
        "max_day_share_le_040": conc["max_day_share"] is not None and conc["max_day_share"] <= 0.40 + 1e-15,
        "max_symbol_share_le_030": conc["max_symbol_share"] is not None and conc["max_symbol_share"] <= 0.30 + 1e-15,
    }
    return {"pass": all(checks.values()), "checks": checks}
