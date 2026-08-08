"""Nested outer-block / inner leave-one-day-out candidate selection."""
from __future__ import annotations

from typing import Any

import numpy as np

from . import (
    MIN_INNER_DAYS_EVAL,
    MIN_INNER_EPISODES,
    MIN_INNER_SYMBOLS,
    MIN_OUTER_EPISODES,
    MIN_OUTER_SYMBOLS,
    OUTER_BLOCKS,
    OUTER_MIN_POSITIVE_BLOCKS,
)
from .features import candidate_mask, fit_thresholds
from .metrics import passes_inner, summarize_mask


def block_day_sets() -> dict[str, set[str]]:
    return {k: set(v) for k, v in OUTER_BLOCKS.items()}


def outer_train_test(fold: str) -> tuple[set[str], set[str]]:
    blocks = block_day_sets()
    test = blocks[fold]
    train: set[str] = set()
    for k, days in blocks.items():
        if k != fold:
            train |= days
    return train, test


def _indices_for_days(dates: np.ndarray, day_set: set[str]) -> np.ndarray:
    return np.array([i for i, d in enumerate(dates.tolist()) if d in day_set], dtype=int)


def run_inner_lodo(
    *,
    catalog: list[dict[str, Any]],
    feat_mat: np.ndarray,
    features: list[str],
    labels: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
    train_days: set[str],
) -> dict[str, Any]:
    """Inner leave-one-day-out on Outer Train. Outer Test never touched."""
    train_days_sorted = sorted(train_days)
    # Accumulate selected-episode indices across inner folds per semantic_id
    pooled_sel: dict[str, np.ndarray] = {
        c["semantic_id"]: np.zeros(len(dates), dtype=bool) for c in catalog
    }
    day_hits: dict[str, list[dict[str, Any]]] = {c["semantic_id"]: [] for c in catalog}

    for hold in train_days_sorted:
        inner_train = train_days - {hold}
        tr_idx = _indices_for_days(dates, inner_train)
        va_idx = _indices_for_days(dates, {hold})
        if tr_idx.size < 50 or va_idx.size < 5:
            continue
        thr = fit_thresholds(feat_mat, features, tr_idx)
        single_cache: dict[str, np.ndarray] = {}
        # precompute singles
        for c in catalog:
            if c["kind"] != "single":
                continue
            candidate_mask(feat_mat, features, thr, c, single_cache)
        base_va = np.zeros(len(dates), dtype=bool)
        base_va[va_idx] = True
        for c in catalog:
            sid = c["semantic_id"]
            m = candidate_mask(feat_mat, features, thr, c, single_cache)
            pooled_sel[sid] |= (m & base_va)
            s = summarize_mask(
                mask=m & base_va,
                labels=labels,
                dates=dates,
                symbols=symbols,
                complement_base=base_va,
            )
            day_hits[sid].append({"day": hold, **s})

    selected: list[str] = []
    inner_summaries: dict[str, Any] = {}
    for c in catalog:
        sid = c["semantic_id"]
        sm = summarize_mask(
            mask=pooled_sel[sid],
            labels=labels,
            dates=dates,
            symbols=symbols,
            complement_base=_indices_mask(dates, train_days),
        )
        # day majority across inner holdout days with enough episodes
        pos_days = 0
        eval_days = 0
        for dh in day_hits[sid]:
            if (dh.get("episode_count") or 0) < 5:
                continue
            eval_days += 1
            if (dh.get("return_300") or 0) > 0 and (dh.get("primary_edge") or 0) > 0:
                pos_days += 1
        sm["inner_positive_days"] = pos_days
        sm["inner_eval_days"] = eval_days
        sm["positive_day_majority"] = eval_days >= MIN_INNER_DAYS_EVAL and pos_days > eval_days / 2.0
        ok = passes_inner(sm, min_ep=MIN_INNER_EPISODES, min_sym=MIN_INNER_SYMBOLS)
        inner_summaries[sid] = {**sm, "inner_pass": ok, "kind": c["kind"]}
        if ok:
            selected.append(sid)

    return {
        "selected_ids": selected,
        "inner_summaries": inner_summaries,
        "n_selected": len(selected),
        "n_catalog": len(catalog),
    }


def _indices_mask(dates: np.ndarray, day_set: set[str]) -> np.ndarray:
    return np.array([d in day_set for d in dates.tolist()], dtype=bool)


def evaluate_outer_test(
    *,
    catalog_by_id: dict[str, dict[str, Any]],
    selected_ids: list[str],
    feat_mat: np.ndarray,
    features: list[str],
    labels: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
    train_days: set[str],
    test_days: set[str],
) -> dict[str, dict[str, Any]]:
    """Fit thresholds on full Outer Train; apply to Outer Test only for inner-frozen candidates."""
    tr_idx = _indices_for_days(dates, train_days)
    thr = fit_thresholds(feat_mat, features, tr_idx)
    test_base = _indices_mask(dates, test_days)
    single_cache: dict[str, np.ndarray] = {}
    for sid in selected_ids:
        c = catalog_by_id[sid]
        if c["kind"] == "single":
            candidate_mask(feat_mat, features, thr, c, single_cache)
    # ensure parent singles cached
    for sid in selected_ids:
        c = catalog_by_id[sid]
        for p in c.get("parents") or []:
            if p in catalog_by_id and catalog_by_id[p]["kind"] == "single":
                candidate_mask(feat_mat, features, thr, catalog_by_id[p], single_cache)

    out: dict[str, dict[str, Any]] = {}
    for sid in selected_ids:
        c = catalog_by_id[sid]
        m = candidate_mask(feat_mat, features, thr, c, single_cache)
        sm = summarize_mask(
            mask=m & test_base,
            labels=labels,
            dates=dates,
            symbols=symbols,
            complement_base=test_base,
        )
        sm["outer_usable"] = (
            (sm.get("episode_count") or 0) >= MIN_OUTER_EPISODES
            and (sm.get("symbol_count") or 0) >= MIN_OUTER_SYMBOLS
        )
        out[sid] = sm
    return out


def aggregate_outer_results(
    fold_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    fold_results[fold] = {
      'selected_ids': [...],
      'outer': {sid: summary},
      'inner': {...},
    }
    """
    all_ids: set[str] = set()
    for fr in fold_results.values():
        all_ids |= set(fr.get("selected_ids") or [])

    families: dict[str, Any] = {}
    for sid in sorted(all_ids):
        blocks = {}
        for fold, fr in fold_results.items():
            if sid not in (fr.get("outer") or {}):
                continue
            blocks[fold] = fr["outer"][sid]
        if not blocks:
            continue
        pos300 = sum(1 for s in blocks.values() if (s.get("return_300") or 0) > 0)
        pos600 = sum(1 for s in blocks.values() if (s.get("return_600") or 0) > 0)
        # pooled primary edge
        edges = [s.get("primary_edge") for s in blocks.values() if s.get("primary_edge") is not None]
        rets300 = [s.get("return_300") for s in blocks.values() if s.get("return_300") is not None]
        rets600 = [s.get("return_600") for s in blocks.values() if s.get("return_600") is not None]
        # episode-weighted means
        w_ep = [s.get("episode_count") or 0 for s in blocks.values()]
        def _wavg(vals, ws):
            pairs = [(v, w) for v, w in zip(vals, ws) if v is not None and w > 0]
            if not pairs:
                return None
            return float(sum(v * w for v, w in pairs) / sum(w for _, w in pairs))

        primary_edge = _wavg(
            [s.get("primary_edge") for s in blocks.values()],
            w_ep,
        )
        mean_300 = _wavg(rets300, [blocks[f].get("episode_count") or 0 for f in blocks])
        # align weights to rets lists carefully
        mean_300 = _wavg(
            [blocks[f].get("return_300") for f in blocks],
            [blocks[f].get("episode_count") or 0 for f in blocks],
        )
        mean_600 = _wavg(
            [blocks[f].get("return_600") for f in blocks],
            [blocks[f].get("episode_count") or 0 for f in blocks],
        )
        mfe = _wavg(
            [blocks[f].get("mfe") for f in blocks],
            [blocks[f].get("episode_count") or 0 for f in blocks],
        )
        mae = _wavg(
            [blocks[f].get("mae") for f in blocks],
            [blocks[f].get("episode_count") or 0 for f in blocks],
        )
        n_blocks = len(blocks)
        outer_pass = (
            n_blocks >= OUTER_MIN_POSITIVE_BLOCKS
            and pos300 >= OUTER_MIN_POSITIVE_BLOCKS
            and pos600 >= OUTER_MIN_POSITIVE_BLOCKS
            and (primary_edge or 0) > 0
            and (mean_300 or 0) > 0
            and (mean_600 or 0) > 0
        )
        families[sid] = {
            "semantic_id": sid,
            "n_outer_blocks_evaluated": n_blocks,
            "positive_blocks_return_300": pos300,
            "positive_blocks_return_600": pos600,
            "primary_edge": primary_edge,
            "return_300": mean_300,
            "return_600": mean_600,
            "mfe": mfe,
            "mae": mae,
            "blocks": {k: {
                "episode_count": v.get("episode_count"),
                "symbol_count": v.get("symbol_count"),
                "primary_win_rate": v.get("primary_win_rate"),
                "primary_edge": v.get("primary_edge"),
                "ft_plus30_count": v.get("ft_plus30_count"),
                "ft_minus20_count": v.get("ft_minus20_count"),
                "return_300": v.get("return_300"),
                "return_600": v.get("return_600"),
                "positive_return_rate_300": v.get("positive_return_rate_300"),
                "mfe": v.get("mfe"),
                "mae": v.get("mae"),
                "selected_minus_complement_300": v.get("selected_minus_complement_300"),
            } for k, v in blocks.items()},
            "outer_pass": outer_pass,
        }
    return families
