"""LODO / LOSO final robustness + manifest freeze."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from . import (
    HISTORICAL_DAYS,
    LODO_MIN_POSITIVE_DAYS,
    MAX_DAY_CONTRIB,
    MAX_SYMBOL_CONTRIB,
)
from .features import candidate_mask, fit_thresholds
from .metrics import day_symbol_concentration, summarize_mask


def run_lodo(
    *,
    spec: dict[str, Any],
    catalog_by_id: dict[str, dict[str, Any]],
    feat_mat: np.ndarray,
    features: list[str],
    labels: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
) -> dict[str, Any]:
    """14× leave-one-day-out: fit on 13, apply to held-out day."""
    sid = spec["semantic_id"]
    daily = []
    pooled = np.zeros(len(dates), dtype=bool)
    for hold in HISTORICAL_DAYS:
        train = set(HISTORICAL_DAYS) - {hold}
        tr_idx = np.array([i for i, d in enumerate(dates.tolist()) if d in train], dtype=int)
        thr = fit_thresholds(feat_mat, features, tr_idx)
        single_cache: dict[str, np.ndarray] = {}
        # cache parents
        if spec["kind"] != "single":
            for p in spec.get("parents") or []:
                candidate_mask(feat_mat, features, thr, catalog_by_id[p], single_cache)
        elif spec["kind"] == "single":
            candidate_mask(feat_mat, features, thr, spec, single_cache)
        m = candidate_mask(feat_mat, features, thr, spec, single_cache)
        day_mask = dates == hold
        sm = summarize_mask(
            mask=m & day_mask,
            labels=labels,
            dates=dates,
            symbols=symbols,
            complement_base=day_mask,
        )
        daily.append({"day": hold, **sm})
        pooled |= (m & day_mask)

    pos = sum(1 for d in daily if (d.get("episode_count") or 0) >= 3 and (d.get("return_300") or 0) > 0)
    neg = sum(1 for d in daily if (d.get("episode_count") or 0) >= 3 and (d.get("return_300") or 0) <= 0)
    day_rets = [d.get("return_300") for d in daily if d.get("return_300") is not None]
    conc = day_symbol_concentration(
        mask=pooled, labels=labels, dates=dates, symbols=symbols
    )
    overall = summarize_mask(
        mask=pooled, labels=labels, dates=dates, symbols=symbols,
        complement_base=np.ones(len(dates), dtype=bool),
    )
    lodo_pass = (
        pos >= LODO_MIN_POSITIVE_DAYS
        and (overall.get("return_300") or 0) > 0
        and (overall.get("return_600") or 0) > 0
        and (overall.get("primary_edge") or 0) > 0
        and (conc.get("max_day_contribution_share") or 0) <= MAX_DAY_CONTRIB
        and (conc.get("max_symbol_contribution_share") or 0) <= MAX_SYMBOL_CONTRIB
    )
    return {
        "semantic_id": sid,
        "positive_day_count": pos,
        "negative_day_count": neg,
        "median_daily_executable_return": float(np.median(day_rets)) if day_rets else None,
        "worst_day": conc.get("worst_day"),
        "best_day": conc.get("best_day"),
        "max_day_contribution": conc.get("max_day_contribution_share"),
        "max_symbol_contribution": conc.get("max_symbol_contribution_share"),
        "overall": overall,
        "daily": daily,
        "lodo_pass": lodo_pass,
        **conc,
    }


def run_loso(
    *,
    spec: dict[str, Any],
    catalog_by_id: dict[str, dict[str, Any]],
    feat_mat: np.ndarray,
    features: list[str],
    labels: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
) -> dict[str, Any]:
    """Leave-one-symbol-out on full 14-day fit applied with symbol omitted from metrics."""
    # Fit once on all days; measure contribution by omitting each symbol from evaluation
    tr_idx = np.arange(len(dates))
    thr = fit_thresholds(feat_mat, features, tr_idx)
    single_cache: dict[str, np.ndarray] = {}
    if spec["kind"] != "single":
        for p in spec.get("parents") or []:
            candidate_mask(feat_mat, features, thr, catalog_by_id[p], single_cache)
    m = candidate_mask(feat_mat, features, thr, spec, single_cache)
    base = summarize_mask(
        mask=m, labels=labels, dates=dates, symbols=symbols,
        complement_base=np.ones(len(dates), dtype=bool),
    )
    uniq = sorted(set(symbols[m & labels["valid"]].tolist()))
    omit_rows = []
    base_ret = base.get("return_300") or 0.0
    for sym in uniq:
        mm = m & (symbols != sym)
        sm = summarize_mask(
            mask=mm, labels=labels, dates=dates, symbols=symbols,
            complement_base=symbols != sym,
        )
        omit_rows.append({
            "omitted_symbol": sym,
            "return_300": sm.get("return_300"),
            "delta_vs_full": (sm.get("return_300") - base_ret)
            if sm.get("return_300") is not None else None,
            "episode_count": sm.get("episode_count"),
        })
    deltas = [r for r in omit_rows if r.get("delta_vs_full") is not None]
    # contribution of symbol ≈ how much full exceeds omit (positive means symbol helped)
    for r in omit_rows:
        if r.get("delta_vs_full") is not None:
            r["symbol_contribution"] = -float(r["delta_vs_full"])
    contribs = [r for r in omit_rows if r.get("symbol_contribution") is not None]
    worst = min(contribs, key=lambda x: x["symbol_contribution"]) if contribs else None
    best = max(contribs, key=lambda x: x["symbol_contribution"]) if contribs else None
    pos_loso = sum(1 for r in omit_rows if (r.get("return_300") or 0) > 0)
    neg_loso = sum(1 for r in omit_rows if (r.get("return_300") or 0) <= 0)
    max_contrib = max((abs(r["symbol_contribution"]) for r in contribs), default=None)
    # severe: any single symbol contribution share via day_symbol_concentration
    conc = day_symbol_concentration(
        mask=m, labels=labels, dates=dates, symbols=symbols
    )
    severe = (conc.get("max_symbol_contribution_share") or 0) > MAX_SYMBOL_CONTRIB
    return {
        "semantic_id": spec["semantic_id"],
        "n_symbols": len(uniq),
        "positive_loso": pos_loso,
        "negative_loso": neg_loso,
        "worst_omitted_symbol": worst["omitted_symbol"] if worst else None,
        "best_omitted_symbol": best["omitted_symbol"] if best else None,
        "max_symbol_contribution": conc.get("max_symbol_contribution_share"),
        "max_abs_omit_delta": float(max_contrib) if max_contrib is not None else None,
        "severe_symbol_concentration": severe,
        "loso_pass": (not severe) and (base.get("return_300") or 0) > 0,
        "full": base,
    }


def final_refit_manifest(
    *,
    survivors: list[dict[str, Any]],
    catalog_by_id: dict[str, dict[str, Any]],
    feat_mat: np.ndarray,
    features: list[str],
    dates: np.ndarray,
) -> dict[str, Any]:
    """14-day threshold refit for outer+LODO survivors. No membership retune after fit."""
    tr_idx = np.arange(len(dates))
    thr = fit_thresholds(feat_mat, features, tr_idx)
    rules = []
    for s in survivors:
        sid = s["semantic_id"]
        spec = catalog_by_id[sid]
        if spec["kind"] == "single":
            f = spec["feature"]
            q = spec["quantile"]
            if f not in thr:
                continue
            rules.append({
                "semantic_id": sid,
                "kind": "single",
                "feature": f,
                "op": spec["op"],
                "quantile_name": spec["quantile_name"],
                "threshold": thr[f][q],
                "family": spec.get("family"),
            })
        else:
            a, b = spec["a"], spec["b"]
            if a["feature"] not in thr or b["feature"] not in thr:
                continue
            rules.append({
                "semantic_id": sid,
                "kind": spec["kind"],
                "parents": spec["parents"],
                "legs": [
                    {
                        "feature": a["feature"],
                        "op": a["op"],
                        "quantile_name": a["quantile_name"],
                        "threshold": thr[a["feature"]][a["quantile"]],
                    },
                    {
                        "feature": b["feature"],
                        "op": b["op"],
                        "quantile_name": b["quantile_name"],
                        "threshold": thr[b["feature"]][b["quantile"]],
                    },
                ],
            })
    body = {
        "manifest_id": "ENTRY_V2_MANIFEST_V1",
        "n_rules": len(rules),
        "rules": rules,
        "fit_days": list(HISTORICAL_DAYS),
        "threshold_source": "ALL_14_HISTORICAL_TRAIN_ONLY_QUANTILES",
        "label_primary": "ABS_RISE_30_BEFORE_DOWN20_600",
        "entry_basis": "actual_ask_Sell1_within_5s",
        "no_membership_retune_after_fit": True,
        "exit_research_allowed": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    return body
