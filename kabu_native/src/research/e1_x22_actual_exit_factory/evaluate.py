"""Pair metrics, baseline/rejected comparison, path-family, status, promotion bundle."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from . import ACTUAL_EXITS, DISCOVERY, EVALUATION, STRESS_DAY
from .exits import simulate_exit_on_path

REASON_CODES = (
    "hard_stop", "profit_target", "trailing_exit", "no_progress_exit",
    "max_hold_exit", "session_close",
)
REASON_TO_I = {r: i for i, r in enumerate(REASON_CODES)}


class ExitTradeMatrix:
    """Population-aligned arrays for one actual EXIT."""

    def __init__(self, n: int):
        self.valid = np.zeros(n, dtype=bool)
        self.pnl = np.full(n, np.nan)
        self.ret_bps = np.full(n, np.nan)
        self.hold = np.full(n, np.nan)
        self.reason = np.full(n, -1, dtype=np.int16)
        self.mfe = np.full(n, np.nan)
        self.mae = np.full(n, np.nan)
        self.entry_px = np.full(n, np.nan)
        self.exit_px = np.full(n, np.nan)
        self.entry_t = np.full(n, np.nan)
        self.exit_t = np.full(n, np.nan)


def precompute_all_exit_matrices(
    rows: list[dict[str, Any]],
    cache: dict[str, Any],
    *,
    use_disk: bool = True,
) -> dict[str, ExitTradeMatrix]:
    from pathlib import Path
    import pickle
    OUT = Path(__file__).resolve().parents[3] / "results" / "research" / "e1_x22_actual_exit_factory"
    OUT.mkdir(parents=True, exist_ok=True)
    pkl = OUT / "_exit_matrices.pkl"
    key = {"n": len(rows), "exits": list(ACTUAL_EXITS), "head": rows[0]["cluster_id"], "tail": rows[-1]["cluster_id"]}
    if use_disk and pkl.exists():
        with pkl.open("rb") as f:
            blob = pickle.load(f)
        if blob.get("key") == key:
            print(f"  loaded exit matrices from disk", flush=True)
            return blob["mats"]

    n = len(rows)
    out: dict[str, ExitTradeMatrix] = {eid: ExitTradeMatrix(n) for eid in ACTUAL_EXITS}
    for i, r in enumerate(rows):
        if (i + 1) % 2000 == 0 or i == 0:
            print(f"  exit sim {i+1}/{n}", flush=True)
        tarr = cache["times"][i]
        parr = cache["prices"][i]
        if tarr.size == 0 or r.get("CurrentPrice") is None:
            continue
        entry_epoch = float(r["grid_epoch"])
        entry_px = float(r["CurrentPrice"])
        for eid in ACTUAL_EXITS:
            tr = simulate_exit_on_path(
                exit_id=eid,
                entry_epoch=entry_epoch,
                entry_price=entry_px,
                date=r["date"],
                session=r["session"],
                times=tarr,
                prices=parr,
            )
            if tr is None:
                continue
            m = out[eid]
            m.valid[i] = True
            m.pnl[i] = tr["gross_reference_pnl_yen_100"]
            m.ret_bps[i] = tr["reference_return_bps"]
            m.hold[i] = tr["hold_sec"]
            m.reason[i] = REASON_TO_I.get(tr["exit_reason"], -1)
            m.mfe[i] = tr["MFE_at_exit_bps"]
            m.mae[i] = tr["MAE_at_exit_bps"]
            m.entry_px[i] = tr["entry_price"]
            m.exit_px[i] = tr["exit_price"]
            m.entry_t[i] = tr["entry_time_epoch"]
            m.exit_t[i] = tr["exit_time_epoch"]
    if use_disk:
        with pkl.open("wb") as f:
            pickle.dump({"key": key, "mats": out}, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"  wrote exit matrices {pkl}", flush=True)
    return out


def _period_mask(dates: np.ndarray, name: str) -> np.ndarray:
    if name == "DISCOVERY":
        return np.isin(dates, list(DISCOVERY))
    if name == "EVALUATION":
        return np.isin(dates, list(EVALUATION))
    if name == "STRESS_20260803":
        return dates == STRESS_DAY
    return np.ones(len(dates), dtype=bool)


def aggregate_matrix(
    mat: ExitTradeMatrix,
    entry_mask: np.ndarray,
    dates: np.ndarray,
    symbols: np.ndarray,
    period: str = "ALL",
) -> dict[str, Any]:
    pm = _period_mask(dates, period)
    sel = entry_mask & pm & mat.valid
    idx = np.where(sel)[0]
    n = int(idx.size)
    if n == 0:
        return {
            "trades": 0, "days": 0, "symbols": 0, "wins": 0, "losses": 0,
            "win_rate": None, "total_reference_pnl_yen_100": None,
            "avg_reference_pnl_yen_100": None, "median_reference_pnl_yen_100": None,
            "profit_factor_reference": None, "best_trade": None, "worst_trade": None,
            "max_drawdown_reference_yen_100": 0.0, "positive_days": 0, "negative_days": 0,
            "median_daily_reference_pnl": None, "avg_hold_sec": None, "median_hold_sec": None,
            "exit_reason_counts": {}, "avg_return_bps": None,
            "day_balanced_return_bps": None, "symbol_balanced_return_bps": None,
            "max_day_contribution": None, "max_symbol_contribution_bps": None,
        }
    pnls = mat.pnl[idx]
    rets = mat.ret_bps[idx]
    holds = mat.hold[idx]
    wins = int(np.sum(pnls > 0))
    losses = int(np.sum(pnls < 0))
    gp = float(np.sum(pnls[pnls > 0])) if wins else 0.0
    gl = float(abs(np.sum(pnls[pnls < 0]))) if losses else 0.0
    # drawdown chronological
    order = np.lexsort((idx, dates[idx]))
    ordered = pnls[order]
    cum = np.cumsum(ordered)
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.min(cum - peak))

    d = dates[idx]
    uniq_d, inv_d = np.unique(d, return_inverse=True)
    day_pnl = np.bincount(inv_d, weights=pnls)
    # day-balanced return: mean of per-day mean returns
    day_ret_sum = np.bincount(inv_d, weights=rets)
    day_ret_cnt = np.bincount(inv_d)
    day_means = day_ret_sum / np.maximum(day_ret_cnt, 1)

    s = symbols[idx]
    uniq_s, inv_s = np.unique(s, return_inverse=True)
    sym_ret_sum = np.bincount(inv_s, weights=rets)
    sym_ret_cnt = np.bincount(inv_s)
    sym_means = sym_ret_sum / np.maximum(sym_ret_cnt, 1)

    reasons = {}
    for code, name in enumerate(REASON_CODES):
        c = int(np.sum(mat.reason[idx] == code))
        if c:
            reasons[name] = c

    return {
        "trades": n,
        "days": int(uniq_d.size),
        "symbols": int(uniq_s.size),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n,
        "total_reference_pnl_yen_100": float(np.sum(pnls)),
        "avg_reference_pnl_yen_100": float(np.mean(pnls)),
        "median_reference_pnl_yen_100": float(np.median(pnls)),
        "profit_factor_reference": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else None),
        "best_trade": float(np.max(pnls)),
        "worst_trade": float(np.min(pnls)),
        "max_drawdown_reference_yen_100": max_dd,
        "positive_days": int(np.sum(day_pnl > 0)),
        "negative_days": int(np.sum(day_pnl < 0)),
        "median_daily_reference_pnl": float(np.median(day_pnl)),
        "avg_hold_sec": float(np.mean(holds)),
        "median_hold_sec": float(np.median(holds)),
        "exit_reason_counts": reasons,
        "avg_return_bps": float(np.mean(rets)),
        "day_balanced_return_bps": float(np.mean(day_means)),
        "symbol_balanced_return_bps": float(np.mean(sym_means)),
        "max_day_contribution": float(np.max(np.abs(day_pnl))),
        "max_symbol_contribution_bps": float(np.max(np.abs(sym_means))),
    }


def sample_trades(
    mat: ExitTradeMatrix,
    rows: list[dict[str, Any]],
    mask: np.ndarray,
    limit: int = 80,
) -> list[dict[str, Any]]:
    out = []
    for i in np.where(mask & mat.valid)[0]:
        r = rows[i]
        out.append({
            "date": r["date"], "session": r["session"], "symbol": r["symbol"],
            "cluster_id": r["cluster_id"],
            "entry_price": float(mat.entry_px[i]),
            "exit_price": float(mat.exit_px[i]),
            "entry_time": float(mat.entry_t[i]),
            "exit_time": float(mat.exit_t[i]),
            "exit_reason": REASON_CODES[mat.reason[i]] if mat.reason[i] >= 0 else "unknown",
            "hold_sec": float(mat.hold[i]),
            "MFE_at_exit_bps": float(mat.mfe[i]),
            "MAE_at_exit_bps": float(mat.mae[i]),
            "reference_return_bps": float(mat.ret_bps[i]),
            "gross_reference_pnl_yen_100": float(mat.pnl[i]),
        })
        if len(out) >= limit:
            break
    return out


def compare_to_baseline(pair: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    def d(a, b):
        if a is None or b is None:
            return None
        return a - b

    stop_p = (pair.get("exit_reason_counts") or {}).get("hard_stop", 0) / max(pair.get("trades") or 1, 1)
    stop_b = (base.get("exit_reason_counts") or {}).get("hard_stop", 0) / max(base.get("trades") or 1, 1)
    return {
        "avg_pnl_delta_vs_baseline": d(pair.get("avg_reference_pnl_yen_100"), base.get("avg_reference_pnl_yen_100")),
        "day_balanced_delta_vs_baseline": d(pair.get("day_balanced_return_bps"), base.get("day_balanced_return_bps")),
        "PF_delta_vs_baseline": d(pair.get("profit_factor_reference"), base.get("profit_factor_reference")),
        "worst_trade_delta_vs_baseline": d(pair.get("worst_trade"), base.get("worst_trade")),
        "max_dd_delta_vs_baseline": d(pair.get("max_drawdown_reference_yen_100"), base.get("max_drawdown_reference_yen_100")),
        "STOP_share_delta_vs_baseline": d(stop_p, stop_b),
    }


def classify_path_family(exit_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    scored = []
    for eid, m in exit_metrics.items():
        avg = m.get("avg_reference_pnl_yen_100")
        if avg is None:
            continue
        scored.append((avg, eid))
    if not scored:
        return {
            "post_entry_path_family": "EXIT_MIXED",
            "best_actual_exit": None,
            "second_best_actual_exit": None,
            "exit_rank_stability": "none",
        }
    scored.sort(reverse=True)
    best = scored[0][1]
    second = scored[1][1] if len(scored) > 1 else None
    signs = [1 if s > 0 else (-1 if s < 0 else 0) for s, _ in scored]
    mixed = len(set(signs) - {0}) > 1 and min(signs) < 0 < max(signs)

    cont_ids = ("EX_CONTINUATION_TRAIL_V1", "EX_TRAIL_10_5_MAX300_V1")
    by_id = {e: s for s, e in scored}
    if mixed and (scored[0][0] - scored[-1][0]) > max(abs(scored[0][0]), 1e-9) * 0.5:
        fam = "EXIT_MIXED"
    elif best == "EX_FAST_PROTECT_V1":
        cont = [by_id[e] for e in cont_ids if e in by_id]
        fam = "QUICK_MOVE" if (not cont or by_id["EX_FAST_PROTECT_V1"] >= max(cont)) else "QUICK_MOVE"
    elif best in cont_ids:
        fam = "CONTINUATION"
    elif best == "EX_ASYM_10_15_V1":
        fam = "ASYMMETRIC_TOLERANT"
    elif best == "EX_TIGHT_5_10_V1":
        fam = "TIGHT_RISK"
    elif best == "EX_TOUCH_10_10_MAX300":
        fam = "TOUCH_DEFINED"
    else:
        fam = "EXIT_MIXED"

    return {
        "post_entry_path_family": fam,
        "best_actual_exit": best,
        "second_best_actual_exit": second,
        "exit_rank_stability": "mixed" if mixed else "stable",
    }


def assign_pair_status(
    metrics_all: dict[str, Any],
    vs_base: dict[str, Any],
    period_metrics: dict[str, dict[str, Any]],
    path_fam: str,
) -> str:
    avg = metrics_all.get("avg_reference_pnl_yen_100")
    if avg is None or metrics_all.get("trades", 0) < 30:
        return "EXPERIMENTAL_ENTRY_CREATED"
    improved = any(
        (vs_base.get(k) is not None and vs_base[k] > 0)
        for k in (
            "avg_pnl_delta_vs_baseline",
            "PF_delta_vs_baseline",
            "worst_trade_delta_vs_baseline",
            "max_dd_delta_vs_baseline",
        )
    ) or (
        vs_base.get("STOP_share_delta_vs_baseline") is not None
        and vs_base["STOP_share_delta_vs_baseline"] < 0
    )
    disc = (period_metrics.get("DISCOVERY") or {}).get("avg_reference_pnl_yen_100")
    ev = (period_metrics.get("EVALUATION") or {}).get("avg_reference_pnl_yen_100")
    period_mixed = disc is not None and ev is not None and ((disc > 0) != (ev > 0))
    if path_fam == "EXIT_MIXED":
        return "EXIT_SENSITIVE"
    if period_mixed:
        return "PERIOD_MIXED"
    if improved and avg > 0:
        return "ENTRY_EXIT_PAIR_PROMISING"
    if avg > 0:
        return "REFERENCE_EXIT_PROMISING"
    return "REFERENCE_WEAK"


def build_promotion_bundle(
    pair_rows: list[dict[str, Any]],
    alias_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rep_ids = {a["candidate_id"] for a in alias_rows if a.get("is_representative")}
    eligible = []
    for p in pair_rows:
        if p["candidate_id"] not in rep_ids:
            continue
        m = p.get("metrics_ALL") or {}
        if (m.get("trades") or 0) < 300:
            continue
        if (m.get("days") or 0) < 7:
            continue
        ev = (p.get("period") or {}).get("EVALUATION") or {}
        if (ev.get("trades") or 0) < 50:
            continue
        reasons = m.get("exit_reason_counts") or {}
        if not reasons:
            continue
        vs = p.get("vs_baseline") or {}
        perf_ok = any(
            vs.get(k) is not None and vs[k] > 0
            for k in (
                "avg_pnl_delta_vs_baseline",
                "PF_delta_vs_baseline",
                "worst_trade_delta_vs_baseline",
                "max_dd_delta_vs_baseline",
            )
        ) or (
            vs.get("STOP_share_delta_vs_baseline") is not None
            and vs["STOP_share_delta_vs_baseline"] < 0
        )
        if not perf_ok:
            continue
        # prefer non-control exits for diversity, but allow control
        eligible.append(p)

    eligible.sort(key=lambda p: -((p.get("metrics_ALL") or {}).get("avg_reference_pnl_yen_100") or -1e18))
    bundle = []
    seen_mask_exit = set()
    fam_counts: dict[str, int] = defaultdict(int)
    path_counts: dict[str, int] = defaultdict(int)
    for p in eligible:
        key = (p.get("alias_representative_id") or p["candidate_id"], p["actual_exit_id"])
        if key in seen_mask_exit:
            continue
        pre = p.get("pre_entry_feature_family") or "OTHER"
        post = p.get("post_entry_path_family") or "OTHER"
        if fam_counts[pre] >= 12 and path_counts[post] >= 12 and len(bundle) >= 40:
            continue
        seen_mask_exit.add(key)
        fam_counts[pre] += 1
        path_counts[post] += 1
        bundle.append({
            "status": "PROMOTION_PAIR_BUNDLE_PROPOSAL",
            "pair_id": p["pair_id"],
            "candidate_id": p["candidate_id"],
            "actual_exit_id": p["actual_exit_id"],
            "pre_entry_feature_family": pre,
            "post_entry_path_family": post,
            "support": (p.get("metrics_ALL") or {}).get("trades"),
            "days": (p.get("metrics_ALL") or {}).get("days"),
            "avg_reference_pnl_yen_100": (p.get("metrics_ALL") or {}).get("avg_reference_pnl_yen_100"),
            "vs_baseline": p.get("vs_baseline"),
            "period_diagnostic": {
                "DISCOVERY_avg": ((p.get("period") or {}).get("DISCOVERY") or {}).get("avg_reference_pnl_yen_100"),
                "EVALUATION_avg": ((p.get("period") or {}).get("EVALUATION") or {}).get("avg_reference_pnl_yen_100"),
                "STRESS_avg": ((p.get("period") or {}).get("STRESS_20260803") or {}).get("avg_reference_pnl_yen_100"),
            },
            "precommit": "NOT_CREATED",
        })
        if len(bundle) >= 80:
            break
    return bundle


# Back-compat aliases used by older imports
def precompute_all_exit_trades(rows, cache):
    return precompute_all_exit_matrices(rows, cache)


def aggregate_trades(trades, entry_mask, dates, symbols, period="ALL"):
    if isinstance(trades, ExitTradeMatrix):
        return aggregate_matrix(trades, entry_mask, dates, symbols, period)
    raise TypeError("expected ExitTradeMatrix")
