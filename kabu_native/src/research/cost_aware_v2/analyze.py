"""Cost-Aware V2 stats, candidates, true walk-forward, decompositions."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Callable, Mapping, Optional, Sequence

from research.cost_aware_v2.dataset import TradeRow

KeepFn = Callable[[TradeRow], bool]

# Forward primary / unified final (must stay identical across reports + K_v2_final)
FINAL_CANDIDATE_ID = "H_board_ts"
FINAL_FEATURES = ["f_np_imb_chg_60"]
SECONDARY_CANDIDATE_ID = "I_price_board"
SECONDARY_FEATURES = ["f_chase", "f_near_high", "f_np_imb_chg_60"]

ORACLE_IDS = frozenset({"stop_only_drop", "np_only_drop"})
NON_DEPLOYABLE = frozenset({"runtime", "stop_only_drop", "np_only_drop"})


def _mean(xs: Sequence[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 6) if xs else None


def _median(xs: Sequence[float]) -> Optional[float]:
    return round(statistics.median(xs), 6) if xs else None


def _std(xs: Sequence[float]) -> Optional[float]:
    return round(statistics.pstdev(xs), 6) if len(xs) > 1 else (0.0 if xs else None)


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(y for y in yens if y > 0)
    gl = abs(sum(y for y in yens if y < 0))
    if gl > 1e-12:
        return round(gp / gl, 4)
    if gp > 0:
        return 999.0
    return None


def _cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    pooled = math.sqrt((sa * sa + sb * sb) / 2.0) if (sa + sb) > 0 else 0.0
    if pooled < 1e-12:
        return 0.0
    return round((ma - mb) / pooled, 4)


def univariate(trades: Sequence[TradeRow], key: str) -> dict[str, Any]:
    vals = [(t, t.features.get(key)) for t in trades if t.features.get(key) is not None]
    miss = 1.0 - (len(vals) / max(1, len(trades)))
    if len(vals) < 8:
        return {"feature": key, "n": len(vals), "missing_rate": round(miss, 4), "usable": False}

    def split(pred):
        pos = [float(v) for t, v in vals if pred(t)]
        neg = [float(v) for t, v in vals if not pred(t)]
        return pos, neg

    w_pos, w_neg = split(lambda t: t.is_winner)
    s_pos, s_neg = split(lambda t: t.is_stop)
    n_pos, n_neg = split(lambda t: t.is_np)
    allv = [float(v) for _, v in vals]
    return {
        "feature": key,
        "n": len(vals),
        "missing_rate": round(miss, 4),
        "usable": miss <= 0.55 and len(vals) >= 30,
        "mean": _mean(allv),
        "median": _median(allv),
        "std": _std(allv),
        "winner_d": _cohens_d(w_pos, w_neg),
        "stop_d": _cohens_d(s_pos, s_neg),
        "np_d": _cohens_d(n_pos, n_neg),
        "winner_mean_pos": _mean(w_pos),
        "winner_mean_neg": _mean(w_neg),
        "stop_mean_pos": _mean(s_pos),
        "stop_mean_neg": _mean(s_neg),
        "np_mean_pos": _mean(n_pos),
        "np_mean_neg": _mean(n_neg),
    }


def evaluate_policy(trades: Sequence[TradeRow], *, keep_fn: KeepFn) -> dict[str, Any]:
    kept = [t for t in trades if keep_fn(t)]
    rejected = [t for t in trades if not keep_fn(t)]
    y5 = [t.pnl_5bps for t in kept]
    ry5 = [t.pnl_5bps for t in trades]
    y = [t.pnl_yen for t in kept]
    ry = [t.pnl_yen for t in trades]
    by_day: dict[str, list[float]] = defaultdict(list)
    by_day_base: dict[str, list[float]] = defaultdict(list)
    for t in kept:
        by_day[t.day].append(t.pnl_5bps)
    for t in trades:
        by_day_base[t.day].append(t.pnl_5bps)
    day_deltas = [
        round(sum(by_day.get(d, [])) - sum(by_day_base[d]), 2) for d in sorted(by_day_base)
    ]
    cost_rejected = round(sum(t.pnl_yen - t.pnl_5bps for t in rejected), 2)
    return {
        "n_trades": len(kept),
        "n_reject": len(rejected),
        "raw_pnl": round(sum(y), 2) if y else 0.0,
        "pnl_5bps": round(sum(y5), 2) if y5 else 0.0,
        "runtime_raw": round(sum(ry), 2),
        "runtime_5bps": round(sum(ry5), 2),
        "delta_raw": round(sum(y) - sum(ry), 2),
        "delta_5bps": round(sum(y5) - sum(ry5), 2),
        "cost_savings_from_reject": cost_rejected,
        "pf": _pf(y5),
        "runtime_pf": _pf(ry5),
        "win_rate": round(sum(1 for t in kept if t.is_winner) / len(kept), 4) if kept else None,
        "winners": sum(1 for t in kept if t.is_winner),
        "stops": sum(1 for t in kept if t.is_stop),
        "no_progress": sum(1 for t in kept if t.is_np),
        "runtime_winners": sum(1 for t in trades if t.is_winner),
        "runtime_stops": sum(1 for t in trades if t.is_stop),
        "runtime_np": sum(1 for t in trades if t.is_np),
        "winner_sacrifice": sum(1 for t in rejected if t.is_winner),
        "loser_reject": sum(1 for t in rejected if t.pnl_yen < 0),
        "stop_avoided": sum(1 for t in rejected if t.is_stop),
        "np_avoided": sum(1 for t in rejected if t.is_np),
        "avg_pnl_5bps": _mean(y5),
        "median_pnl_5bps": _median(y5),
        "max_dd_proxy": round(min(day_deltas), 2) if day_deltas else None,
        "improve_days": sum(1 for x in day_deltas if x > 0),
        "worsen_days": sum(1 for x in day_deltas if x < 0),
        "day_delta_median": _median(day_deltas),
        "best_trade": max(y5) if y5 else None,
        "worst_trade": min(y5) if y5 else None,
    }


def fit_thresholds(trades: Sequence[TradeRow]) -> dict[str, Optional[float]]:
    """Fit reject thresholds on the provided train set only."""

    def thr(key: str, side: str, q: float = 0.8) -> Optional[float]:
        xs = [float(t.features[key]) for t in trades if t.features.get(key) is not None]
        if len(xs) < 20:
            return None
        xs.sort()
        if side == "high":
            i = int(max(0, min(len(xs) - 1, round(q * (len(xs) - 1)))))
        else:
            i = int(max(0, min(len(xs) - 1, round((1 - q) * (len(xs) - 1)))))
        return xs[i]

    return {
        "t_chase": thr("f_chase", "high", 0.85),
        "t_near": thr("f_near_high", "high", 0.85),
        "t_mom_lo": thr("f_mom", "low", 0.8),
        "t_imb_chg": thr("f_np_imb_chg_60", "low", 0.8),
        "t_ret60_neg": thr("f_np_ret_60", "low", 0.8),
        "t_bounce_hi": thr("f_bounce", "high", 0.8),
        "t_w54": thr("f_w54_stop_risk", "high", 0.95),
    }


def make_keep_fn(policy_id: str, thr: Mapping[str, Optional[float]]) -> KeepFn:
    t_chase = thr.get("t_chase")
    t_near = thr.get("t_near")
    t_mom_lo = thr.get("t_mom_lo")
    t_imb_chg = thr.get("t_imb_chg")
    t_ret60_neg = thr.get("t_ret60_neg")
    t_bounce_hi = thr.get("t_bounce_hi")
    t_w54 = thr.get("t_w54")

    def has(t: TradeRow, k: str) -> bool:
        return t.features.get(k) is not None

    def rej_stop_only(t: TradeRow) -> bool:
        if t_chase is not None and has(t, "f_chase") and float(t.features["f_chase"]) >= t_chase:
            return False
        if t_near is not None and has(t, "f_near_high") and float(t.features["f_near_high"]) >= t_near:
            return False
        return True

    def rej_np_only(t: TradeRow) -> bool:
        if t_mom_lo is not None and has(t, "f_mom") and float(t.features["f_mom"]) <= t_mom_lo:
            if t_imb_chg is not None and has(t, "f_np_imb_chg_60") and float(t.features["f_np_imb_chg_60"]) <= t_imb_chg:
                return False
            if not has(t, "f_np_imb_chg_60") and t_ret60_neg is not None and has(t, "f_np_ret_60"):
                if float(t.features["f_np_ret_60"]) <= t_ret60_neg:
                    return False
        return True

    def rej_winner_pref(t: TradeRow) -> bool:
        weak_mom = t_mom_lo is not None and has(t, "f_mom") and float(t.features["f_mom"]) <= t_mom_lo
        weak_bounce = (
            t_bounce_hi is not None and has(t, "f_bounce") and float(t.features["f_bounce"]) < t_bounce_hi
        )
        high_chase = t_chase is not None and has(t, "f_chase") and float(t.features["f_chase"]) >= t_chase
        if weak_mom and weak_bounce and high_chase:
            return False
        return True

    def board_ts_only(t: TradeRow) -> bool:
        if not has(t, "f_np_imb_chg_60"):
            return True  # fail-open
        if t_imb_chg is not None and float(t.features["f_np_imb_chg_60"]) <= t_imb_chg:
            return False
        return True

    def rej_all(t: TradeRow) -> bool:
        return rej_winner_pref(t) and rej_stop_only(t) and rej_np_only(t)

    def pbv2_complement(t: TradeRow) -> bool:
        pb = t.features.get("f_pbv2")
        if pb is None:
            return rej_all(t)
        if float(pb) >= 6:
            return True
        if float(pb) <= 2:
            return rej_stop_only(t)
        return rej_all(t)

    def old_w54_proxy(t: TradeRow) -> bool:
        if t_w54 is not None and has(t, "f_w54_stop_risk") and float(t.features["f_w54_stop_risk"]) >= t_w54:
            return False
        return True

    # K_v2_final MUST equal H_board_ts (unified final / Forward primary)
    mapping: dict[str, KeepFn] = {
        "runtime": lambda t: True,
        "A_winner": rej_winner_pref,
        "B_stop": rej_stop_only,
        "C_np": rej_np_only,
        "D_win_stop": lambda t: rej_winner_pref(t) and rej_stop_only(t),
        "E_win_np": lambda t: rej_winner_pref(t) and rej_np_only(t),
        "F_stop_np": lambda t: rej_stop_only(t) and rej_np_only(t),
        "G_all": rej_all,
        "H_board_ts": board_ts_only,
        "I_price_board": lambda t: rej_stop_only(t) and board_ts_only(t),
        "J_pbv2_comp": pbv2_complement,
        "K_v2_final": board_ts_only,  # == H_board_ts
        "old_w54_proxy": old_w54_proxy,
        "stop_only_drop": lambda t: not t.is_stop,
        "np_only_drop": lambda t: not t.is_np,
    }
    if policy_id not in mapping:
        raise KeyError(f"unknown policy {policy_id}")
    return mapping[policy_id]


def build_keep_fns(
    trades: Sequence[TradeRow],
) -> tuple[dict[str, tuple[str, KeepFn]], dict[str, Any], list[dict]]:
    thr = fit_thresholds(trades)
    feat_keys = sorted({k for t in trades for k in t.features.keys()})
    uni_rows = [univariate(trades, k) for k in feat_keys]

    names = {
        "runtime": "Runtime PBv2",
        "A_winner": "Winner重視 reject",
        "B_stop": "STOP回避 reject (chase/near)",
        "C_np": "NoProgress回避 reject",
        "D_win_stop": "Winner+STOP",
        "E_win_np": "Winner+NoProgress",
        "F_stop_np": "STOP+NoProgress",
        "G_all": "Winner+STOP+NP",
        "H_board_ts": "板時系列のみ (Forward primary)",
        "I_price_board": "価格+板時系列 (secondary / large-reject)",
        "J_pbv2_comp": "PBv2補完型",
        "K_v2_final": "Cost-Aware V2 Final (== H_board_ts)",
        "old_w54_proxy": "旧W54 stop_risk proxy",
        "stop_only_drop": "ORACLE STOP_only drop",
        "np_only_drop": "ORACLE NP_only drop",
    }
    fns: dict[str, tuple[str, KeepFn]] = {
        cid: (names[cid], make_keep_fn(cid, thr)) for cid in names
    }
    return fns, thr, uni_rows


def fixed_threshold_by_day(trades: Sequence[TradeRow], keep_fn: KeepFn) -> dict[str, Any]:
    """In-sample day splits using ONE fixed keep_fn (NOT true walk-forward)."""
    days = sorted({t.day for t in trades})
    deltas = []
    for d in days:
        test = [t for t in trades if t.day == d]
        if len(test) < 5:
            continue
        base = sum(t.pnl_5bps for t in test)
        kept = sum(t.pnl_5bps for t in test if keep_fn(t))
        deltas.append({"day": d, "delta_5bps": round(kept - base, 2), "n": len(test)})
    xs = [x["delta_5bps"] for x in deltas]
    return {
        "method": "fixed_threshold_by_day",
        "note": "NOT walk-forward; thresholds fitted on full sample",
        "folds": deltas,
        "median_delta": _median(xs),
        "mean_delta": _mean(xs),
        "pos_folds": sum(1 for x in xs if x > 0),
        "neg_folds": sum(1 for x in xs if x < 0),
    }


def _has_board_feat(t: TradeRow) -> bool:
    return t.features.get("f_np_imb_chg_60") is not None


def leave_one_day_out(
    trades: Sequence[TradeRow],
    *,
    policy_id: str,
    min_test_n: int = 5,
    min_train_n: int = 40,
) -> dict[str, Any]:
    """Leave-one-day-out: fit on all other days (may include future days; NOT chronological)."""
    days = sorted({t.day for t in trades})
    folds = []
    for d in days:
        train = [t for t in trades if t.day != d]
        test = [t for t in trades if t.day == d]
        if len(test) < min_test_n or len(train) < min_train_n:
            continue
        thr = fit_thresholds(train)
        keep_fn = make_keep_fn(policy_id, thr)
        base = sum(t.pnl_5bps for t in test)
        kept_rows = [t for t in test if keep_fn(t)]
        kept = sum(t.pnl_5bps for t in kept_rows)
        rejected = [t for t in test if not keep_fn(t)]
        folds.append(
            {
                "day": d,
                "n_train": len(train),
                "n_test": len(test),
                "n_kept": len(kept_rows),
                "n_reject": len(rejected),
                "delta_5bps": round(kept - base, 2),
                "winner_sacrifice": sum(1 for t in rejected if t.is_winner),
                "stop_avoided": sum(1 for t in rejected if t.is_stop),
                "np_avoided": sum(1 for t in rejected if t.is_np),
                "thresholds": {k: thr.get(k) for k in ("t_imb_chg", "t_chase", "t_near")},
            }
        )
    xs = [x["delta_5bps"] for x in folds]
    return {
        "method": "leave_one_day_out",
        "note": "Train may include future calendar days relative to eval day; not chronological OOS",
        "policy": policy_id,
        "folds": folds,
        "median_delta": _median(xs),
        "mean_delta": _mean(xs),
        "pos_folds": sum(1 for x in xs if x > 0),
        "neg_folds": sum(1 for x in xs if x < 0),
        "n_folds": len(folds),
    }


# Minimum past trades with f_np_imb_chg_60 required to fit H_board_ts threshold.
MIN_BOARD_TRAIN_N = 20


def fit_thresholds_h_board(train_board: Sequence[TradeRow]) -> dict[str, Optional[float]]:
    """Fit H_board_ts threshold using only rows that have f_np_imb_chg_60."""
    thr = fit_thresholds(train_board)
    # Recompute t_imb_chg strictly on board-present rows (fit_thresholds already does, but guard).
    xs = [float(t.features["f_np_imb_chg_60"]) for t in train_board if _has_board_feat(t)]
    if len(xs) < MIN_BOARD_TRAIN_N:
        thr["t_imb_chg"] = None
        return thr
    xs.sort()
    # low threshold = ~20th percentile (same as fit_thresholds side=low q=0.8)
    i_lo = int(max(0, min(len(xs) - 1, round((1 - 0.8) * (len(xs) - 1)))))
    thr["t_imb_chg"] = xs[i_lo]
    return thr


def chronological_walk_forward(
    trades: Sequence[TradeRow],
    *,
    policy_id: str = "H_board_ts",
    min_test_n: int = 1,
    min_board_train_n: int = MIN_BOARD_TRAIN_N,
) -> dict[str, Any]:
    """Chronological walk-forward: train only on calendar days strictly before eval day.

    For H_board_ts:
    - thresholds fitted only on past rows with f_np_imb_chg_60
    - insufficient past board history → INSUFFICIENT_BOARD_TRAIN_HISTORY (not fail-open Δ=0)
    - eval day with no board features → FAIL_OPEN (excluded from stability pos/neg counts)
    """
    days = sorted({t.day for t in trades})
    folds: list[dict[str, Any]] = []
    n_fail_open = 0
    n_board_warmup = 0
    n_insufficient = 0
    n_oos_evaluable = 0

    for d in days:
        test = [t for t in trades if t.day == d]
        if len(test) < min_test_n:
            continue
        train_all = [t for t in trades if t.day < d]  # no future days
        train_board = [t for t in train_all if _has_board_feat(t)]
        test_board = [t for t in test if _has_board_feat(t)]

        # Days with no active board feature on eval → runtime fail-open (policy keeps all)
        if policy_id == "H_board_ts" and not test_board:
            n_fail_open += 1
            folds.append(
                {
                    "day": d,
                    "status": "FAIL_OPEN",
                    "n_train_all": len(train_all),
                    "n_train_board": len(train_board),
                    "n_test": len(test),
                    "n_test_board": 0,
                    "delta_5bps": None,
                    "counts_toward_stability": False,
                    "note": "no f_np_imb_chg_60 on eval day; fail-open keep-all; not a stability fold",
                }
            )
            continue

        if policy_id == "H_board_ts":
            if len(train_board) < min_board_train_n:
                n_insufficient += 1
                # First board day with no usable past board history = board warmup
                is_warmup = len(train_board) == 0
                if is_warmup:
                    n_board_warmup += 1
                status = "INSUFFICIENT_BOARD_TRAIN_HISTORY"
                folds.append(
                    {
                        "day": d,
                        "status": status,
                        "board_warmup": is_warmup,
                        "n_train_all": len(train_all),
                        "n_train_board": len(train_board),
                        "n_test": len(test),
                        "n_test_board": len(test_board),
                        "delta_5bps": None,
                        "counts_toward_stability": False,
                        "verdict": status,
                        "note": (
                            "board warmup / learning history insufficient"
                            if is_warmup
                            else f"need>={min_board_train_n} past board rows"
                        ),
                    }
                )
                continue

            thr = fit_thresholds_h_board(train_board)
            if thr.get("t_imb_chg") is None:
                n_insufficient += 1
                folds.append(
                    {
                        "day": d,
                        "status": "INSUFFICIENT_BOARD_TRAIN_HISTORY",
                        "board_warmup": False,
                        "n_train_board": len(train_board),
                        "n_test": len(test),
                        "delta_5bps": None,
                        "counts_toward_stability": False,
                        "verdict": "INSUFFICIENT_BOARD_TRAIN_HISTORY",
                    }
                )
                continue

            keep_fn = make_keep_fn("H_board_ts", thr)
            base = sum(t.pnl_5bps for t in test)
            kept_rows = [t for t in test if keep_fn(t)]
            rejected = [t for t in test if not keep_fn(t)]
            delta = round(sum(t.pnl_5bps for t in kept_rows) - base, 2)
            n_oos_evaluable += 1
            folds.append(
                {
                    "day": d,
                    "status": "OOS_EVALUABLE",
                    "n_train_all": len(train_all),
                    "n_train_board": len(train_board),
                    "n_test": len(test),
                    "n_test_board": len(test_board),
                    "n_kept": len(kept_rows),
                    "n_reject": len(rejected),
                    "delta_5bps": delta,
                    "winner_sacrifice": sum(1 for t in rejected if t.is_winner),
                    "stop_avoided": sum(1 for t in rejected if t.is_stop),
                    "np_avoided": sum(1 for t in rejected if t.is_np),
                    "t_imb_chg": thr.get("t_imb_chg"),
                    "counts_toward_stability": True,
                    "verdict": "OOS_EVALUABLE",
                }
            )
            continue

        # Generic chronological for other policies (past days only; all features)
        if len(train_all) < 40:
            folds.append(
                {
                    "day": d,
                    "status": "INSUFFICIENT_TRAIN",
                    "n_train_all": len(train_all),
                    "n_test": len(test),
                    "delta_5bps": None,
                    "counts_toward_stability": False,
                }
            )
            continue
        thr = fit_thresholds(train_all)
        keep_fn = make_keep_fn(policy_id, thr)
        base = sum(t.pnl_5bps for t in test)
        kept_rows = [t for t in test if keep_fn(t)]
        rejected = [t for t in test if not keep_fn(t)]
        delta = round(sum(t.pnl_5bps for t in kept_rows) - base, 2)
        n_oos_evaluable += 1
        folds.append(
            {
                "day": d,
                "status": "OOS_EVALUABLE",
                "n_train_all": len(train_all),
                "n_test": len(test),
                "n_kept": len(kept_rows),
                "n_reject": len(rejected),
                "delta_5bps": delta,
                "counts_toward_stability": True,
            }
        )

    oos = [f for f in folds if f.get("counts_toward_stability")]
    xs = [f["delta_5bps"] for f in oos if f.get("delta_5bps") is not None]
    return {
        "method": "chronological_walk_forward",
        "policy": policy_id,
        "note": "Train uses only days strictly before eval day; no future leakage",
        "min_board_train_n": min_board_train_n,
        "folds": folds,
        "oos_folds": oos,
        "n_oos_evaluable": n_oos_evaluable,
        "n_fail_open": n_fail_open,
        "n_board_warmup": n_board_warmup,
        "n_insufficient_board_train": n_insufficient,
        "median_delta_oos": _median(xs),
        "mean_delta_oos": _mean(xs),
        "pos_folds_oos": sum(1 for x in xs if x > 0),
        "neg_folds_oos": sum(1 for x in xs if x < 0),
        "zero_folds_oos": sum(1 for x in xs if x == 0),
    }


def leave_one_symbol_out_true(
    trades: Sequence[TradeRow],
    *,
    policy_id: str,
    top_n: int = 15,
) -> dict[str, Any]:
    """Fit thresholds excluding each top symbol, evaluate on held-out symbol."""
    by_sym: dict[str, list[TradeRow]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    top = sorted(by_sym.items(), key=lambda kv: -len(kv[1]))[:top_n]
    folds = []
    for sym, _rows in top:
        train = [t for t in trades if t.symbol != sym]
        test = [t for t in trades if t.symbol == sym]
        if len(train) < 40 or len(test) < 3:
            continue
        thr = fit_thresholds(train)
        keep_fn = make_keep_fn(policy_id, thr)
        base = sum(t.pnl_5bps for t in test)
        kept = sum(t.pnl_5bps for t in test if keep_fn(t))
        folds.append({"symbol": sym, "n_test": len(test), "delta_5bps": round(kept - base, 2)})
    xs = [f["delta_5bps"] for f in folds]
    return {
        "method": "true_leave_one_symbol_out",
        "policy": policy_id,
        "folds": folds,
        "median_delta": _median(xs),
        "pos": sum(1 for x in xs if x > 0),
        "neg": sum(1 for x in xs if x < 0),
    }


def counterfactual(trades: Sequence[TradeRow], keep_fn: KeepFn) -> dict[str, Any]:
    rejected = [t for t in trades if not keep_fn(t)]
    kept = [t for t in trades if keep_fn(t)]
    return {
        "if_rejected_were_kept_pnl_5bps": round(sum(t.pnl_5bps for t in rejected), 2),
        "kept_only_pnl_5bps": round(sum(t.pnl_5bps for t in kept), 2),
        "rejected_winner_pnl": round(sum(t.pnl_5bps for t in rejected if t.is_winner), 2),
        "rejected_stop_pnl": round(sum(t.pnl_5bps for t in rejected if t.is_stop), 2),
        "rejected_np_pnl": round(sum(t.pnl_5bps for t in rejected if t.is_np), 2),
        "n_rejected": len(rejected),
        "n_kept": len(kept),
    }


def decompose_i_price_board(
    trades: Sequence[TradeRow],
    thr: Mapping[str, Optional[float]],
) -> dict[str, Any]:
    """Decompose I_price_board into chase/near, board-ts, combo incremental, 0bps, cost, selection."""
    fn_b = make_keep_fn("B_stop", thr)
    fn_h = make_keep_fn("H_board_ts", thr)
    fn_i = make_keep_fn("I_price_board", thr)
    m_b = evaluate_policy(trades, keep_fn=fn_b)
    m_h = evaluate_policy(trades, keep_fn=fn_h)
    m_i = evaluate_policy(trades, keep_fn=fn_i)
    m_rt = evaluate_policy(trades, keep_fn=lambda t: True)

    better_single = max(m_b["delta_5bps"], m_h["delta_5bps"])
    combo_incremental = round(m_i["delta_5bps"] - better_single, 2)

    return {
        "chase_near_only": {
            "policy": "B_stop",
            "features": ["f_chase", "f_near_high"],
            "delta_5bps": m_b["delta_5bps"],
            "delta_raw_0bps": m_b["delta_raw"],
            "cost_savings_from_reject": m_b["cost_savings_from_reject"],
            "n_reject": m_b["n_reject"],
            "winner_sacrifice": m_b["winner_sacrifice"],
            "pure_selection_delta_raw": m_b["delta_raw"],
        },
        "np_imb_chg_60_only": {
            "policy": "H_board_ts",
            "features": ["f_np_imb_chg_60"],
            "delta_5bps": m_h["delta_5bps"],
            "delta_raw_0bps": m_h["delta_raw"],
            "cost_savings_from_reject": m_h["cost_savings_from_reject"],
            "n_reject": m_h["n_reject"],
            "winner_sacrifice": m_h["winner_sacrifice"],
            "pure_selection_delta_raw": m_h["delta_raw"],
        },
        "both_combined": {
            "policy": "I_price_board",
            "features": SECONDARY_FEATURES,
            "delta_5bps": m_i["delta_5bps"],
            "delta_raw_0bps": m_i["delta_raw"],
            "cost_savings_from_reject": m_i["cost_savings_from_reject"],
            "n_reject": m_i["n_reject"],
            "winner_sacrifice": m_i["winner_sacrifice"],
            "pure_selection_delta_raw": m_i["delta_raw"],
        },
        "combo_incremental_vs_better_single_5bps": combo_incremental,
        "identity_check": {
            "note": "delta_5bps ≈ delta_raw_0bps + cost_savings_from_reject",
            "I_delta_5bps": m_i["delta_5bps"],
            "I_delta_raw": m_i["delta_raw"],
            "I_cost_savings": m_i["cost_savings_from_reject"],
            "I_sum_parts": round(m_i["delta_raw"] + m_i["cost_savings_from_reject"], 2),
            "runtime_5bps": m_rt["pnl_5bps"],
            "runtime_raw": m_rt["raw_pnl"],
        },
    }


def select_features(uni_rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    selected, rejected = [], []
    for u in uni_rows:
        if not u.get("usable"):
            rejected.append({**u, "reject_reason": "missing_or_n_low"})
            continue
        scores = [abs(u.get("winner_d") or 0), abs(u.get("stop_d") or 0), abs(u.get("np_d") or 0)]
        if max(scores) >= 0.12:
            selected.append({**dict(u), "select_reason": "effect_size"})
        else:
            rejected.append({**dict(u), "reject_reason": "weak_effect"})
    return selected, rejected
