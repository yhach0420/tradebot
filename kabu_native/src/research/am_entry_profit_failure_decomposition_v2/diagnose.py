"""Diagnostics. No threshold search. No policy. No new target."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_failure_decomposition_v2 import EQUAL_SHARE
from research.am_entry_profit_improvement import ARCH_PAIR, ARCH_RF, ARCH_RIDGE, UTILITY_KEY
from research.canonical_entry_performance_rebase.analyze import _f, rank_group, row_key
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import spearman
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _median
from research.wait5_session_target_learnability import AM_TOPK, POS_REP_MIN


ARCHITECTURES = (ARCH_RIDGE, ARCH_RF, ARCH_PAIR)


def outcome_class(r: dict[str, Any]) -> str:
    if int(r.get("Y_FILL5") or 0) != 1:
        return "NONFILL"
    u = _f(r.get(UTILITY_KEY))
    if u is None:
        return "NONFILL"
    if float(u) > 1e-12:
        return "POSITIVE_FILL"
    if float(u) < -1e-12:
        return "NEGATIVE_FILL"
    return "FLAT_FILL"


def mark_current_top3(rows: list[dict[str, Any]]) -> set[str]:
    selected: set[str] = set()
    for _k, grp in group_cohorts(rows).items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        ranked = rank_group(grp, "current_score")
        for r in ranked[: int(AM_TOPK)]:
            selected.add(row_key(r))
    for r in rows:
        r["CURRENT_TOP3"] = row_key(r) in selected
    return selected


def filled_utility_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = neg = flat = zero_all = fill_n = 0
    for r in rows:
        u = _f(r.get(UTILITY_KEY))
        if u is None:
            continue
        if abs(float(u)) <= 1e-12:
            zero_all += 1
        if int(r.get("Y_FILL5") or 0) != 1:
            continue
        fill_n += 1
        if float(u) > 1e-12:
            pos += 1
        elif float(u) < -1e-12:
            neg += 1
        else:
            flat += 1
    return {
        "POSITIVE_UTILITY_N": pos,
        "NEGATIVE_UTILITY_N": neg,
        "FLAT_UTILITY_N": flat,
        "UTILITY_FILL_N": fill_n,
        "UTILITY_ZERO_N": zero_all,
    }


def price_tercile_cuts(rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    xs = []
    for r in rows:
        if int(r.get("Y_FILL5") or 0) != 1:
            continue
        px = _f(r.get("fill_price"))
        if px is not None:
            xs.append(float(px))
    if len(xs) < 3:
        return None
    qs = np.quantile(np.asarray(xs, dtype=float), [1.0 / 3.0, 2.0 / 3.0])
    return float(qs[0]), float(qs[1])


def price_bucket(px: Optional[float], cuts: tuple[float, float] | None) -> Optional[str]:
    if px is None or cuts is None:
        return None
    if float(px) <= cuts[0]:
        return "LOW"
    if float(px) <= cuts[1]:
        return "MID"
    return "HIGH"


def utility_tercile_cuts(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 3:
        return None
    qs = np.quantile(np.asarray(values, dtype=float), [1.0 / 3.0, 2.0 / 3.0])
    return float(qs[0]), float(qs[1])


def pnl_class(pnl: Optional[float]) -> str:
    if pnl is None:
        return "FLAT"
    if float(pnl) > 1e-12:
        return "WIN"
    if float(pnl) < -1e-12:
        return "LOSS"
    return "FLAT"


def spec_independent_top3_keys(rows: list[dict[str, Any]], scores: dict[str, Any], score_attr: str = "pred") -> set[str]:
    tagged = []
    for r in rows:
        rec = dict(r)
        rec[score_attr] = scores.get(row_key(r))
        tagged.append(rec)
    selected: set[str] = set()
    for _k, grp in group_cohorts(tagged).items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        ranked = rank_group(grp, score_attr)
        for r in ranked[: int(AM_TOPK)]:
            selected.add(row_key(r))
    return selected


def median_or_none(xs: list[float]) -> Optional[float]:
    return float(np.median(xs)) if xs else None


def replacement_fill_relations(spec_rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills, nets, fill_pf_x, fill_pf_y, trades = [], [], [], [], []
    pos_net = []
    pos_pf = []
    for r in spec_rows:
        fr = _f(r.get("OOF_FILL_RATE"))
        net = _f(r.get("NET_PNL"))
        pf = _f(r.get("PF"))
        tn = _f(r.get("TRADE_N"))
        if fr is not None and net is not None:
            fills.append(float(fr))
            nets.append(float(net))
        if fr is not None and pf is not None:
            fill_pf_x.append(float(fr))
            fill_pf_y.append(float(pf))
        if tn is not None and net is not None:
            trades.append((float(tn), float(net)))
        if net is not None and float(net) > 0:
            pos_net.append(r)
        if pf is not None and float(pf) > 1:
            pos_pf.append(r)
    tn_net_x = [a for a, _b in trades]
    tn_net_y = [b for _a, b in trades]
    return {
        "SPEARMAN_FILL_VS_NET": spearman(fills, nets) if len(fills) >= 10 else None,
        "SPEARMAN_FILL_VS_PF": spearman(fill_pf_x, fill_pf_y) if len(fill_pf_x) >= 10 else None,
        "SPEARMAN_TRADE_N_VS_NET": spearman(tn_net_x, tn_net_y) if len(tn_net_x) >= 10 else None,
        "NET_POS_SPEC_N": len(pos_net),
        "PF_GT1_SPEC_N": len(pos_pf),
        "NET_POS_SPECS": [
            {
                "spec_id": r.get("spec_id"),
                "TRADE_N": r.get("TRADE_N"),
                "OOF_FILL_RATE": r.get("OOF_FILL_RATE"),
                "NET_PNL": r.get("NET_PNL"),
                "PF": r.get("PF"),
            }
            for r in pos_net
        ],
        "PF_GT1_SPECS": [
            {
                "spec_id": r.get("spec_id"),
                "TRADE_N": r.get("TRADE_N"),
                "OOF_FILL_RATE": r.get("OOF_FILL_RATE"),
                "NET_PNL": r.get("NET_PNL"),
                "PF": r.get("PF"),
            }
            for r in pos_pf
        ],
    }


def pred_outcome_medians(rows: list[dict[str, Any]], scores: dict[str, Any]) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {"NONFILL": [], "POSITIVE_FILL": [], "NEGATIVE_FILL": [], "FLAT_FILL": []}
    for r in rows:
        sc = _f(scores.get(row_key(r)))
        if sc is None:
            continue
        buckets[outcome_class(r)].append(float(sc))
    return {
        "PRED_SCORE_NONFILL_MEDIAN": median_or_none(buckets["NONFILL"]),
        "PRED_SCORE_POS_FILL_MEDIAN": median_or_none(buckets["POSITIVE_FILL"]),
        "PRED_SCORE_NEG_FILL_MEDIAN": median_or_none(buckets["NEGATIVE_FILL"]),
        "PRED_SCORE_FLAT_FILL_MEDIAN": median_or_none(buckets["FLAT_FILL"]),
        "n_nonfill": len(buckets["NONFILL"]),
        "n_pos_fill": len(buckets["POSITIVE_FILL"]),
        "n_neg_fill": len(buckets["NEGATIVE_FILL"]),
        "n_flat_fill": len(buckets["FLAT_FILL"]),
    }


def top3_outcome_shares(rows: list[dict[str, Any]], selected: set[str]) -> dict[str, Any]:
    n = n_non = n_pos = n_neg = n_flat = 0
    for r in rows:
        if row_key(r) not in selected:
            continue
        n += 1
        oc = outcome_class(r)
        if oc == "NONFILL":
            n_non += 1
        elif oc == "POSITIVE_FILL":
            n_pos += 1
        elif oc == "NEGATIVE_FILL":
            n_neg += 1
        else:
            n_flat += 1
    def _sh(x: int) -> Optional[float]:
        return float(x) / float(n) if n else None
    return {
        "TOP3_N": n,
        "NONFILL_SHARE": _sh(n_non),
        "POSITIVE_FILL_SHARE": _sh(n_pos),
        "NEGATIVE_FILL_SHARE": _sh(n_neg),
        "FLAT_FILL_SHARE": _sh(n_flat),
    }


def price_scale_label(rows: list[dict[str, Any]], cuts: tuple[float, float] | None) -> dict[str, Any]:
    by: dict[str, list[dict[str, float]]] = {"LOW": [], "MID": [], "HIGH": []}
    for r in rows:
        if int(r.get("Y_FILL5") or 0) != 1:
            continue
        u = _f(r.get(UTILITY_KEY))
        px = _f(r.get("fill_price"))
        b = price_bucket(px, cuts)
        if u is None or b is None:
            continue
        by[b].append({"u": float(u), "abs_u": abs(float(u)), "px": float(px)})
    terc = []
    loss_abs_high = 0.0
    loss_abs_all = 0.0
    for name in ("LOW", "MID", "HIGH"):
        xs = by[name]
        us = [z["u"] for z in xs]
        aus = [z["abs_u"] for z in xs]
        losses = [z["u"] for z in xs if z["u"] < -1e-12]
        abs_losses = [abs(v) for v in losses]
        loss_abs_all += sum(abs_losses)
        if name == "HIGH":
            loss_abs_high = sum(abs_losses)
        terc.append(
            {
                "tercile": name,
                "n": len(xs),
                "UTILITY_MEDIAN": median_or_none(us),
                "ABS_UTILITY_MEDIAN": median_or_none(aus),
                "LOSS_MEDIAN": median_or_none(losses),
                "ABS_LOSS_MEDIAN": median_or_none(abs_losses),
            }
        )
    return {
        "terciles": terc,
        "HIGH_PRICE_GROSS_LOSS_SHARE": (loss_abs_high / loss_abs_all) if loss_abs_all > 1e-12 else None,
    }


def spec_abs_errors(
    rows: list[dict[str, Any]],
    scores: dict[str, Any],
    cuts: tuple[float, float] | None,
    *,
    yen_scale: bool,
) -> dict[str, Any]:
    abs_err: list[float] = []
    prices: list[float] = []
    high_abs = 0.0
    all_abs = 0.0
    by_sym: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if int(r.get("Y_FILL5") or 0) != 1:
            continue
        u = _f(r.get(UTILITY_KEY))
        sc = _f(scores.get(row_key(r)))
        px = _f(r.get("fill_price"))
        if u is None or sc is None or px is None:
            continue
        err = float(u) - float(sc)
        ae = abs(err)
        abs_err.append(ae)
        prices.append(float(px))
        all_abs += ae
        if price_bucket(px, cuts) == "HIGH":
            high_abs += ae
        by_sym[str(r.get("symbol") or "").replace(".T", "")].append(ae)
    sp = spearman(prices, abs_err) if yen_scale and len(prices) >= 10 else None
    return {
        "HIGH_PRICE_ABS_ERROR_SHARE": (high_abs / all_abs) if all_abs > 1e-12 else None,
        "SPEARMAN_ABS_ERROR_VS_FILL_PRICE": sp,
        "symbol_abs_error_median": {s: float(np.median(vs)) for s, vs in by_sym.items()},
        "n": len(abs_err),
    }


def paired_spearman(
    rows: list[dict[str, Any]],
    scores: dict[str, Any],
    *,
    mask: Any,
    y_fn: Any,
    min_n: int = 10,
) -> Optional[float]:
    xs: list[float] = []
    ys: list[float] = []
    for r in rows:
        if not mask(r):
            continue
        sc = _f(scores.get(row_key(r)))
        y = y_fn(r)
        if sc is None or y is None:
            continue
        xs.append(float(sc))
        ys.append(float(y))
    if len(xs) < int(min_n):
        return None
    return spearman(xs, ys)


def daily_spearman(
    rows: list[dict[str, Any]],
    scores: dict[str, Any],
    *,
    mask: Any,
    y_fn: Any,
    days: list[str],
) -> list[dict[str, Any]]:
    out = []
    for d in days:
        xs: list[float] = []
        ys: list[float] = []
        for r in rows:
            if str(r.get("date") or "") != d:
                continue
            if not mask(r):
                continue
            sc = _f(scores.get(row_key(r)))
            y = y_fn(r)
            if sc is None or y is None:
                continue
            xs.append(float(sc))
            ys.append(float(y))
        sp = spearman(xs, ys) if len(xs) >= int(MIN_COHORT_N) else None
        out.append({"date": d, "spearman": sp, "n": len(xs)})
    return out


def positive_utility_top_rank_enrichment(rows: list[dict[str, Any]], scores: dict[str, Any], current_keys: set[str]) -> Optional[float]:
    top_n = top_pos = base_n = base_pos = 0
    tagged = []
    for r in rows:
        rec = dict(r)
        rec["pred"] = scores.get(row_key(r))
        tagged.append(rec)
    for _k, grp in group_cohorts(tagged).items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        outside = [r for r in grp if row_key(r) not in current_keys]
        if len(outside) < int(AM_TOPK):
            continue
        for r in outside:
            base_n += 1
            if outcome_class(r) == "POSITIVE_FILL":
                base_pos += 1
        ranked = rank_group(outside, "pred")[: int(AM_TOPK)]
        for r in ranked:
            top_n += 1
            if outcome_class(r) == "POSITIVE_FILL":
                top_pos += 1
    base = (float(base_pos) / float(base_n)) if base_n else None
    top = (float(top_pos) / float(top_n)) if top_n else None
    if base is None or top is None or base <= 1e-12:
        return None
    return float(top / base)


def win_loss_scores(
    rows: list[dict[str, Any]],
    scores: dict[str, Any],
    trade_pnl: dict[str, float],
) -> dict[str, Any]:
    win: list[float] = []
    loss: list[float] = []
    flat: list[float] = []
    fill_pnls: list[tuple[str, float, float]] = []
    for r in rows:
        k = row_key(r)
        if k not in trade_pnl:
            continue
        sc = _f(scores.get(k))
        if sc is None:
            continue
        pnl = float(trade_pnl[k])
        cls = pnl_class(pnl)
        if cls == "WIN":
            win.append(float(sc))
        elif cls == "LOSS":
            loss.append(float(sc))
        else:
            flat.append(float(sc))
        fill_pnls.append((k, pnl, float(sc)))
    win_m = median_or_none(win)
    loss_m = median_or_none(loss)
    delta = (float(win_m) - float(loss_m)) if win_m is not None and loss_m is not None else None
    worst_delta = None
    if fill_pnls:
        pnls = [p for _k, p, _s in fill_pnls]
        cuts = utility_tercile_cuts(pnls)
        if cuts is not None:
            worst = [s for _k, p, s in fill_pnls if p <= cuts[0]]
            rest = [s for _k, p, s in fill_pnls if p > cuts[0]]
            wm = median_or_none(worst)
            rm = median_or_none(rest)
            if wm is not None and rm is not None:
                worst_delta = float(rm) - float(wm)
    return {
        "WIN_N": len(win),
        "LOSS_N": len(loss),
        "FLAT_N": len(flat),
        "SCORE_WIN_MEDIAN": win_m,
        "SCORE_LOSS_MEDIAN": loss_m,
        "WIN_MINUS_LOSS_SCORE": delta,
        "WORST_LOSS_DISCRIMINATION": worst_delta,
    }


def robust_architecture(rep_effects: list[Optional[float]], daily_medians: list[Optional[float]]) -> dict[str, Any]:
    reps = [float(v) for v in rep_effects if v is not None]
    pos_rep = sum(1 for v in reps if v > 0)
    med = _median(reps) if reps else None
    daily = [float(v) for v in daily_medians if v is not None]
    st = delta_series_stats(daily) if daily else {
        "median": None,
        "positive_days": 0,
        "negative_days": 0,
        "ex_best_day": None,
        "ex_top3_days": None,
        "n_days": 0,
    }
    med_ok = med is not None and float(med) > 0
    rep_ok = int(pos_rep) >= int(POS_REP_MIN)
    day_ok = int(st.get("positive_days") or 0) > int(st.get("negative_days") or 0)
    exb = st.get("ex_best_day")
    ext = st.get("ex_top3_days")
    exb_ok = exb is not None and float(exb) > 0
    ext_ok = ext is not None and float(ext) >= 0
    supported = bool(med_ok and rep_ok and day_ok and exb_ok and ext_ok)
    return {
        "median_effect": med,
        "positive_rep_n": pos_rep,
        "rep_n": len(reps),
        "positive_days": st.get("positive_days"),
        "negative_days": st.get("negative_days"),
        "ex_best_day": st.get("ex_best_day"),
        "ex_top3_days": st.get("ex_top3_days"),
        "n_days": st.get("n_days"),
        "SUPPORTED": supported,
        "gates": {
            "median_gt_0": med_ok,
            "pos_rep_ge_6": rep_ok,
            "pos_days_gt_neg": day_ok,
            "ex_best_gt_0": exb_ok,
            "ex_top3_ge_0": ext_ok,
        },
    }
