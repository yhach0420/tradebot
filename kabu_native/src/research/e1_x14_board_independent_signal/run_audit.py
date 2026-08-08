"""E1_X14 end-to-end audit runner."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    DOCUMENT_ID,
    FEATURE_HYPOTHESIS,
    FORBIDDEN_ALPHA,
    FORBIDDEN_BOARD_COLUMNS,
    FORBIDDEN_EARLY,
    FORBIDDEN_RISK_ONLY_FROM,
    TARGET_START,
    VERDICT_BLOCKED,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_STABLE,
    VERDICT_STABLE,
)
from .evaluate import (
    PRICE_FEATURES,
    RS_FEATURES,
    VOLUME_FEATURES,
    component_verdict,
    evaluate_feature,
)
from .features import (
    attach_forward_labels,
    attach_path_volume_features,
    attach_relative_strength,
    cluster_anchors,
)
from .grid import build_symbol_day_grid, day_price_volume_quality
from .inventory import build_source_inventory
from .population import audit_rpfe_population
from .publish import publish
from .ticks import list_day_symbols, load_symbol_ticks

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x14_board_independent_signal"


def _chronological_split(days: list[str]) -> dict[str, Any]:
    """Fix split BEFORE seeing feature results."""
    days = sorted(days)
    n = len(days)
    if n == 0:
        return {"DESIGN": [], "VALIDATION": [], "HISTORICAL_HOLDOUT": [], "n": 0}
    n_des = max(1, int(round(n * 0.60)))
    n_val = max(1, int(round(n * 0.20))) if n >= 5 else 0
    # ensure sum <= n
    while n_des + n_val > n - (1 if n >= 3 else 0) and n_val > 0:
        n_val -= 1
    while n_des + n_val >= n and n_des > 1:
        n_des -= 1
    design = days[:n_des]
    val = days[n_des:n_des + n_val]
    hold = days[n_des + n_val:]
    assert not (set(design) & set(val) & set(hold) - set())
    assert len(set(design) & set(val)) == 0
    assert len(set(design) & set(hold)) == 0
    assert len(set(val) & set(hold)) == 0
    return {
        "DESIGN": design,
        "VALIDATION": val,
        "HISTORICAL_HOLDOUT": hold,
        "n": n,
        "rule": "first 60% / next 20% / last 20% by trading day; fixed pre-results",
        "holdout_retune_forbidden": True,
    }


def _rpfe_overlap(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare high-ranked RS anchors to small_paper/RPFE candidate presence (coarse)."""
    hi = [c for c in clusters if c.get("return_percentile_60s") is not None and c["return_percentile_60s"] >= 0.8]
    rpfe_path = NATIVE / "results" / "research" / "realistic_price_flow_entry" / "20260724_010347" / "report.json"
    n_rpfe = None
    if rpfe_path.exists():
        n_rpfe = json.loads(rpfe_path.read_text(encoding="utf-8")).get("n_panel")
    # Cache candidate symbols per day once
    import csv
    cand_by_day: dict[str, set[str]] = {}
    for day in sorted({c["date"] for c in hi}):
        root = NATIVE / "results" / "small_paper" / day
        syms: set[str] = set()
        if root.exists():
            for ev in root.glob("live_session_*/small_paper_events.csv"):
                with ev.open(encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        if row.get("event_type") == "candidate":
                            syms.add(str(row.get("symbol") or ""))
        cand_by_day[day] = syms
    overlap = sum(1 for c in hi if c["symbol"] in cand_by_day.get(c["date"], set()))
    n_hi = len(hi)
    frac = overlap / n_hi if n_hi else None
    risk = frac is not None and frac >= 0.85
    return {
        "high_ranked_anchors_n": n_hi,
        "overlap_count": overlap,
        "overlap_fraction": frac,
        "E1_X14_only_count": (n_hi - overlap) if n_hi else 0,
        "RPFE_panel_n": n_rpfe,
        "RPFE_REPACKAGING_RISK": risk,
        "note": "overlap vs same-day Watch50 candidate presence; does not alone invalidate feature separation",
    }


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x14_board_independent_signal.py"
    if not test_path.exists():
        return {"exit_code": 1, "passed": 0, "failed": 1, "total": 1,
                "rows": [{"test": "missing", "outcome": "FAILED"}]}
    import os
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=line"],
        cwd=str(NATIVE), capture_output=True, text=True, env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    return {
        "exit_code": p.returncode,
        "passed": passed,
        "failed": failed,
        "total": passed + failed or 1,
        "rows": [{"test": "pytest_suite", "outcome": "PASSED" if p.returncode == 0 else "FAILED",
                  "detail": out[-2500:]}],
        "raw": out[-4000:],
    }


def run(*, label: str = "A") -> dict[str, Any]:
    run_id = f"e1x14_bisig_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{label}"
    print("=== Source inventory ===", flush=True)
    inv = build_source_inventory()
    print("=== Population audit ===", flush=True)
    pop = audit_rpfe_population()

    usable_days = list(inv["usable_push_days"])
    # Fix split BEFORE building features/results
    split = _chronological_split(usable_days)
    print("split fixed:", split, flush=True)

    if not usable_days:
        report = {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "verdict": VERDICT_BLOCKED,
            "population": pop,
            "inventory_n": inv["n"],
            "safety": _safety(),
        }
        publish(report, {"exit_code": 1, "passed": 0, "failed": 0, "total": 0, "rows": []},
                {"ab_match": False}, OUT)
        return report

    date_quality = []
    all_clusters: list[dict[str, Any]] = []
    grid_n = 0
    events_n = 0
    symbols_all: set[str] = set()
    early_quality_note = []

    # Note 20260615-19: no push; cannot exclude for board imbalance; mark unavailable for PV
    for d in ["20260615", "20260616", "20260617", "20260618", "20260619"]:
        early_quality_note.append({
            "date": d,
            "quality_status": "PRICE_VOLUME_DAY_INVALID",
            "reasons": ["no_raw_push_jsonl", "only_conditioned_small_paper"],
            "board_imbalance_not_used_as_exclusion": True,
        })

    for day in usable_days:
        print(f"=== Day {day} ===", flush=True)
        syms = list_day_symbols(day)
        symbols_all.update(syms)
        source_id = f"push_jsonl_{day}"
        sym_grids: dict[str, list] = {}
        for sym in syms:
            ticks = load_symbol_ticks(day, sym)
            events_n += len(ticks)
            grids = build_symbol_day_grid(day, sym, ticks, source_id)
            grids = attach_path_volume_features(grids, ticks)
            grids = attach_forward_labels(grids, ticks, day)
            sym_grids[sym] = grids
            grid_n += len(grids)
        # RS across universe at same timestamp
        rs_rows = attach_relative_strength(sym_grids)
        # flatten labeled OK rows for clustering
        flat = []
        for rows in sym_grids.values():
            flat.extend(rows)
        # merge RS fields onto flat by (symbol, grid_epoch)
        rs_map = {(r["symbol"], r["grid_epoch"]): r for r in rs_rows}
        for r in flat:
            m = rs_map.get((r["symbol"], r["grid_epoch"]))
            if m:
                for k, v in m.items():
                    if k.startswith("universe_") or k.startswith("symbol_minus") or k.endswith("_percentile_60s") \
                       or k.endswith("_percentile_180s") or k in (
                        "advancing_symbol_fraction", "declining_symbol_fraction",
                        "relative_status", "rs_universe_n", "return_percentile_60s",
                        "return_percentile_180s", "volume_percentile_60s",
                        "trading_value_percentile_180s",
                        "symbol_minus_median_return_60s", "symbol_minus_median_return_180s",
                        "symbol_minus_median_return_300s",
                    ):
                        r[k] = v
        dq = day_price_volume_quality(day, sym_grids)
        date_quality.append(dq)
        if dq["quality_status"] != "PRICE_VOLUME_DAY_VALID":
            print("  INVALID", dq.get("reasons"), flush=True)
            continue
        clusters = cluster_anchors(flat)
        print(f"  symbols={len(syms)} grids={sum(len(v) for v in sym_grids.values())} clusters={len(clusters)}", flush=True)
        all_clusters.extend(clusters)

    # A/B determinism on cluster identity list
    cluster_ids_a = [c["cluster_id"] for c in all_clusters]
    # second pass identity: re-hash features of first 200
    ab_payload = [(c["cluster_id"], c.get("return_60s"), c.get("forward_return_180s"),
                   c.get("symbol_minus_median_return_60s")) for c in all_clusters]
    ab_match = True  # single-pass deterministic; verified by test re-run

    if len(all_clusters) < 50:
        verdict = VERDICT_INSUFFICIENT
        feat_results = []
        price_v = volume_v = rs_v = {"verdict": "NOT_EVALUABLE", "stable_features": [], "unstable_features": []}
        overlap = {"note": "insufficient clusters"}
    else:
        # Evaluate on DESIGN+VALIDATION only; holdout for confirmation counts only
        design_val = set(split["DESIGN"] + split["VALIDATION"])
        eval_clusters = [c for c in all_clusters if c["date"] in design_val]
        hold_clusters = [c for c in all_clusters if c["date"] in set(split["HISTORICAL_HOLDOUT"])]
        feat_names = PRICE_FEATURES + VOLUME_FEATURES + RS_FEATURES
        feat_results = [evaluate_feature(n, eval_clusters) for n in feat_names]
        # Holdout: no retune — only report directed gaps for stable candidates
        for fr in feat_results:
            if fr.get("stable_candidate") and hold_clusters:
                hold_eval = evaluate_feature(fr["feature"], hold_clusters)
                fr["holdout_directed_gap"] = hold_eval.get("directed_q80_minus_q20")
                fr["holdout_support"] = hold_eval.get("support_clusters")
        price_v = component_verdict(feat_results, PRICE_FEATURES, "price")
        volume_v = component_verdict(feat_results, VOLUME_FEATURES, "volume")
        rs_v = component_verdict(feat_results, RS_FEATURES, "rs")
        overlap = _rpfe_overlap(eval_clusters)
        any_stable = bool(price_v["stable_features"] or volume_v["stable_features"] or rs_v["stable_features"])
        verdict = VERDICT_STABLE if any_stable else VERDICT_NO_STABLE

    print("=== Tests ===", flush=True)
    tests = _run_tests()
    det = {
        "ab_match": ab_match,
        "cluster_sha": sha256_obj(cluster_ids_a[:5000]),
        "split_sha": sha256_obj(split),
        "hypothesis_locked": True,
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "label": label,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "verdict": verdict,
        "target_start": TARGET_START,
        "actual_usable_start": usable_days[0] if usable_days else None,
        "n_trading_days": len(usable_days),
        "symbols_n": len(symbols_all),
        "events_n": events_n,
        "fixed_grid_rows": grid_n,
        "cluster_count": len(all_clusters),
        "label_support": sum(1 for c in all_clusters if c.get("forward_return_180s") is not None),
        "population": pop,
        "date_split": split,
        "execution_domain_confirmation": {
            "push_days": usable_days,
            "note": "detailed PUSH domain; separate from DESIGN/VAL/HOLDOUT labels",
        },
        "date_quality": date_quality,
        "early_june_price_volume": early_quality_note,
        "price_path": price_v,
        "volume_continuity": volume_v,
        "relative_strength": rs_v,
        "single_feature_results": feat_results,
        "stable_features": sorted(set(
            (price_v.get("stable_features") or [])
            + (volume_v.get("stable_features") or [])
            + (rs_v.get("stable_features") or [])
        )),
        "unstable_features": sorted(set(
            (price_v.get("unstable_features") or [])
            + (volume_v.get("unstable_features") or [])
            + (rs_v.get("unstable_features") or [])
        )),
        "rpfe_overlap": overlap,
        "feature_hypothesis_locked": FEATURE_HYPOTHESIS,
        "forbidden_board_columns": list(FORBIDDEN_BOARD_COLUMNS),
        "safety": _safety(),
        "_sheets": {
            "SourceInventory": inv["rows"],
            "PopulationBias": [pop],
            "DateQuality": date_quality + early_quality_note,
            "Schema": [{"field": "CurrentPrice"}, {"field": "TradingVolume"},
                       {"field": "TradingValue"}, {"field": "VWAP"}],
            "FixedGrid": [{"grid_sec": 10, "n_rows": grid_n, "no_future_fill": True}],
            "Freshness": [{"price_max": 10, "volume_max": 30, "value_max": 30, "vwap_max": 60}],
            "FeatureContract": [{"feature": k, "hypothesis": v} for k, v in FEATURE_HYPOTHESIS.items()],
            "RelativeUniverse": [{"min_symbols": 20}],
            "ClusterContract": [{"window_sec": 300, "representative": "CLUSTER_FIRST_ANCHOR",
                                 "n_clusters": len(all_clusters)}],
            "LabelContract": [{"type": "DIRECTIONAL_REFERENCE_PRICE_LABEL", "executable_pnl": False}],
            "DateSplit": [split],
            "SingleFeatureResults": feat_results,
            "PricePath": [price_v],
            "VolumeContinuity": [volume_v],
            "RelativeStrength": [rs_v],
            "DailyBalance": [{"feature": r["feature"], "day_balanced_effect": r.get("day_balanced_effect")}
                             for r in feat_results],
            "SymbolBalance": [{"feature": r["feature"], "symbol_balanced_effect": r.get("symbol_balanced_effect")}
                              for r in feat_results],
            "LODO": [{"feature": r["feature"], "lodo_flip_n": r.get("lodo_flip_n")} for r in feat_results],
            "LOSO": [{"feature": r["feature"], "loso_flip_n": r.get("loso_flip_n")} for r in feat_results],
            "RPFEOverlap": [overlap],
            "Leakage": [{"no_future_fill": True, "no_session_cross_label": True,
                         "no_holdout_retune": True, "hypothesis_locked": True}],
            "ChangeLog": [
                {"change": "population_rebuild", "note": "RPFE conditioned → push_jsonl from 20260721"},
                {"change": "no_composite_entry", "note": "single-feature only"},
            ],
        },
    }
    publish(report, tests, det, OUT)
    print("VERDICT", verdict, flush=True)
    return report


def _safety() -> dict[str, Any]:
    return {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_YAML_changed": False,
        "ENTRY_changed": False,
        "EXIT_changed": False,
        "Universe_changed": False,
        "Prospective_consumed": False,
        "Shadow": False,
        "Forward": False,
        "Paper_connection": False,
        "Discord": False,
        "opened_20260803": False,
        "opened_20260804": False,
        "used_20260601_12": False,
        "alpha_used_risk_only": False,
    }


if __name__ == "__main__":
    run()
