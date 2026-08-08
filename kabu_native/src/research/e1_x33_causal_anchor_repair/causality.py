"""Causality proof: prefix invariance + dependency manifest."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from research.e1_x14_board_independent_signal.features import attach_path_volume_features
from research.e1_x14_board_independent_signal.grid import build_symbol_day_grid
from research.e1_x14_board_independent_signal.ticks import list_day_symbols, load_symbol_ticks

from . import ANCHOR_ID, CLUSTER_WINDOW_SEC, FORBIDDEN_FROM, HISTORICAL_DAYS, SAMPLING_SEED
from .grid_rebuild import _slim, causal_cluster_first


def _anchors_before(anchors: list[dict[str, Any]], T: float) -> set[tuple]:
    return {
        (a["date"], a["symbol"], a["session"], float(a["grid_epoch"]))
        for a in anchors
        if float(a["grid_epoch"]) <= T + 1e-9
    }


def prefix_invariance_test(
    *,
    n_symbol_days: int = 12,
    n_cuts_per: int = 2,
) -> dict[str, Any]:
    """
    For sampled symbol-days: full-day causal anchors vs prefix-truncated ticks.
    Anchors with grid_epoch <= T must match exactly.
    """
    rng = np.random.default_rng(SAMPLING_SEED)
    pairs = []
    for day in HISTORICAL_DAYS:
        assert day < FORBIDDEN_FROM
        for sym in list_day_symbols(day):
            pairs.append((day, sym))
    if len(pairs) > n_symbol_days:
        pick = rng.choice(len(pairs), size=n_symbol_days, replace=False)
        pairs = [pairs[i] for i in sorted(pick)]

    results = []
    violations = 0
    for day, sym in pairs:
        ticks = load_symbol_ticks(day, sym)
        if len(ticks) < 50:
            continue
        # full
        grids_full = attach_path_volume_features(
            build_symbol_day_grid(day, sym, ticks, f"push_jsonl_{day}"), ticks
        )
        grids_full = [_slim(r) for r in grids_full]
        anch_full = causal_cluster_first(grids_full)
        if len(anch_full) < 2:
            continue
        # cuts at mid points of anchor times
        epochs = sorted(float(a["grid_epoch"]) for a in anch_full)
        cut_candidates = []
        if len(epochs) >= 2:
            cut_candidates.append(epochs[len(epochs) // 2])
        if len(epochs) >= 4:
            cut_candidates.append(epochs[len(epochs) * 3 // 4])
        cut_candidates = cut_candidates[:n_cuts_per]
        for T in cut_candidates:
            ticks_p = [t for t in ticks if float(t["t"]) <= T + 1e-9]
            if len(ticks_p) < 20:
                continue
            grids_p = attach_path_volume_features(
                build_symbol_day_grid(day, sym, ticks_p, f"push_jsonl_{day}"), ticks_p
            )
            grids_p = [_slim(r) for r in grids_p]
            anch_p = causal_cluster_first(grids_p)
            full_set = _anchors_before(anch_full, T)
            pref_set = _anchors_before(anch_p, T)
            ok = full_set == pref_set
            if not ok:
                violations += 1
            results.append({
                "date": day,
                "symbol": sym,
                "T": T,
                "full_n_le_T": len(full_set),
                "prefix_n_le_T": len(pref_set),
                "match": ok,
                "only_full": sorted(full_set - pref_set)[:5],
                "only_prefix": sorted(pref_set - full_set)[:5],
            })

    status = "PASS" if violations == 0 and len(results) >= 5 else (
        "CAUSALITY_VIOLATION" if violations > 0 else "INSUFFICIENT_TESTS"
    )
    return {
        "status": status,
        "n_tests": len(results),
        "violations": violations,
        "sample": results[:20],
        "prefix_invariance": status == "PASS",
    }


def dependency_manifest() -> dict[str, Any]:
    body = {
        "manifest_id": "CAUSAL_ANCHOR_DEPENDENCY_MANIFEST_V1",
        "anchor_id": ANCHOR_ID,
        "allowed_inputs": [
            "push_jsonl ticks (t <= grid_epoch as-of)",
            "10s grid quality_status / CurrentPrice",
            "attach_path_volume_features lookbacks",
            "feature_status == OK",
            f"CLUSTER_WINDOW_SEC == {CLUSTER_WINDOW_SEC}",
            "CLUSTER_FIRST_ANCHOR representative",
        ],
        "forbidden_inputs": [
            "forward_return_60s",
            "forward_return_180s",
            "forward_return_*",
            "future MFE/MAE",
            "future labels",
            "post-anchor path for membership",
            "attach_forward_labels",
        ],
        "source_functions": [
            "research.e1_x14_board_independent_signal.grid.build_symbol_day_grid",
            "research.e1_x14_board_independent_signal.features.attach_path_volume_features",
            "research.e1_x33_causal_anchor_repair.grid_rebuild.causal_cluster_first",
        ],
        "uses_future_information": False,
        "cluster_window_sec": CLUSTER_WINDOW_SEC,
        "cluster_first_semantic": "CLUSTER_FIRST_ANCHOR",
        "no_anchor_grid_search": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body
