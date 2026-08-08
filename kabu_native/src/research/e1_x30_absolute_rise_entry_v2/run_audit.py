"""E1_X30 runner: Absolute-Rise ENTRY V2 nested CV (no EXIT, no 20260810)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x28_executable_joint.board import load_board_events, verify_board_mapping
from research.e1_x28_executable_joint.replay import build_entry_asks

from . import (
    ANALYSIS_ID,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    FORBIDDEN_FROM,
    HISTORICAL_DAYS,
    OUTER_BLOCKS,
    VERDICT_FOUND,
    VERDICT_NONE,
    X29_V2_PRECOMMIT_SHA,
)
from .cohorts_ref import reference_cohort_metrics
from .cv import (
    aggregate_outer_results,
    evaluate_outer_test,
    outer_train_test,
    run_inner_lodo,
)
from .features import available_features, build_semantic_catalog, feature_matrix
from .labels import compute_label_arrays, label_prevalence
from .population import load_population
from .publish import publish
from .robust import final_refit_manifest, run_lodo, run_loso

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x30_absolute_rise_entry_v2"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x30_absolute_rise_entry_v2.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(tp), "-q", "--tb=line"],
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
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2000:]}


def _load_boards(rows, allowed):
    keys = sorted({(r["date"], r["symbol"]) for r in rows if r["date"] in allowed})
    cache = {}
    print(f"  boards {len(keys)}...", flush=True)

    def _one(k):
        return k, load_board_events(k[0], k[1])

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_one, k) for k in keys]
        done = 0
        for fut in as_completed(futs):
            k, b = fut.result()
            cache[k] = b
            done += 1
            if done % 40 == 0 or done == len(keys):
                print(f"    {done}/{len(keys)}", flush=True)
    return cache


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x30_entryv2_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok"), mapping
    assert mapping.get("mapping_sha") == BOARD_MAPPING_SHA

    rows = load_population()
    dates_arr = np.array([r["date"] for r in rows])
    symbols_arr = np.array([r["symbol"] for r in rows])
    allowed = set(HISTORICAL_DAYS)
    assert not any(d >= FORBIDDEN_FROM for d in dates_arr)

    print("=== boards + entry asks ===", flush=True)
    board_by_key = _load_boards(rows, allowed)
    entry_asks = build_entry_asks(rows, board_by_key)
    entry_asks_b = build_entry_asks(rows, board_by_key)
    ab_entry = bool(np.array_equal(entry_asks["valid"], entry_asks_b["valid"]))

    print("=== absolute-rise labels ===", flush=True)
    label_cache = OUT / "_labels_cache.npz"
    if label_cache.exists():
        print(f"  loading cache {label_cache.name}", flush=True)
        z = np.load(label_cache, allow_pickle=False)
        labels = {k: z[k] for k in z.files}
        if len(labels.get("valid", [])) != len(rows):
            labels = compute_label_arrays(
                rows=rows, entry_asks=entry_asks, board_by_key=board_by_key
            )
            np.savez_compressed(label_cache, **labels)
    else:
        labels = compute_label_arrays(
            rows=rows, entry_asks=entry_asks, board_by_key=board_by_key
        )
        np.savez_compressed(label_cache, **labels)
    prev = label_prevalence(labels)
    print(f"  valid={prev.get('valid_n')} primary_rate={prev.get('primary_rate')}", flush=True)

    print("=== features + semantic catalog ===", flush=True)
    feats = available_features(rows)
    feat_mat = feature_matrix(rows, feats)
    catalog = build_semantic_catalog(feats)
    catalog_by_id = {c["semantic_id"]: c for c in catalog}
    print(f"  features={len(feats)} catalog={len(catalog)}", flush=True)

    # Nested outer CV
    fold_results: dict[str, Any] = {}
    for fold in ("A", "B", "C", "D"):
        train_days, test_days = outer_train_test(fold)
        print(f"=== Outer fold {fold} train={sorted(train_days)} test={sorted(test_days)} ===", flush=True)
        print("  inner LODO...", flush=True)
        inner = run_inner_lodo(
            catalog=catalog,
            feat_mat=feat_mat,
            features=feats,
            labels=labels,
            dates=dates_arr,
            symbols=symbols_arr,
            train_days=train_days,
        )
        print(f"  inner selected={inner['n_selected']}/{inner['n_catalog']}", flush=True)
        print("  outer test (blind)...", flush=True)
        outer = evaluate_outer_test(
            catalog_by_id=catalog_by_id,
            selected_ids=inner["selected_ids"],
            feat_mat=feat_mat,
            features=feats,
            labels=labels,
            dates=dates_arr,
            symbols=symbols_arr,
            train_days=train_days,
            test_days=test_days,
        )
        fold_results[fold] = {
            "selected_ids": inner["selected_ids"],
            "outer": outer,
            "inner_n_selected": inner["n_selected"],
            "train_days": sorted(train_days),
            "test_days": sorted(test_days),
        }
        # persist fold freeze (no retune from test)
        (OUT / f"_fold_{fold}_selected.json").write_text(
            json.dumps({
                "fold": fold,
                "selected_ids": inner["selected_ids"],
                "test_days": sorted(test_days),
            }, indent=2),
            encoding="utf-8",
        )

    print("=== aggregate outer ===", flush=True)
    families = aggregate_outer_results(fold_results)
    outer_pass_ids = [sid for sid, f in families.items() if f.get("outer_pass")]
    print(f"  outer-pass families={len(outer_pass_ids)}", flush=True)

    # LODO / LOSO only for outer-pass
    lodo_results = {}
    loso_results = {}
    lodo_pass_ids = []
    for sid in outer_pass_ids:
        print(f"  LODO {sid}...", flush=True)
        lr = run_lodo(
            spec=catalog_by_id[sid],
            catalog_by_id=catalog_by_id,
            feat_mat=feat_mat,
            features=feats,
            labels=labels,
            dates=dates_arr,
            symbols=symbols_arr,
        )
        lodo_results[sid] = lr
        if not lr.get("lodo_pass"):
            continue
        print(f"  LOSO {sid}...", flush=True)
        sr = run_loso(
            spec=catalog_by_id[sid],
            catalog_by_id=catalog_by_id,
            feat_mat=feat_mat,
            features=feats,
            labels=labels,
            dates=dates_arr,
            symbols=symbols_arr,
        )
        loso_results[sid] = sr
        if sr.get("loso_pass"):
            lodo_pass_ids.append(sid)

    print("=== old49/118 reference (not for selection) ===", flush=True)
    ref = reference_cohort_metrics(
        rows=rows, labels=labels, dates=dates_arr, symbols=symbols_arr
    )

    survivors = []
    for sid in lodo_pass_ids:
        fam = families[sid]
        lr = lodo_results[sid]
        sr = loso_results[sid]
        survivors.append({
            "semantic_id": sid,
            "kind": catalog_by_id[sid]["kind"],
            "primary_first_touch_edge": fam.get("primary_edge"),
            "return_300": fam.get("return_300"),
            "return_600": fam.get("return_600"),
            "mfe": fam.get("mfe"),
            "mae": fam.get("mae"),
            "positive_outer_blocks_300": fam.get("positive_blocks_return_300"),
            "positive_outer_blocks_600": fam.get("positive_blocks_return_600"),
            "positive_lodo_days": lr.get("positive_day_count"),
            "max_day_contribution": lr.get("max_day_contribution"),
            "max_symbol_contribution": sr.get("max_symbol_contribution"),
            "lodo": {
                "positive_day_count": lr.get("positive_day_count"),
                "negative_day_count": lr.get("negative_day_count"),
                "median_daily_executable_return": lr.get("median_daily_executable_return"),
                "worst_day": lr.get("worst_day"),
                "best_day": lr.get("best_day"),
            },
            "loso": {
                "positive_loso": sr.get("positive_loso"),
                "negative_loso": sr.get("negative_loso"),
                "worst_omitted_symbol": sr.get("worst_omitted_symbol"),
                "best_omitted_symbol": sr.get("best_omitted_symbol"),
                "max_symbol_contribution": sr.get("max_symbol_contribution"),
            },
        })

    manifest = None
    manifest_sha = None
    if survivors:
        manifest = final_refit_manifest(
            survivors=survivors,
            catalog_by_id=catalog_by_id,
            feat_mat=feat_mat,
            features=feats,
            dates=dates_arr,
        )
        manifest_sha = manifest["manifest_sha256"]
        (OUT / "ENTRY_V2_MANIFEST_V1.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )
        verdict = VERDICT_FOUND
        exit_allowed = True
    else:
        verdict = VERDICT_NONE
        exit_allowed = False

    # A/B: entry asks already checked; labels via nan-aware equality + sample rescan
    def _arr_eq(a, b):
        if a.dtype == bool or np.issubdtype(a.dtype, np.integer):
            return bool(np.array_equal(a, b))
        return bool(np.allclose(a, b, equal_nan=True))

    ab_labels = _arr_eq(labels["valid"], labels["valid"].copy()) and _arr_eq(
        labels["primary"], labels["primary"].copy()
    ) and _arr_eq(labels["return_300"], labels["return_300"].copy())
    # second independent path: rebuild asks already OK; rescan 200 label episodes
    sample_idx = [i for i in range(len(rows)) if labels["valid"][i]][:200]
    ab_sample = True
    from research.e1_x22_actual_exit_factory.paths import session_end_epoch
    from research.e1_x30_absolute_rise_entry_v2.labels import _scan_episode
    for i in sample_idx:
        r = rows[i]
        board = board_by_key[(r["date"], r["symbol"])]
        ep = _scan_episode(
            board,
            ask=float(entry_asks["ask"][i]),
            ask_t=float(entry_asks["ask_t"][i]),
            sess_end=session_end_epoch(r["date"], r["session"]),
        )
        if bool(ep["ok"]) != bool(labels["valid"][i]):
            ab_sample = False
            break
        if bool(ep["primary"]) != bool(labels["primary"][i]):
            ab_sample = False
            break
        if abs(float(ep["mfe"]) - float(labels["mfe"][i])) > 1e-6:
            ab_sample = False
            break
    ab_labels = bool(ab_sample)

    interim = {
        "run_id": run_id,
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "board_mapping_sha": BOARD_MAPPING_SHA,
        "x29_v2_precommit_sha": X29_V2_PRECOMMIT_SHA,
        "population_n": len(rows),
        "historical_days": list(HISTORICAL_DAYS),
        "outer_blocks": {k: list(v) for k, v in OUTER_BLOCKS.items()},
        "label_prevalence": prev,
        "candidate_semantic_families_generated": len(catalog),
        "features_n": len(feats),
        "entry_only_no_exit": True,
        "opened_20260810": False,
        "ab_entry": ab_entry,
        "ab_labels": ab_labels,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    print("=== tests ===", flush=True)
    tests = _run_tests()

    report = {
        **interim,
        "verdict": verdict,
        "inner_cv_summary": {
            fold: {
                "n_selected": fold_results[fold]["inner_n_selected"],
                "test_days": fold_results[fold]["test_days"],
            }
            for fold in fold_results
        },
        "outer_abcd_results": {
            sid: families[sid] for sid in list(families)[:50]
        },
        "outer_pass_ids": outer_pass_ids,
        "candidate_families_outer_pass": len(outer_pass_ids),
        "candidate_families_lodo_pass": len(lodo_pass_ids),
        "best_entry_families": survivors[:20],
        "old49_comparison": ref["cohorts"].get("ENTRY_V1_Specific49"),
        "old118_comparison": ref["cohorts"].get("ENTRY_V1_Family118"),
        "entry_v2_manifest_created": bool(manifest),
        "manifest_sha": manifest_sha,
        "exit_research_allowed": exit_allowed,
        "opened_20260810": False,
        "must_be_false_20260810": True,
        "ab_determinism": {"entry_asks": ab_entry, "labels": ab_labels},
        "tests": tests,
        "safety": {
            "submit_cancel_live": "0/0/0",
            "paper_only": True,
            "no_runtime_entry_exit_universe_change": True,
            "no_discord_production": True,
        },
        "artifacts": [
            "report.json", "report.md", "audit.xlsx",
            *(["ENTRY_V2_MANIFEST_V1.json"] if manifest else []),
        ],
    }

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": verdict,
            "catalog": len(catalog),
            "outer_pass": len(outer_pass_ids),
            "lodo_pass": len(lodo_pass_ids),
            "manifest_sha": manifest_sha,
            "opened_20260810": False,
        }],
        "outer_families": [
            {
                "semantic_id": sid,
                "outer_pass": f.get("outer_pass"),
                "pos300": f.get("positive_blocks_return_300"),
                "pos600": f.get("positive_blocks_return_600"),
                "primary_edge": f.get("primary_edge"),
                "return_300": f.get("return_300"),
                "return_600": f.get("return_600"),
                "mfe": f.get("mfe"),
                "mae": f.get("mae"),
                "n_blocks": f.get("n_outer_blocks_evaluated"),
            }
            for sid, f in families.items()
        ],
        "survivors": survivors or [{"semantic_id": None}],
        "old_reference": [
            {"name": k, **{kk: vv for kk, vv in v.items() if not isinstance(vv, (dict, list))}}
            for k, v in ref["cohorts"].items()
        ],
        "fold_inner": [
            {"fold": fold, "n_selected": fold_results[fold]["inner_n_selected"]}
            for fold in fold_results
        ],
    }
    publish(OUT, report, sheets)
    print(f"=== DONE verdict={verdict} manifest={bool(manifest)} ===", flush=True)
    return report


if __name__ == "__main__":
    main()
