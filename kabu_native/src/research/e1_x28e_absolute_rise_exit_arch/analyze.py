"""EXIT architecture metrics + selection rules."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import numpy as np

from . import ADDITIONAL_STRESS, EVALUATION


def summarize_exit(
    *,
    mat: dict[str, np.ndarray],
    mask: np.ndarray,
    dates: np.ndarray,
    symbols: np.ndarray,
) -> dict[str, Any]:
    idx = np.where(mask & mat["valid"])[0]
    n = int(idx.size)
    if n == 0:
        return {
            "trades": 0, "avg_return_bps": None, "median_return_bps": None,
            "profit_factor": None, "win_rate": None, "pnl_100share": None,
            "avg_mae": None, "avg_mfe": None, "mfe_capture_ratio": None,
            "giveback_from_mfe": None, "hard_stop_rate": None,
            "no_progress_rate": None, "trail_exit_rate": None,
            "session_or_maxhold_rate": None, "exit_reason_counts": {},
            "days": 0, "symbols": 0,
        }
    rets = mat["ret_bps"][idx]
    pnls = mat["pnl"][idx]
    mae = mat.get("mae_bps", np.full(n, np.nan))[idx] if "mae_bps" in mat else np.full(n, np.nan)
    mfe = mat.get("mfe_bps", np.full(n, np.nan))[idx] if "mfe_bps" in mat else np.full(n, np.nan)
    reasons = Counter(str(x) for x in mat["reason"][idx].tolist())
    gains = pnls[pnls > 0].sum()
    losses = np.abs(pnls[pnls < 0].sum())
    pf = float(gains / losses) if losses > 0 else None
    hard = sum(reasons.get(k, 0) for k in ("hard_stop", "stop_hit"))
    np_n = reasons.get("no_progress_exit", 0) + reasons.get("NO_PROGRESS", 0)
    trail = sum(reasons.get(k, 0) for k in ("trailing_exit", "trailing_mfe_exit", "TRAIL_GIVEBACK"))
    sess = sum(
        reasons.get(k, 0)
        for k in (
            "session_close", "max_hold_exit", "morning_session_close",
            "afternoon_session_close", "session_end", "MAX_HOLD", "SESSION_CLOSE",
        )
    )
    # MFE capture: exit_ret / mfe when mfe>0
    cap = []
    give = []
    for a, b in zip(rets, mfe):
        if b == b and b > 0:
            cap.append(float(a / b))
            give.append(float(b - a))
    return {
        "trades": n,
        "avg_return_bps": float(np.mean(rets)),
        "median_return_bps": float(np.median(rets)),
        "profit_factor": pf,
        "win_rate": float(np.mean(pnls > 0)),
        "pnl_100share": float(np.sum(pnls)),
        "avg_mae": float(np.nanmean(mae)) if np.isfinite(mae).any() else None,
        "avg_mfe": float(np.nanmean(mfe)) if np.isfinite(mfe).any() else None,
        "mfe_capture_ratio": float(np.mean(cap)) if cap else None,
        "giveback_from_mfe": float(np.mean(give)) if give else None,
        "hard_stop_rate": hard / n,
        "no_progress_rate": np_n / n,
        "trail_exit_rate": trail / n,
        "session_or_maxhold_rate": sess / n,
        "exit_reason_counts": dict(reasons),
        "days": int(np.unique(dates[idx]).size),
        "symbols": int(np.unique(symbols[idx]).size),
    }


def entry_delta_from_mat(mat, sel_mask, dates=None) -> Optional[float]:
    sel = sel_mask & mat["valid"]
    comp = (~sel_mask) & mat["valid"]
    if not sel.any() or not comp.any():
        return None
    return float(np.mean(mat["ret_bps"][sel]) - np.mean(mat["ret_bps"][comp]))


def block_abs(mat, sel_mask, dates, block_dates: tuple[str, ...]) -> Optional[float]:
    m = sel_mask & mat["valid"] & np.isin(dates, list(block_dates))
    if not m.any():
        return None
    return float(np.mean(mat["ret_bps"][m]))


def lodo_loso(mat, sel_mask, dates, symbols) -> dict[str, Any]:
    idx = np.where(sel_mask & mat["valid"])[0]
    if idx.size == 0:
        return {
            "max_day_contribution_share": None,
            "positive_LODO_count": 0, "negative_LODO_count": 0,
            "max_symbol_contribution_share": None,
            "positive_LOSO_count": 0, "negative_LOSO_count": 0,
            "without_285A": "NOT_PRESENT" if "285A" not in set(symbols[idx].tolist()) else None,
        }
    full = float(np.mean(mat["ret_bps"][idx]))
    days = sorted(set(dates[idx].tolist()))
    pos_d = neg_d = 0
    day_shares = []
    for day in days:
        keep = idx[dates[idx] != day]
        only = idx[dates[idx] == day]
        if only.size and abs(full) > 1e-12:
            w = only.size / idx.size
            day_shares.append(abs(w * float(np.mean(mat["ret_bps"][only])) / full))
        if keep.size:
            m = float(np.mean(mat["ret_bps"][keep]))
            if m > 0:
                pos_d += 1
            elif m < 0:
                neg_d += 1
    syms = sorted(set(symbols[idx].tolist()))
    pos_s = neg_s = 0
    sym_shares = []
    for sym in syms:
        keep = idx[symbols[idx] != sym]
        only = idx[symbols[idx] == sym]
        if only.size and abs(full) > 1e-12:
            w = only.size / idx.size
            sym_shares.append(abs(w * float(np.mean(mat["ret_bps"][only])) / full))
        if keep.size:
            m = float(np.mean(mat["ret_bps"][keep]))
            if m > 0:
                pos_s += 1
            elif m < 0:
                neg_s += 1
    present = set(syms)
    return {
        "max_day_contribution_share": max(day_shares) if day_shares else None,
        "positive_LODO_count": pos_d,
        "negative_LODO_count": neg_d,
        "max_symbol_contribution_share": max(sym_shares) if sym_shares else None,
        "positive_LOSO_count": pos_s,
        "negative_LOSO_count": neg_s,
        "without_285A": "NOT_PRESENT" if "285A" not in present else float(
            np.mean(mat["ret_bps"][idx[symbols[idx] != "285A"]])
        ) if (symbols[idx] != "285A").any() else None,
        "without_2354": (
            "NOT_PRESENT" if "2354" not in present
            else float(np.mean(mat["ret_bps"][idx[symbols[idx] != "2354"]]))
        ),
        "without_4052": (
            "NOT_PRESENT" if "4052" not in present
            else float(np.mean(mat["ret_bps"][idx[symbols[idx] != "4052"]]))
        ),
    }


def architecture_passes(metrics: dict[str, Any], entry_delta: Optional[float],
                        eval_abs: Optional[float], stress_abs: Optional[float],
                        dep: dict[str, Any]) -> bool:
    if (metrics.get("avg_return_bps") or 0) <= 0:
        return False
    pf = metrics.get("profit_factor")
    if pf is None or pf <= 1:
        return False
    if entry_delta is None or entry_delta <= 0:
        return False
    if eval_abs is None or eval_abs <= 0:
        return False
    if stress_abs is None or stress_abs <= 0:
        return False
    # severe concentration: day share > 0.85 or symbol > 0.5
    if (dep.get("max_day_contribution_share") or 0) > 0.85:
        return False
    if (dep.get("max_symbol_contribution_share") or 0) > 0.50:
        return False
    return True


def rank_architecture(cands: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    Priority: simpler > fewer cand-specific params > safer stop > stable > PnL last.
    complexity: family=1, pbv2=2, specific=3
    """
    if not cands:
        return None
    order = {"family": 1, "pbv2": 2, "specific": 3}

    def key(c):
        return (
            order.get(c["arch"], 9),
            -(c.get("narrower_stop_score") or 0),
            -(c.get("stability_score") or 0),
            -(c.get("metrics", {}).get("pnl_100share") or -1e18),
        )

    return sorted(cands, key=key)[0]
