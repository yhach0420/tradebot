"""BG_CONT_VWAP profit-source RCA. Diagnostic labels may use future path. Not ENTRY features. No rule change."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.freeze_group_mechanism_definitions_v1 import (
    CHECKPOINT_MIN,
    FAVOR_BPS,
    ISOLATE_SYMBOL,
    LARGE_WINNER_BPS,
    X1_TAX_BPS,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _bps(exit_px: float, entry_px: float) -> float | None:
    if not _finite(exit_px) or not _finite(entry_px) or float(entry_px) == 0:
        return None
    return float((float(exit_px) / float(entry_px) - 1.0) * 10_000.0)


def _mean(xs: list[Any]) -> float | None:
    arr = np.asarray([float(x) for x in xs if x is not None and _finite(x)], dtype=float)
    if arr.size == 0:
        return None
    return float(np.mean(arr))


def _median(xs: list[Any]) -> float | None:
    arr = np.asarray([float(x) for x in xs if x is not None and _finite(x)], dtype=float)
    if arr.size == 0:
        return None
    return float(np.median(arr))


def _pf(x0: np.ndarray) -> float | None:
    pos = float(np.sum(x0[x0 > 0]))
    neg = float(-np.sum(x0[x0 < 0]))
    if neg <= 0:
        return None
    return pos / neg


def _dd(day_means: list[float]) -> float | None:
    if not day_means:
        return None
    arr = np.asarray(day_means, dtype=float)
    eq = np.cumsum(arr)
    return float(np.min(eq - np.maximum.accumulate(eq)))


def annotate_trade(t: dict[str, Any]) -> dict[str, Any]:
    px = t.get("x0_entry_open")
    fwd = list(t.get("fwd_bars") or [])
    hold = int(t.get("hold_min") or 0)
    x0 = float(t.get("x0_bps") or 0.0)
    if not _finite(px) or float(px) <= 0 or not fwd:
        t["rca_ok"] = False
        return t
    px = float(px)
    hold_i = min(max(hold, 0), len(fwd) - 1)
    mfe = mae = None
    mfe_i = mae_i = None
    above_entry = 0
    above_vw = 0
    for i, row in enumerate(fwd[: hold_i + 1]):
        hb = _bps(row[2], px)
        lb = _bps(row[3], px)
        if hb is not None and (mfe is None or hb > mfe):
            mfe, mfe_i = hb, i
        if lb is not None and (mae is None or lb < mae):
            mae, mae_i = lb, i
        if _finite(row[4]) and float(row[4]) > px:
            above_entry += 1
        if row[6] is True:
            above_vw += 1
    giveback = None if mfe is None else float(mfe) - x0
    cps = {}
    for m in CHECKPOINT_MIN:
        if m < len(fwd) and _finite(fwd[m][4]):
            cps[f"p{m}m_close_bps"] = _bps(fwd[m][4], px)
            cps[f"p{m}m_in_trade"] = m <= hold_i
        else:
            cps[f"p{m}m_close_bps"] = None
            cps[f"p{m}m_in_trade"] = False
    exit_close_bps = _bps(fwd[hold_i][4], px) if _finite(fwd[hold_i][4]) else None
    recovered = False
    recovered_above_vwap = False
    recovered_above_entry = False
    post_hi = None
    fill_i = min(hold_i + 1, len(fwd) - 1)
    for row in fwd[fill_i + 1 :]:
        cb = _bps(row[4], px) if _finite(row[4]) else None
        hb = _bps(row[2], px)
        if hb is not None and (post_hi is None or hb > post_hi):
            post_hi = hb
        if row[6] is True:
            recovered_above_vwap = True
        if cb is not None and cb > 0:
            recovered_above_entry = True
    recovered = bool(recovered_above_vwap or recovered_above_entry)
    if mfe is not None and mfe <= 0:
        give_cls = "NEVER_PROFITABLE"
    elif mfe is not None and mfe > 0 and x0 < 0:
        give_cls = "PROFITABLE_THEN_LOSS"
    elif mfe is not None and mfe > 0 and 0 <= x0 < FAVOR_BPS:
        give_cls = "PROFITABLE_THEN_SMALL_WIN"
    elif x0 >= LARGE_WINNER_BPS:
        give_cls = "LARGE_WINNER"
    elif x0 >= FAVOR_BPS:
        give_cls = "PROFITABLE_AND_RETAINED"
    else:
        give_cls = "OTHER"
    mfe_v = float(mfe or 0.0)
    mae_v = float(mae or 0.0)
    if x0 >= LARGE_WINNER_BPS:
        path_cls = "LARGE_WINNER"
    elif abs(mfe_v) < FAVOR_BPS and abs(mae_v) < FAVOR_BPS:
        path_cls = "STALL_NO_PROGRESS"
    elif mfe_i is not None and mfe_i <= 1 and mfe_v < FAVOR_BPS and mae_v <= -FAVOR_BPS:
        path_cls = "IMMEDIATE_FAILURE"
    elif (mfe_i is None or mfe_v < FAVOR_BPS) and mae_i is not None and mae_i <= 1 and mae_v <= -FAVOR_BPS:
        path_cls = "IMMEDIATE_FAILURE"
    elif mfe_v >= FAVOR_BPS and x0 < 0:
        path_cls = "SMALL_PROGRESS_THEN_FAILURE"
    elif x0 > 0 and above_vw >= 5:
        path_cls = "SUSTAINED_CONTINUATION"
    elif x0 > 0:
        path_cls = "SMALL_WINNER"
    else:
        path_cls = "OTHER_LOSS"
    t.update(
        {
            "rca_ok": True,
            "trade_mfe_bps": mfe,
            "trade_mae_bps": mae,
            "time_to_mfe_min": mfe_i,
            "time_to_mae_min": mae_i,
            "mfe_before_mae": bool(mfe_i is not None and mae_i is not None and mfe_i <= mae_i),
            "mae_before_mfe": bool(mfe_i is not None and mae_i is not None and mae_i < mfe_i),
            "time_above_entry_min": above_entry,
            "time_above_vwap_min": above_vw,
            "time_reclaim_to_vwap_loss_min": hold_i if t.get("exit_reason") == "vwap_loss" else None,
            "giveback_bps": giveback,
            "giveback_class": give_cls,
            "entry_path_class": path_cls,
            "exit_close_bps": exit_close_bps,
            "immediate_vwap_loss": bool(t.get("exit_reason") == "vwap_loss" and hold_i <= 1),
            "thesis_broken_at_exit": bool(t.get("exit_reason") == "vwap_loss"),
            "meaningful_mfe_before_exit": bool(mfe is not None and float(mfe) >= FAVOR_BPS),
            "recovered_after_exit": recovered,
            "recovered_above_vwap_after_exit": recovered_above_vwap,
            "recovered_above_entry_after_exit": recovered_above_entry,
            "post_exit_mfe_bps": post_hi,
            **cps,
        }
    )
    return t


def symbol_contribution(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by[str(t["symbol"])].append(t)
    total = float(sum(float(t["x0_bps"]) for t in trades))
    pos = float(sum(float(t["x0_bps"]) for t in trades if float(t["x0_bps"]) > 0))
    rows = []
    for sym, xs in sorted(by.items(), key=lambda kv: -sum(float(t["x0_bps"]) for t in kv[1])):
        x0 = np.asarray([float(t["x0_bps"]) for t in xs], dtype=float)
        by_day: dict[str, list[float]] = defaultdict(list)
        by_block: dict[str, list[float]] = defaultdict(list)
        for t in xs:
            by_day[str(t["date"])].append(float(t["x0_bps"]))
            if t.get("block"):
                by_block[str(t["block"])].append(float(t["x0_bps"]))
        gross = float(np.sum(x0))
        rows.append(
            {
                "symbol": sym,
                "trade_n": int(x0.size),
                "day_n": len(by_day),
                "X0_mean": float(np.mean(x0)),
                "X0_median": float(np.median(x0)),
                "PF": _pf(x0),
                "hit_rate": float(np.mean(x0 > 0)),
                "total_gross_contribution": gross,
                "share_of_total_gross_pnl": (gross / total) if total != 0 else None,
                "share_of_positive_pnl": (float(np.sum(x0[x0 > 0])) / pos) if pos > 0 else None,
                "D2": _mean(by_block.get("D2") or []),
                "D3": _mean(by_block.get("D3") or []),
                "D4": _mean(by_block.get("D4") or []),
                "max_DD": _dd([float(np.mean(vs)) for vs in by_day.values()]),
                "MFE": _mean([t.get("trade_mfe_bps") for t in xs]),
                "MAE": _mean([t.get("trade_mae_bps") for t in xs]),
                "is_8136": sym == ISOLATE_SYMBOL,
            }
        )
    return rows


def subset_econ(trades: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    if not trades:
        return {"label": label, "trade_n": 0, "mean_x0_bps": None}
    x0 = np.asarray([float(t["x0_bps"]) for t in trades], dtype=float)
    by_day: dict[str, list[float]] = defaultdict(list)
    by_block: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_day[str(t["date"])].append(float(t["x0_bps"]))
        if t.get("block"):
            by_block[str(t["block"])].append(float(t["x0_bps"]))
    return {
        "label": label,
        "trade_n": int(x0.size),
        "day_n": len(by_day),
        "symbol_n": len({t["symbol"] for t in trades}),
        "mean_x0_bps": float(np.mean(x0)),
        "median_x0_bps": float(np.median(x0)),
        "mean_x1_bps": float(np.mean(x0) - X1_TAX_BPS),
        "PF": _pf(x0),
        "hit_rate": float(np.mean(x0 > 0)),
        "max_DD": _dd([float(np.mean(vs)) for vs in by_day.values()]),
        "block_mean_x0": {k: float(np.mean(vs)) for k, vs in by_block.items()},
        "total_gross": float(np.sum(x0)),
        "no_8136_strategy": False,
        "diagnostic_only": True,
    }


def winner_concentration(trades: list[dict[str, Any]]) -> dict[str, Any]:
    x0 = np.asarray([float(t["x0_bps"]) for t in trades], dtype=float)
    n = int(x0.size)
    order = np.argsort(-x0)
    pos_mask = x0 > 0
    pos_sum = float(np.sum(x0[pos_mask]))
    total = float(np.sum(x0))
    mean0 = float(np.mean(x0))

    def drop(k: int) -> dict[str, Any]:
        k = min(max(int(k), 0), n)
        keep = np.ones(n, dtype=bool)
        keep[order[:k]] = False
        left = x0[keep]
        return {
            "removed_n": k,
            "remaining_n": int(left.size),
            "mean_x0_bps": float(np.mean(left)) if left.size else None,
            "median_x0_bps": float(np.median(left)) if left.size else None,
            "total_gross": float(np.sum(left)) if left.size else None,
        }

    def share_top_frac(frac: float) -> float | None:
        if pos_sum <= 0:
            return None
        k = max(1, int(np.ceil(frac * n)))
        top = x0[order[:k]]
        return float(np.sum(top[top > 0]) / pos_sum)

    k1 = max(1, int(np.ceil(0.01 * n)))
    k5 = max(1, int(np.ceil(0.05 * n)))
    k10 = max(1, int(np.ceil(0.10 * n)))
    return {
        "n": n,
        "mean_x0_bps": mean0,
        "total_gross": total,
        "positive_pnl": pos_sum,
        "drop_top_1_trade": drop(1),
        "drop_top_5_trades": drop(5),
        "drop_top_10_trades": drop(10),
        "drop_top_1pct": drop(k1),
        "drop_top_5pct": drop(k5),
        "share_positive_pnl_top_1pct": share_top_frac(0.01),
        "share_positive_pnl_top_5pct": share_top_frac(0.05),
        "share_positive_pnl_top_10pct": share_top_frac(0.10),
        "top_1pct_n": k1,
        "top_5pct_n": k5,
        "top_10pct_n": k10,
        "used_as_filter": False,
    }


def giveback_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    gb = [float(t["giveback_bps"]) for t in trades if t.get("giveback_bps") is not None]
    winners = [float(t["giveback_bps"]) for t in trades if t.get("giveback_bps") is not None and float(t["x0_bps"]) > 0]
    losers = [float(t["giveback_bps"]) for t in trades if t.get("giveback_bps") is not None and float(t["x0_bps"]) <= 0]
    cls: dict[str, int] = defaultdict(int)
    for t in trades:
        cls[str(t.get("giveback_class") or "OTHER")] += 1
    n = max(len(trades), 1)
    return {
        "fraction_never_profitable": cls.get("NEVER_PROFITABLE", 0) / n,
        "fraction_profitable_then_loss": cls.get("PROFITABLE_THEN_LOSS", 0) / n,
        "fraction_profitable_then_small_win": cls.get("PROFITABLE_THEN_SMALL_WIN", 0) / n,
        "fraction_profitable_and_retained": cls.get("PROFITABLE_AND_RETAINED", 0) / n,
        "fraction_large_winner": cls.get("LARGE_WINNER", 0) / n,
        "counts": dict(cls),
        "average_giveback": _mean(gb),
        "median_giveback": _median(gb),
        "winner_giveback": _mean(winners),
        "loser_giveback": _mean(losers),
        "n": len(trades),
    }


def exit_analysis(trades: list[dict[str, Any]]) -> dict[str, Any]:
    reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[str(t.get("exit_reason") or "")] += 1
    vwap = [t for t in trades if t.get("exit_reason") == "vwap_loss"]
    n_v = max(len(vwap), 1)
    protect = sum(1 for t in vwap if not t.get("meaningful_mfe_before_exit"))
    give = sum(1 for t in vwap if t.get("meaningful_mfe_before_exit") and float(t.get("x0_bps") or 0) < float(t.get("trade_mfe_bps") or 0) - FAVOR_BPS)
    rec = sum(1 for t in vwap if t.get("recovered_after_exit"))
    imm = sum(1 for t in vwap if t.get("immediate_vwap_loss"))
    return {
        "exit_reasons": dict(reasons),
        "vwap_loss_n": len(vwap),
        "session_close_n": reasons.get("session_flat", 0),
        "other_n": len(trades) - len(vwap) - reasons.get("session_flat", 0),
        "vwap_loss_thesis_already_broken": True,
        "vwap_loss_immediate_frac": imm / n_v,
        "vwap_loss_recovered_after_exit_frac": rec / n_v,
        "vwap_loss_had_meaningful_mfe_frac": sum(1 for t in vwap if t.get("meaningful_mfe_before_exit")) / n_v,
        "vwap_loss_protects_failed_continuation_n": protect,
        "vwap_loss_gives_back_useful_profit_n": give,
        "exit_not_changed": True,
    }


def cost_headroom(trades: list[dict[str, Any]]) -> dict[str, Any]:
    x0 = np.asarray([float(t["x0_bps"]) for t in trades], dtype=float)
    mean0 = float(np.mean(x0))
    return {
        "gross_mean_bps": mean0,
        "gross_median_bps": float(np.median(x0)),
        "break_even_execution_bps": mean0,
        "canonical_tax_bps": X1_TAX_BPS,
        "tax_not_lowered_to_promote": True,
        "share_profitable_at_0bps": float(np.mean(x0 > 0)),
        "share_profitable_at_2bps": float(np.mean(x0 > 2)),
        "share_profitable_at_4bps": float(np.mean(x0 > 4)),
        "share_profitable_at_6bps": float(np.mean(x0 > 6)),
        "share_profitable_at_8bps": float(np.mean(x0 > 8)),
        "p10": float(np.percentile(x0, 10)),
        "p25": float(np.percentile(x0, 25)),
        "p50": float(np.percentile(x0, 50)),
        "p75": float(np.percentile(x0, 75)),
        "p90": float(np.percentile(x0, 90)),
        "p95": float(np.percentile(x0, 95)),
        "p99": float(np.percentile(x0, 99)),
        "diagnostic_only": True,
    }


def block_evolution(trades: list[dict[str, Any]], membership: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by[str(t.get("block") or "")].append(t)
    out = []
    for blk in ("D2", "D3", "D4"):
        xs = by.get(blk) or []
        if not xs:
            out.append({"block": blk, "trade_n": 0})
            continue
        x0 = np.asarray([float(t["x0_bps"]) for t in xs], dtype=float)
        win = x0[x0 > 0]
        loss = x0[x0 <= 0]
        n8136 = sum(1 for t in xs if str(t["symbol"]) == ISOLATE_SYMBOL)
        members = sorted({sym for sym, mp in membership.items() if mp.get(blk) == "CONTINUATION"})
        traded = sorted({str(t["symbol"]) for t in xs})
        out.append(
            {
                "block": blk,
                "trade_n": int(x0.size),
                "symbol_n": len(traded),
                "mean_x0_bps": float(np.mean(x0)),
                "median_x0_bps": float(np.median(x0)),
                "hit_rate": float(np.mean(x0 > 0)),
                "mean_winner_bps": float(np.mean(win)) if win.size else None,
                "mean_loser_bps": float(np.mean(loss)) if loss.size else None,
                "n_8136": n8136,
                "share_8136_trades": n8136 / max(int(x0.size), 1),
                "share_8136_gross": (sum(float(t["x0_bps"]) for t in xs if str(t["symbol"]) == ISOLATE_SYMBOL) / float(np.sum(x0))) if float(np.sum(x0)) != 0 else None,
                "continuation_members_at_block": members,
                "traded_symbols": traded,
                "d4_specific_rule": False,
            }
        )
    return out


def group_membership_table(symbols: list[str], by_eval: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows = []
    for sym in symbols:
        d2 = (by_eval.get("D2") or {}).get(sym)
        d3 = (by_eval.get("D3") or {}).get(sym)
        d4 = (by_eval.get("D4") or {}).get(sym)
        labs = [d2, d3, d4]
        always = all(x == "CONTINUATION" for x in labs)
        entered_later = (d2 != "CONTINUATION") and ("CONTINUATION" in {d3, d4})
        left = (d2 == "CONTINUATION") and (d3 != "CONTINUATION" or d4 != "CONTINUATION")
        unstable = len({x for x in labs if x}) > 1
        if always:
            status = "always_CONTINUATION"
        elif entered_later:
            status = "entered_CONTINUATION_later"
        elif left:
            status = "left_CONTINUATION"
        elif unstable:
            status = "unstable_membership"
        else:
            status = "other"
        rows.append(
            {
                "symbol": sym,
                "D2": d2,
                "D3": d3,
                "D4": d4,
                "status": status,
                "full_discovery_label_used": False,
            }
        )
    return rows


def root_cause(trades: list[dict[str, Any]], *, win: dict[str, Any], sym_rows: list[dict[str, Any]], with8136: dict[str, Any], without8136: dict[str, Any]) -> dict[str, Any]:
    n = max(len(trades), 1)
    losers = [t for t in trades if float(t["x0_bps"]) <= 0]
    a_n = sum(1 for t in losers if (t.get("trade_mfe_bps") is None or float(t.get("trade_mfe_bps") or 0) < FAVOR_BPS))
    b_n = sum(1 for t in trades if t.get("meaningful_mfe_before_exit") and (float(t["x0_bps"]) < 0 or float(t.get("giveback_bps") or 0) >= FAVOR_BPS))
    a_frac = a_n / n
    b_frac = b_n / n
    never_mfe_loser_frac = a_n / max(len(losers), 1)
    top5 = float(win.get("share_positive_pnl_top_5pct") or 0)
    drop5 = (win.get("drop_top_5pct") or {}).get("mean_x0_bps")
    c_flag = bool(top5 >= 0.50 or (drop5 is not None and float(drop5) <= 0))
    row8136 = next((r for r in sym_rows if r.get("symbol") == ISOLATE_SYMBOL), {})
    d_share = float(row8136.get("share_of_total_gross_pnl") or 0)
    d_pos = float(row8136.get("share_of_positive_pnl") or 0)
    d_flag = bool(d_share >= 0.40 or d_pos >= 0.50)
    without_mean = without8136.get("mean_x0_bps")
    across = bool(without_mean is not None and float(without_mean) > 0)
    scores = {
        "A_ENTRY_INSUFFICIENT": float(never_mfe_loser_frac),
        "B_EXIT_GIVEBACK": float(b_frac),
        "C_WINNER_TAIL_DEPENDENCE": float(top5),
        "D_SYMBOL_CONCENTRATION": max(d_share, d_pos),
    }
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    strong = [k for k, v in ranked if (k == "A_ENTRY_INSUFFICIENT" and v >= 0.50) or (k == "B_EXIT_GIVEBACK" and v >= 0.40) or (k == "C_WINNER_TAIL_DEPENDENCE" and v >= 0.50) or (k == "D_SYMBOL_CONCENTRATION" and v >= 0.40)]
    if len(strong) >= 2:
        label = "E_MIXED"
        primary = strong[0]
    elif strong:
        label = strong[0]
        primary = strong[0]
    else:
        label = "E_MIXED"
        primary = ranked[0][0]
    vwap_give = sum(1 for t in trades if t.get("exit_reason") == "vwap_loss" and t.get("meaningful_mfe_before_exit"))
    vwap_protect = sum(1 for t in trades if t.get("exit_reason") == "vwap_loss" and not t.get("meaningful_mfe_before_exit"))
    if vwap_give > vwap_protect * 1.2:
        vwap_role = "give_back_useful_profit"
    elif vwap_protect > vwap_give * 1.2:
        vwap_role = "protect_from_failed_continuation"
    else:
        vwap_role = "mixed_protect_and_giveback"
    exit_justified = label in {"B_EXIT_GIVEBACK", "E_MIXED"} and ("B_EXIT_GIVEBACK" in strong or primary == "B_EXIT_GIVEBACK")
    return {
        "label": label,
        "primary": primary,
        "scores": scores,
        "strong_flags": strong,
        "A_loser_never_meaningful_mfe_n": a_n,
        "A_frac_of_all_trades": a_frac,
        "A_frac_of_losers": never_mfe_loser_frac,
        "B_giveback_n": b_n,
        "B_frac": b_frac,
        "C_top5pct_share_positive_pnl": top5,
        "C_mean_x0_without_top5pct": drop5,
        "C_flag": c_flag,
        "D_8136_share_total_gross": d_share,
        "D_8136_share_positive": d_pos,
        "D_flag": d_flag,
        "mechanism_exists_without_8136_gross_positive": across,
        "without_8136_mean_x0": without_mean,
        "with_8136_mean_x0": with8136.get("mean_x0_bps"),
        "vwap_loss_role": vwap_role,
        "exit_research_justified": bool(exit_justified),
        "v27_not_bolted": True,
        "no_8136_strategy_created": True,
        "top_winners_not_used_as_filter": True,
        "contribution_note": "A=losers with MFE<8bps; B=trades with MFE>=8 that give back >=8bps or finish negative; C=top 5% of trades by X0 share of positive PnL; D=8136 share of gross/positive PnL.",
    }


def path_class_table(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by[str(t.get("entry_path_class") or "OTHER")].append(t)
    rows = []
    n = max(len(trades), 1)
    for lab, xs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        x0 = np.asarray([float(t["x0_bps"]) for t in xs], dtype=float)
        rows.append(
            {
                "entry_path_class": lab,
                "n": len(xs),
                "frac": len(xs) / n,
                "mean_x0_bps": float(np.mean(x0)),
                "median_x0_bps": float(np.median(x0)),
                "used_as_entry_feature": False,
                "rca_future_path_ok": True,
            }
        )
    return rows
