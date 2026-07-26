"""Fit 12 candidates + split-local vs TRAIN-fixed threshold evaluation."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from research.ueia_economic_gate_and_flow_delay.constants import COST_BPS, HYPOTHESES, MIN_SELECTED
from research.upward_edge_identification_audit.features import features_for_groups
from research.upward_edge_identification_audit.labels import label_summary
from research.upward_edge_identification_audit.models import (
    _apply_std,
    _feat_matrix,
    _impute,
    _standardize_fit,
    _train_medians,
    _y_up,
    fit_logit,
    population_metrics,
    predict_proba,
    pr_auc,
    roc_auc,
    top_decile_lift,
)
from research.upward_edge_identification_audit.samples import Sample


@dataclass
class FittedModel:
    key: str
    barrier: str
    groups: list[str]
    keys: list[str]
    medians: dict[str, float]
    means: list[float]
    stds: list[float]
    w: list[float]
    b: float
    train_scores: list[float] = field(default_factory=list)
    fixed_threshold: Optional[float] = None


def _score_samples(model: FittedModel, samples: Sequence[Sample]) -> list[float]:
    rows, _ = _feat_matrix(samples, model.groups, model.keys)
    X = _impute(rows, model.keys, model.medians)
    Xs = _apply_std(X, model.means, model.stds)
    return predict_proba(Xs, model.w, model.b)


def fit_candidate(train: Sequence[Sample], barrier: str, hid: str) -> FittedModel:
    groups = list(HYPOTHESES[hid])
    key = f"{barrier}_{hid}"
    tr_rows, keys = _feat_matrix(train, groups)
    med = _train_medians(tr_rows, keys)
    Xtr = _impute(tr_rows, keys, med)
    ytr, Xtr_f = [], []
    for s, row in zip(train, Xtr):
        yi = _y_up(s, barrier)
        if yi is None:
            continue
        ytr.append(yi)
        Xtr_f.append(row)
    means, stds = _standardize_fit(Xtr_f)
    Xtr_s = _apply_std(Xtr_f, means, stds)
    w, b = fit_logit(Xtr_s, ytr)
    model = FittedModel(
        key=key, barrier=barrier, groups=groups, keys=keys,
        medians=med, means=means, stds=stds, w=w, b=b,
    )
    model.train_scores = _score_samples(model, train)
    # TRAIN 90th percentile on all train scores (not only binary) — deployable threshold
    sc = sorted(model.train_scores)
    model.fixed_threshold = sc[int(0.90 * (len(sc) - 1))] if sc else None
    return model


def _selected_metrics(samples: Sequence[Sample], barrier: str, selected: list[Sample], scores_all: list[float]) -> dict[str, Any]:
    base = population_metrics(samples, barrier)
    # binary AUC on full scored set
    y, p = [], []
    for s, sc in zip(samples, scores_all):
        yi = _y_up(s, barrier)
        if yi is None:
            continue
        y.append(yi)
        p.append(sc)
    top_m = population_metrics(selected, barrier) if selected else label_summary([])
    # lift vs base UP rate among selected
    base_up = base.get("UP_FIRST_rate") or 0.0
    sel_up = top_m.get("UP_FIRST_rate")
    lift = (sel_up / base_up) if base_up and sel_up is not None else None
    # cost adj mean on selected (all label outcomes, not only binary)
    cadjs = [s.labels[barrier].cost_adjusted_return_bps for s in selected if s.labels[barrier].cost_adjusted_return_bps is not None]
    mfes = [s.labels[barrier].MFE_bps for s in selected if s.labels[barrier].MFE_bps is not None]
    maes = [s.labels[barrier].MAE_bps for s in selected if s.labels[barrier].MAE_bps is not None]
    abs_mae = [abs(x) for x in maes]
    return {
        **base,
        "n_selected": len(selected),
        "select_rate": len(selected) / len(samples) if samples else 0.0,
        "n_binary": len(y),
        "roc_auc": roc_auc(y, p) if len(set(y)) > 1 else None,
        "pr_auc": pr_auc(y, p) if len(set(y)) > 1 else None,
        "top_decile_lift": top_decile_lift(y, p) if len(set(y)) > 1 else None,  # split-local diagnostic
        "selected_lift_vs_base": lift,
        "selected_up_rate": sel_up,
        "selected_down_rate": top_m.get("DOWN_FIRST_rate"),
        "selected_up_down": top_m.get("up_down_ratio"),
        "selected_cost_adj": (sum(cadjs) / len(cadjs)) if cadjs else None,
        "selected_mfe": (sum(mfes) / len(mfes)) if mfes else None,
        "selected_mae": (sum(maes) / len(maes)) if maes else None,
        "selected_mfe_mae": (
            (sum(mfes) / len(mfes)) / (sum(abs_mae) / len(abs_mae))
            if mfes and abs_mae and sum(abs_mae) else None
        ),
        # aliases matching old report keys for split-local compare
        "top_decile_cost_adj": None,  # filled by caller for split-local
        "top_decile_mfe_mae": None,
    }


def evaluate_split_local_decile(samples: Sequence[Sample], barrier: str, scores: list[float]) -> dict[str, Any]:
    """Diagnostic: re-rank top 10% inside this split (NOT deployable)."""
    pairs = sorted(zip(scores, samples), key=lambda x: x[0], reverse=True)
    # among binary only for parity with original UEIA
    bin_pairs = [(sc, s) for sc, s in pairs if _y_up(s, barrier) is not None]
    k = max(1, len(bin_pairs) // 10) if bin_pairs else 1
    selected = [s for _, s in bin_pairs[:k]]
    m = _selected_metrics(samples, barrier, selected, scores)
    m["mode"] = "split_local_top_decile"
    m["top_decile_cost_adj"] = m["selected_cost_adj"]
    m["top_decile_mfe_mae"] = m["selected_mfe_mae"]
    m["top_decile_lift"] = m.get("top_decile_lift")
    return m


def evaluate_fixed_threshold(
    samples: Sequence[Sample],
    barrier: str,
    scores: list[float],
    threshold: float,
) -> dict[str, Any]:
    selected = [s for s, sc in zip(samples, scores) if sc >= threshold]
    m = _selected_metrics(samples, barrier, selected, scores)
    m["mode"] = "train_fixed_threshold"
    m["threshold"] = threshold
    m["top_decile_cost_adj"] = m["selected_cost_adj"]  # economic gate uses this name
    m["top_decile_mfe_mae"] = m["selected_mfe_mae"]
    return m


def symbol_concentration(selected: Sequence[Sample], barrier: str) -> tuple[float, float]:
    from collections import defaultdict
    pnl_proxy = defaultdict(float)
    for s in selected:
        v = s.labels[barrier].cost_adjusted_return_bps
        if v is not None:
            pnl_proxy[s.symbol] += v
    tot = sum(abs(v) for v in pnl_proxy.values()) or 1.0
    ranked = sorted(pnl_proxy.values(), key=abs, reverse=True)
    top1 = abs(ranked[0]) / tot if ranked else 0.0
    top3 = sum(abs(x) for x in ranked[:3]) / tot if ranked else 0.0
    return top1, top3


def daily_cost_adj(selected: Sequence[Sample], barrier: str) -> dict[str, Optional[float]]:
    from collections import defaultdict
    bags: dict[str, list[float]] = defaultdict(list)
    for s in selected:
        v = s.labels[barrier].cost_adjusted_return_bps
        if v is not None:
            bags[s.day].append(v)
    return {d: (sum(v) / len(v) if v else None) for d, v in bags.items()}


def train_passes(m: dict[str, Any], train_days: list[str], selected: Sequence[Sample], barrier: str) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("roc_auc") or 0) <= 0.55:
        ok = False
        reasons.append("auc<=0.55")
    lift = m.get("selected_lift_vs_base")
    if lift is None:
        lift = m.get("top_decile_lift")
    if lift is None or lift <= 1.20:
        ok = False
        reasons.append("lift<=1.20")
    if (m.get("selected_cost_adj") or 0) <= 0:
        ok = False
        reasons.append("cost_adj<=0")
    if (m.get("selected_mfe_mae") or 0) <= 1.0:
        ok = False
        reasons.append("mfe_mae<=1")
    if (m.get("n_selected") or 0) < MIN_SELECTED:
        ok = False
        reasons.append("n_selected_low")
    dailies = daily_cost_adj(selected, barrier)
    pos_days = sum(1 for d in train_days if (dailies.get(d) or 0) > 0)
    if pos_days < 2 and len(train_days) >= 2:
        ok = False
        reasons.append("not_multi_day_positive")
    top1, _ = symbol_concentration(selected, barrier)
    if top1 >= 0.50:
        ok = False
        reasons.append("symbol_concentration")
    return ok, reasons


def val_passes(m: dict[str, Any], selected: Sequence[Sample], barrier: str, base_ud: Optional[float]) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("roc_auc") or 0) <= 0.55:
        ok = False
        reasons.append("auc<=0.55")
    lift = m.get("selected_lift_vs_base") or m.get("top_decile_lift")
    if lift is None or lift <= 1.20:
        ok = False
        reasons.append("lift<=1.20")
    if (m.get("selected_cost_adj") or 0) <= 0:
        ok = False
        reasons.append("cost_adj<=0")
    if (m.get("selected_mfe_mae") or 0) <= 1.0:
        ok = False
        reasons.append("mfe_mae<=1")
    if (m.get("n_selected") or 0) < MIN_SELECTED:
        ok = False
        reasons.append("n_selected_low")
    top1, _ = symbol_concentration(selected, barrier)
    if top1 >= 0.50:
        ok = False
        reasons.append("symbol_concentration")
    ud = m.get("selected_up_down")
    if base_ud is not None and ud is not None and ud <= base_ud:
        ok = False
        reasons.append("up_down_not_improved")
    return ok, reasons


COST_FORMULA = {
    "entry": "canonical_ask at sample time",
    "future_path": "canonical_bid after sample",
    "terminal_return_bps": "(last_bid_in_path - entry_ask) / entry_ask * 10000",
    "path_end": "first UP/DOWN/BOTH barrier hit, else last bid within horizon, else DATA_END",
    "cost_adjusted_return_bps": "terminal_return_bps - 5.0  (single roundtrip cost)",
    "spread_explicit_deduction": 0,
    "cost_bps_deduction_count": 1,
    "note": "ask-bid path already embeds spread; 5bps is additive roundtrip friction only",
    "MFE_bps": "(max_future_bid - entry_ask)/entry_ask*10000; can be negative if bid never exceeds ask",
    "MAE_bps": "(min_future_bid - entry_ask)/entry_ask*10000; typically negative",
    "MFE_over_abs_MAE": "mean(MFE) / mean(|MAE|)",
}


def manual_check_path(s: Sample, barrier: str) -> dict[str, Any]:
    lab = s.labels[barrier]
    # recompute terminal and cost from stored fields
    term = lab.terminal_return_bps
    cadj = None if term is None else term - COST_BPS
    ok = (
        lab.cost_adjusted_return_bps is not None
        and cadj is not None
        and abs(lab.cost_adjusted_return_bps - cadj) < 1e-9
    )
    return {
        "sample_id": s.sample_id,
        "barrier": barrier,
        "result": lab.first_result,
        "entry_ask": lab.entry_ask,
        "entry_bid": lab.entry_bid,
        "entry_spread": lab.entry_spread,
        "terminal_return_bps": term,
        "stored_cost_adj": lab.cost_adjusted_return_bps,
        "recomputed_cost_adj": cadj,
        "MFE_bps": lab.MFE_bps,
        "MAE_bps": lab.MAE_bps,
        "formula_match": ok,
        "spread_deductions": 0,
        "cost_deductions": 1,
    }
