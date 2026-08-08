"""SHORT nested CV — only when Phase B baseline passes. Reuses X30 semantic factory."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x30_absolute_rise_entry_v2.cv import (
    aggregate_outer_results,
    evaluate_outer_test,
    outer_train_test,
    run_inner_lodo,
)
from research.e1_x30_absolute_rise_entry_v2.features import (
    available_features,
    build_semantic_catalog,
    feature_matrix,
)
from research.e1_x30_absolute_rise_entry_v2.robust import run_lodo, run_loso

from . import OUTER_BLOCKS


def _adapt_short_as_long_labels(short: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Map SHORT arrays into the X30 label schema expected by CV helpers."""
    return {
        "valid": short["valid"],
        "primary": short["primary"],
        "return_300": short["return_300"],
        "return_300_valid": short["return_300_valid"],
        "return_600": short["return_600"],
        "return_600_valid": short["return_600_valid"],
        "mfe": short["mfe"],
        "mae": short["mae"],
        "time_to_p30": np.full(len(short["valid"]), np.nan),  # unused counts ok
        "time_to_m20": np.full(len(short["valid"]), np.nan),
    }


def run_short_nested_cv(
    *,
    rows: list[dict[str, Any]],
    short: dict[str, np.ndarray],
) -> dict[str, Any]:
    labels = _adapt_short_as_long_labels(short)
    dates = np.array([r["date"] for r in rows])
    symbols = np.array([r["symbol"] for r in rows])
    feats = available_features(rows)
    feat_mat = feature_matrix(rows, feats)
    catalog = build_semantic_catalog(feats)
    catalog_by_id = {c["semantic_id"]: c for c in catalog}

    fold_results: dict[str, Any] = {}
    for fold in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(fold)
        print(f"=== SHORT Outer {fold} ===", flush=True)
        inner = run_inner_lodo(
            catalog=catalog,
            feat_mat=feat_mat,
            features=feats,
            labels=labels,
            dates=dates,
            symbols=symbols,
            train_days=train_days,
        )
        print(f"  inner selected={inner['n_selected']}", flush=True)
        outer = evaluate_outer_test(
            catalog_by_id=catalog_by_id,
            selected_ids=inner["selected_ids"],
            feat_mat=feat_mat,
            features=feats,
            labels=labels,
            dates=dates,
            symbols=symbols,
            train_days=train_days,
            test_days=test_days,
        )
        fold_results[fold] = {
            "selected_ids": inner["selected_ids"],
            "outer": outer,
            "inner_n_selected": inner["n_selected"],
        }

    families = aggregate_outer_results(fold_results)
    outer_pass = [sid for sid, f in families.items() if f.get("outer_pass")]
    lodo_pass = []
    survivors = []
    for sid in outer_pass:
        lr = run_lodo(
            spec=catalog_by_id[sid],
            catalog_by_id=catalog_by_id,
            feat_mat=feat_mat,
            features=feats,
            labels=labels,
            dates=dates,
            symbols=symbols,
        )
        if not lr.get("lodo_pass"):
            continue
        sr = run_loso(
            spec=catalog_by_id[sid],
            catalog_by_id=catalog_by_id,
            feat_mat=feat_mat,
            features=feats,
            labels=labels,
            dates=dates,
            symbols=symbols,
        )
        if sr.get("loso_pass"):
            lodo_pass.append(sid)
            survivors.append({
                "semantic_id": sid,
                "outer": families[sid],
                "lodo_positive_days": lr.get("positive_day_count"),
                "max_day_contribution": lr.get("max_day_contribution"),
                "max_symbol_contribution": sr.get("max_symbol_contribution"),
            })

    return {
        "catalog_n": len(catalog),
        "outer_blocks": {k: list(v) for k, v in OUTER_BLOCKS.items()},
        "fold_inner_selected": {f: fold_results[f]["inner_n_selected"] for f in fold_results},
        "outer_pass_count": len(outer_pass),
        "lodo_pass_count": len(lodo_pass),
        "survivors": survivors,
        "short_signal_found": len(survivors) > 0,
        "families_sample": {sid: families[sid] for sid in list(families)[:30]},
    }
