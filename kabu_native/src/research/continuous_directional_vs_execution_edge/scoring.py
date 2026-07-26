"""Fit H1–H6 on directional labels; evaluate execution separately."""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from research.continuous_directional_vs_execution_edge.constants import HYPOTHESES, MIN_SELECTED
from research.continuous_directional_vs_execution_edge.labels import DirLabel
from research.ueia_economic_gate_and_flow_delay.scoring import (
    FittedModel,
    _score_samples,
    daily_cost_adj,
    symbol_concentration,
)
from research.upward_edge_identification_audit.features import features_for_groups
from research.upward_edge_identification_audit.models import (
    _apply_std,
    _feat_matrix,
    _impute,
    _standardize_fit,
    _train_medians,
    fit_logit,
    pr_auc,
    predict_proba,
    roc_auc,
    top_decile_lift,
    top_quintile_lift,
)
from research.upward_edge_identification_audit.samples import Sample


def _y_dir(s: Sample, label_key: str) -> Optional[int]:
    lab = s.labels.get(label_key)
    if lab is None:
        return None
    if lab.first_result == "UP_FIRST":
        return 1
    if lab.first_result == "DOWN_FIRST":
        return 0
    return None


def dir_pop(samples: Sequence[Sample], label_key: str) -> dict[str, Any]:
    labs = [s.labels[label_key] for s in samples if label_key in s.labels]
    n = len(labs) or 1
    up = sum(1 for L in labs if L.first_result == "UP_FIRST")
    dn = sum(1 for L in labs if L.first_result == "DOWN_FIRST")
    terms = [L.terminal_bps for L in labs if L.terminal_bps is not None]
    mfes = [L.mfe_bps for L in labs if L.mfe_bps is not None]
    maes = [L.mae_bps for L in labs if L.mae_bps is not None]
    abs_mae = [abs(x) for x in maes]
    return {
        "n": len(labs),
        "UP_FIRST": up, "UP_FIRST_rate": up / n,
        "DOWN_FIRST": dn, "DOWN_FIRST_rate": dn / n,
        "NEITHER": sum(1 for L in labs if L.first_result == "NEITHER"),
        "up_down_ratio": (up / dn) if dn else None,
        "avg_terminal_bps": sum(terms) / len(terms) if terms else None,
        "median_terminal_bps": sorted(terms)[len(terms) // 2] if terms else None,
        "avg_mfe": sum(mfes) / len(mfes) if mfes else None,
        "avg_mae": sum(maes) / len(maes) if maes else None,
        "mfe_mae": (sum(mfes) / len(mfes)) / (sum(abs_mae) / len(abs_mae)) if mfes and abs_mae and sum(abs_mae) else None,
    }


def fit_dir_candidate(train: Sequence[Sample], label_key: str, hid: str) -> FittedModel:
    groups = list(HYPOTHESES[hid])
    key = f"{label_key}_{hid}"
    tr_rows, keys = _feat_matrix(train, groups)
    med = _train_medians(tr_rows, keys)
    Xtr = _impute(tr_rows, keys, med)
    ytr, Xf = [], []
    for s, row in zip(train, Xtr):
        yi = _y_dir(s, label_key)
        if yi is None:
            continue
        ytr.append(yi)
        Xf.append(row)
    means, stds = _standardize_fit(Xf)
    w, b = fit_logit(_apply_std(Xf, means, stds), ytr)
    model = FittedModel(
        key=key, barrier=label_key, groups=groups, keys=keys,
        medians=med, means=means, stds=stds, w=w, b=b,
    )
    model.train_scores = _score_samples(model, train)
    sc = sorted(model.train_scores)
    model.fixed_threshold = sc[int(0.90 * (len(sc) - 1))] if sc else None
    return model


def eval_dir_fixed(
    samples: Sequence[Sample],
    label_key: str,
    scores: list[float],
    threshold: float,
) -> dict[str, Any]:
    base = dir_pop(samples, label_key)
    selected = [s for s, sc in zip(samples, scores) if sc >= threshold]
    sel = dir_pop(selected, label_key) if selected else dir_pop([], label_key)
    y, p = [], []
    for s, sc in zip(samples, scores):
        yi = _y_dir(s, label_key)
        if yi is None:
            continue
        y.append(yi)
        p.append(sc)
    base_up = base.get("UP_FIRST_rate") or 0
    lift = (sel.get("UP_FIRST_rate") / base_up) if base_up and sel.get("UP_FIRST_rate") is not None else None
    return {
        **base,
        "n_selected": len(selected),
        "select_rate": len(selected) / len(samples) if samples else 0,
        "roc_auc": roc_auc(y, p) if len(set(y)) > 1 else None,
        "pr_auc": pr_auc(y, p) if len(set(y)) > 1 else None,
        "top_decile_lift": top_decile_lift(y, p) if len(set(y)) > 1 else None,
        "top_quintile_lift": top_quintile_lift(y, p) if len(set(y)) > 1 else None,
        "selected_up_rate": sel.get("UP_FIRST_rate"),
        "selected_down_rate": sel.get("DOWN_FIRST_rate"),
        "selected_up_down": sel.get("up_down_ratio"),
        "selected_avg_terminal": sel.get("avg_terminal_bps"),
        "selected_median_terminal": sel.get("median_terminal_bps"),
        "selected_mfe_mae": sel.get("mfe_mae"),
        "selected_lift": lift,
        "threshold": threshold,
    }


def train_dir_passes(m: dict, train_days: list[str], selected: Sequence[Sample], label_key: str) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("roc_auc") or 0) <= 0.55:
        ok = False
        reasons.append("auc<=0.55")
    if (m.get("selected_lift") or 0) <= 1.20:
        ok = False
        reasons.append("lift<=1.20")
    if (m.get("selected_avg_terminal") or 0) <= 0:
        ok = False
        reasons.append("future_return<=0")
    if (m.get("selected_mfe_mae") or 0) <= 1.0:
        ok = False
        reasons.append("mfe_mae<=1")
    if (m.get("n_selected") or 0) < MIN_SELECTED:
        ok = False
        reasons.append("n_low")
    # multi-day: terminal > 0 both days among selected
    by = {}
    for s in selected:
        lab = s.labels.get(label_key)
        if lab and lab.terminal_bps is not None:
            by.setdefault(s.day, []).append(lab.terminal_bps)
    pos_days = sum(1 for d in train_days if by.get(d) and (sum(by[d]) / len(by[d])) > 0)
    if pos_days < 2 and len(train_days) >= 2:
        ok = False
        reasons.append("not_multi_day")
    # concentration via up counts
    from collections import defaultdict
    ups = defaultdict(int)
    for s in selected:
        if s.labels.get(label_key) and s.labels[label_key].first_result == "UP_FIRST":
            ups[s.symbol] += 1
    tot = sum(ups.values()) or 1
    top1 = max(ups.values()) / tot if ups else 0
    if top1 >= 0.50:
        ok = False
        reasons.append("symbol_concentration")
    base_ud = m.get("up_down_ratio")
    sel_ud = m.get("selected_up_down")
    if base_ud is not None and sel_ud is not None and sel_ud <= base_ud:
        ok = False
        reasons.append("up_down_not_improved")
    return ok, reasons


def val_dir_passes(m: dict, selected: Sequence[Sample], label_key: str, base_ud: Optional[float]) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("roc_auc") or 0) <= 0.55:
        ok = False
        reasons.append("auc<=0.55")
    if (m.get("selected_lift") or 0) <= 1.20:
        ok = False
        reasons.append("lift<=1.20")
    if (m.get("selected_avg_terminal") or 0) <= 0:
        ok = False
        reasons.append("future_return<=0")
    if (m.get("selected_mfe_mae") or 0) <= 1.0:
        ok = False
        reasons.append("mfe_mae<=1")
    if (m.get("n_selected") or 0) < MIN_SELECTED:
        ok = False
        reasons.append("n_low")
    if base_ud is not None and (m.get("selected_up_down") or 0) <= base_ud:
        ok = False
        reasons.append("up_down_not_improved")
    from collections import defaultdict
    ups = defaultdict(int)
    for s in selected:
        if s.labels.get(label_key) and s.labels[label_key].first_result == "UP_FIRST":
            ups[s.symbol] += 1
    tot = sum(ups.values()) or 1
    if ups and max(ups.values()) / tot >= 0.50:
        ok = False
        reasons.append("symbol_concentration")
    return ok, reasons


def exec_selected_metrics(selected: Sequence[Sample], horizon_key: str = "h60") -> dict[str, Any]:
    rows = []
    for s in selected:
        ex = getattr(s, "execution", None) or {}
        h = ex.get(horizon_key) or {}
        if h.get("cost_adj_bps") is not None:
            rows.append(h)
    if not rows:
        return {"n": 0, "cost_adj": None, "mfe_mae": None}
    cadj = [r["cost_adj_bps"] for r in rows]
    mfe = [r["mfe_bps"] for r in rows if r.get("mfe_bps") is not None]
    mae = [r["mae_bps"] for r in rows if r.get("mae_bps") is not None]
    abs_mae = [abs(x) for x in mae]
    pos = sum(1 for x in cadj if x > 0)
    wins = [x for x in cadj if x > 0]
    losses = [x for x in cadj if x <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    return {
        "n": len(rows),
        "cost_adj": sum(cadj) / len(cadj),
        "mfe": sum(mfe) / len(mfe) if mfe else None,
        "mae": sum(mae) / len(mae) if mae else None,
        "mfe_mae": (sum(mfe) / len(mfe)) / (sum(abs_mae) / len(abs_mae)) if mfe and abs_mae and sum(abs_mae) else None,
        "positive_rate": pos / len(cadj),
        "pf": pf,
        "avg_yen_100": sum(r.get("yen_100") or 0 for r in rows) / len(rows),
    }
