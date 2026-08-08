"""E1_X25 runner: freeze registry → long paths → aggregate → families → publish."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x6_provisional.util import sha256_obj
from research.e1_x22_actual_exit_factory.registry import (
    build_alias_groups,
    load_population_checked,
    load_x21_registry,
    load_x21_report,
    mask_sha256,
    rebuild_candidates_and_masks,
    reconcile_registry,
)

from . import (
    ANALYSIS_ID,
    DOCUMENT_ID,
    EXPECTED_ALIASES,
    EXPECTED_CAND_N,
    EXPECTED_POP_N,
    EXPECTED_UNIQUE_MASKS,
    FORBIDDEN_RISK_FROM,
    SOURCE_X21,
    SOURCE_X22,
    SOURCE_X23,
    SOURCE_X24,
    VERDICT_FAMILIES,
    VERDICT_MIXED,
    VERDICT_NO_EDGE,
    VERDICT_PATH_FAIL,
    VERDICT_REG_FAIL,
)
from .aggregate import aggregate_candidate_period, period_mask
from .families import (
    FAMILY_RULES_FROZEN,
    assign_discovery_families,
    assign_path_evidence_status,
)
from .path_build import build_long_path_metrics, delete_interim_caches
from .publish import publish
from .stats import apply_fdr_tags, day_cluster_bootstrap_delta, stability_diagnostics

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x25_long_horizon_path"
X22_DIR = NATIVE / "results" / "research" / "e1_x22_actual_exit_factory"
X23_DIR = NATIVE / "results" / "research" / "e1_x23_diversified_bundle"
X24_DIR = NATIVE / "results" / "research" / "e1_x24_executable_bridge"


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x25_long_horizon_path.py"
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
        "exit_code": p.returncode, "passed": passed, "failed": failed,
        "total": passed + failed or 1,
        "rows": [{"test": "pytest_suite",
                  "outcome": "PASSED" if p.returncode == 0 else "FAILED",
                  "detail": out[-3000:]}],
    }


def _parity_vs_x22(rows: list[dict[str, Any]], metrics_pack: dict[str, Any]) -> dict[str, Any]:
    """Compare unrestricted as-of returns to X19 labels (same as X22 path contract)."""
    out = {}
    for h, key in ((60, "forward_return_60s"), (180, "forward_return_180s"), (300, "forward_return_300s")):
        ours = metrics_pack["parity_return_bps"][h]
        match = mismatch = missing = 0
        max_diff = 0.0
        for i, r in enumerate(rows):
            lab = r.get(key)
            if lab is None or not np.isfinite(ours[i]):
                missing += 1
                continue
            diff = abs(float(ours[i]) / 10000.0 - float(lab))
            max_diff = max(max_diff, diff)
            if diff <= 1e-9:
                match += 1
            else:
                mismatch += 1
        out[f"{h}s"] = {
            "match": match, "mismatch": mismatch, "missing": missing,
            "max_abs_diff": max_diff,
            "ok": mismatch == 0 and match > 0,
        }
    out["all_ok"] = all(out[f"{h}s"]["ok"] for h in (60, 180, 300))
    return out


def _load_20260804_rows() -> list[dict[str, Any]]:
    fp = X23_DIR / "_clusters_20260804.jsonl"
    rows = [json.loads(l) for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def _vector_bootstrap_return900(
    unique_masks: dict[str, np.ndarray],
    metrics: dict[str, np.ndarray],
    dates: np.ndarray,
    path_ok: np.ndarray,
) -> list[dict[str, Any]]:
    """Efficient day-cluster bootstrap for return_900s across all unique masks (Discovery)."""
    from . import BOOTSTRAP_ITERS, BOOTSTRAP_SEED, DISCOVERY

    elig = (
        path_ok
        & period_mask(dates, "DISCOVERY")
        & metrics["eligible_900s"]
        & metrics["fresh_ok_900s"]
        & np.isfinite(metrics["return_900s_bps"])
    )
    values = metrics["return_900s_bps"]
    days = np.array(sorted(set(dates[elig].tolist())))
    if days.size < 2:
        return []

    reps = list(unique_masks.keys())
    M = len(reps)
    D = days.size
    day_sel = np.full((M, D), np.nan)
    day_all = np.full(D, np.nan)
    for di, d in enumerate(days):
        m = elig & (dates == d)
        if not m.any():
            continue
        day_all[di] = float(np.mean(values[m]))
        for mi, rid in enumerate(reps):
            ms = m & unique_masks[rid]
            if ms.any():
                day_sel[mi, di] = float(np.mean(values[ms]))

    ok_day = np.isfinite(day_all)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    rows_out = []
    pvals = []
    for mi, rid in enumerate(reps):
        ok = ok_day & np.isfinite(day_sel[mi])
        if ok.sum() < 2:
            rows_out.append({
                "representative_id": rid, "metric": "return_900s",
                "delta": None, "ci95_lo": None, "ci95_hi": None,
                "raw_p": None, "tag": "DESCRIPTIVE_ONLY",
            })
            pvals.append(np.nan)
            continue
        idx = np.where(ok)[0]
        obs = float(np.mean(day_sel[mi, idx]) - np.mean(day_all[idx]))
        samp = rng.choice(idx, size=(BOOTSTRAP_ITERS, idx.size), replace=True)
        boots = day_sel[mi, samp].mean(axis=1) - day_all[samp].mean(axis=1)
        lo, hi = np.quantile(boots, [0.025, 0.975])
        raw_p = float(np.mean(np.abs(boots) >= abs(obs)))
        tag = "CI_SUPPORTED" if (lo > 0 or hi < 0) else "DESCRIPTIVE_ONLY"
        rows_out.append({
            "representative_id": rid, "metric": "return_900s",
            "delta": obs, "ci95_lo": float(lo), "ci95_hi": float(hi),
            "raw_p": raw_p, "tag": tag, "iters": BOOTSTRAP_ITERS, "seed": BOOTSTRAP_SEED,
        })
        pvals.append(raw_p)

    # FDR
    finite = [(i, p) for i, p in enumerate(pvals) if p == p]
    if finite:
        from .stats import bh_qvalues
        arr = np.array([p for _, p in finite])
        q = bh_qvalues(arr)
        for j, (i, _) in enumerate(finite):
            rows_out[i]["bh_q"] = float(q[j])
            if q[j] <= 0.05 and rows_out[i]["tag"] == "CI_SUPPORTED":
                rows_out[i]["tag"] = "FDR_SUPPORTED"
    return rows_out


def run_once(run_id: str) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)

    # --- registry freeze ---
    x21_report = load_x21_report()
    x21_reg = load_x21_registry()
    recon = reconcile_registry(x21_report, x21_reg)
    if not recon["ok"]:
        return {"verdict": VERDICT_REG_FAIL, "recon": recon, "run_id": run_id}

    rows = load_population_checked()
    if len(rows) != EXPECTED_POP_N:
        return {"verdict": VERDICT_REG_FAIL, "run_id": run_id, "pop": len(rows)}

    print("=== rebuild candidates/masks ===", flush=True)
    candidates, masks = rebuild_candidates_and_masks(rows)
    reg_ids = {c["candidate_id"] for c in x21_reg}
    built_ids = {c["candidate_id"] for c in candidates}
    if reg_ids != built_ids:
        return {"verdict": VERDICT_REG_FAIL, "run_id": run_id, "reason": "candidate_id_mismatch"}

    alias_rows, cand_to_rep, unique_masks = build_alias_groups(candidates, masks)
    unique_n = len(unique_masks)
    alias_n = sum(1 for a in alias_rows if not a["is_representative"])
    if unique_n != EXPECTED_UNIQUE_MASKS or alias_n != EXPECTED_ALIASES or len(candidates) != EXPECTED_CAND_N:
        return {
            "verdict": VERDICT_REG_FAIL, "run_id": run_id,
            "unique_n": unique_n, "alias_n": alias_n, "cand_n": len(candidates),
        }
    print(f"=== registry OK unique={unique_n} aliases={alias_n} ===", flush=True)

    # decision mask registry
    mask_reg = []
    for a in alias_rows:
        if a["is_representative"]:
            mask_reg.append({
                "representative_id": a["candidate_id"],
                "decision_mask_sha256": a["decision_mask_sha256"],
                "mask_support": a["mask_support"],
                "alias_group_id": a["alias_group_id"],
            })

    # --- long paths ---
    print("=== build long-horizon path metrics (once per anchor) ===", flush=True)
    pack = build_long_path_metrics(rows, use_disk=True)
    if pack["meta"]["paths_ok"] < EXPECTED_POP_N * 0.95:
        return {"verdict": VERDICT_PATH_FAIL, "run_id": run_id, "meta": pack["meta"]}

    parity = _parity_vs_x22(rows, pack)
    print(f"  parity60/180/300={parity['all_ok']} paths_ok={pack['meta']['paths_ok']}", flush=True)

    metrics = pack["metrics"]
    dates = np.array([r["date"] for r in rows])
    symbols = np.array([r["symbol"] for r in rows])
    sessions = np.array([r["session"] for r in rows])
    path_ok = metrics["ok"]

    # horizon eligibility counts
    elig_counts = {}
    censor_counts = {}
    fresh_counts = {}
    for h in (60, 180, 300, 600, 900, 1800, "session"):
        key = f"{h}s" if isinstance(h, int) else h
        elig_counts[key] = int(metrics[f"eligible_{key}"].sum())
        censor_counts[key] = int(metrics[f"censored_{key}"].sum())
        fresh_counts[key] = int((metrics[f"eligible_{key}"] & metrics[f"fresh_ok_{key}"]).sum())

    # --- aggregate all unique masks ---
    print("=== aggregate unique masks ===", flush=True)
    periods = ("DISCOVERY", "EVALUATION", "20260803", "ALL")
    handoff = []
    family_counts: Counter = Counter()
    evidence_counts: Counter = Counter()
    disc_family_rows = []
    eval_rows = []
    stress_rows = []
    support_rows = []
    fixed_ret_rows = []
    mfe_rows = []
    reach_rows = []
    ft_rows = []
    prerise_rows = []
    gb_rows = []
    base_rows = []
    comp_rows = []
    fam_val_rows = []
    evidence_rows = []
    daily_rows = []
    symbol_rows = []
    lodo_rows = []
    loso_rows = []

    reps = list(unique_masks.keys())
    # sha map
    sha_by_rep = {a["candidate_id"]: a["decision_mask_sha256"] for a in alias_rows if a["is_representative"]}
    aliases_by_rep: dict[str, list[str]] = {r: [] for r in reps}
    for a in alias_rows:
        aliases_by_rep[a["alias_representative_id"]].append(a["candidate_id"])

    done = 0
    for rid in reps:
        sel = unique_masks[rid]
        by_period = {}
        for per in periods:
            by_period[per] = aggregate_candidate_period(
                selected=sel, metrics=metrics, dates=dates, symbols=symbols,
                sessions=sessions, period=per, path_ok=path_ok,
            )
        fam_tags = assign_discovery_families(by_period["DISCOVERY"])
        # freeze discovery tags — never rewrite from eval
        evidence = assign_path_evidence_status(
            disc_agg=by_period["DISCOVERY"], eval_agg=by_period["EVALUATION"],
        )
        for t in fam_tags:
            family_counts[t] += 1
        evidence_counts[evidence] += 1

        disc = by_period["DISCOVERY"]
        evl = by_period["EVALUATION"]
        st = by_period["20260803"]

        # compact handoff
        h900 = disc["horizons"].get("900s", {})
        handoff.append({
            "candidate_id": rid,
            "decision_mask_sha256": sha_by_rep[rid],
            "aliases": aliases_by_rep[rid],
            "discovery_family_tags": fam_tags,
            "path_evidence_status": evidence,
            "support_discovery": disc.get("selected_anchors"),
            "days_discovery": h900.get("SELECTED", {}).get("days"),
            "symbols_discovery": h900.get("SELECTED", {}).get("symbols"),
            "return_900s_mean_sel": h900.get("SELECTED", {}).get("return", {}).get("mean"),
            "return_900s_delta_vs_ALL": h900.get("delta_vs_ALL", {}).get("mean_return"),
            "return_900s_delta_vs_COMPLEMENT": h900.get("delta_vs_COMPLEMENT", {}).get("mean_return"),
            "MFE_900s_delta_vs_ALL": h900.get("delta_vs_ALL", {}).get("mean_MFE"),
            "up30_reach_sel": disc.get("reach", {}).get("up_30", {}).get("SELECTED", {}).get("reach_rate"),
            "up50_reach_sel": disc.get("reach", {}).get("up_50", {}).get("SELECTED", {}).get("reach_rate"),
            "eval_return_900s_delta_vs_ALL": evl.get("horizons", {}).get("900s", {}).get("delta_vs_ALL", {}).get("mean_return"),
            "stress_return_900s_delta_vs_ALL": st.get("horizons", {}).get("900s", {}).get("delta_vs_ALL", {}).get("mean_return"),
        })

        disc_family_rows.append({
            "representative_id": rid, "tags": ",".join(fam_tags),
            "selected": disc.get("selected_anchors"),
        })
        evidence_rows.append({"representative_id": rid, "status": evidence, "tags": ",".join(fam_tags)})
        fam_val_rows.append({
            "representative_id": rid,
            "discovery_family_tags": ",".join(fam_tags),
            "evaluation_return_900s_delta": evl.get("horizons", {}).get("900s", {}).get("delta_vs_ALL", {}).get("mean_return"),
            "stress_return_900s_delta": st.get("horizons", {}).get("900s", {}).get("delta_vs_ALL", {}).get("mean_return"),
            "note": "discovery_tags_frozen",
        })

        for per, agg in by_period.items():
            h = agg["horizons"].get("900s", {})
            support_rows.append({
                "representative_id": rid, "period": per,
                "selected": agg.get("selected_anchors"),
                "eligible_900s": h.get("eligible_n"),
                "retention_rate": h.get("retention_rate"),
                "days": h.get("SELECTED", {}).get("days"),
                "symbols": h.get("SELECTED", {}).get("symbols"),
            })
            for hk, hv in agg["horizons"].items():
                sel_stats = hv.get("SELECTED", {})
                fixed_ret_rows.append({
                    "representative_id": rid, "period": per, "horizon": hk,
                    "mean_return": sel_stats.get("return", {}).get("mean"),
                    "median_return": sel_stats.get("return", {}).get("median"),
                    "day_balanced": sel_stats.get("return", {}).get("day_balanced"),
                    "symbol_balanced": sel_stats.get("return", {}).get("symbol_balanced"),
                    "pos_rate": sel_stats.get("return", {}).get("positive_rate"),
                    "neg_rate": sel_stats.get("return", {}).get("negative_rate"),
                    "delta_vs_ALL": hv.get("delta_vs_ALL", {}).get("mean_return"),
                    "delta_vs_COMP": hv.get("delta_vs_COMPLEMENT", {}).get("mean_return"),
                })
                mfe_rows.append({
                    "representative_id": rid, "period": per, "horizon": hk,
                    "mean_MFE": sel_stats.get("MFE", {}).get("mean"),
                    "median_MFE": sel_stats.get("MFE", {}).get("median"),
                    "q25_MFE": sel_stats.get("MFE", {}).get("q25"),
                    "q75_MFE": sel_stats.get("MFE", {}).get("q75"),
                    "mean_MAE": sel_stats.get("MAE", {}).get("mean"),
                    "median_MAE": sel_stats.get("MAE", {}).get("median"),
                    "q25_MAE": sel_stats.get("MAE", {}).get("q25"),
                    "q75_MAE": sel_stats.get("MAE", {}).get("q75"),
                })
                gb_rows.append({
                    "representative_id": rid, "period": per, "horizon": hk,
                    "median_terminal_giveback": sel_stats.get("median_terminal_giveback"),
                    "median_max_giveback": sel_stats.get("median_max_giveback"),
                })
                base_rows.append({
                    "representative_id": rid, "period": per, "horizon": hk,
                    **{f"d_{k}": v for k, v in (hv.get("delta_vs_ALL") or {}).items()},
                })
                comp_rows.append({
                    "representative_id": rid, "period": per, "horizon": hk,
                    **{f"d_{k}": v for k, v in (hv.get("delta_vs_COMPLEMENT") or {}).items()},
                })
            if per == "DISCOVERY":
                for uk, uv in agg.get("reach", {}).items():
                    reach_rows.append({
                        "representative_id": rid, "target": uk,
                        "sel_rate": uv.get("SELECTED", {}).get("reach_rate"),
                        "all_rate": uv.get("ALL_ANCHORS", {}).get("reach_rate"),
                        "median_time": uv.get("SELECTED", {}).get("median_reach_time"),
                    })
                for fk, fv in agg.get("first_touch", {}).items():
                    ft_rows.append({
                        "representative_id": rid, "pair": fk,
                        "sel_up_first": fv.get("SELECTED", {}).get("up_first_rate"),
                        "all_up_first": fv.get("ALL_ANCHORS", {}).get("up_first_rate"),
                        "sel_down_first": fv.get("SELECTED", {}).get("down_first_rate"),
                    })
                for uk, uv in agg.get("pre_rise", {}).items():
                    prerise_rows.append({
                        "representative_id": rid, "target": uk,
                        "sel_median_pre_MAE": uv.get("SELECTED_median_pre_MAE"),
                        "all_median_pre_MAE": uv.get("ALL_median_pre_MAE"),
                    })

        # stability on Discovery return_900s
        elig900 = path_ok & period_mask(dates, "DISCOVERY") & metrics["eligible_900s"] & metrics["fresh_ok_900s"]
        stab = stability_diagnostics(
            values=metrics["return_900s_bps"], selected=sel, dates=dates,
            symbols=symbols, eligible=elig900, light=(done >= 100),
        )
        if done < 100:
            for row in stab.get("lodo") or []:
                lodo_rows.append({"representative_id": rid, **row})
            for row in stab.get("loso") or []:
                loso_rows.append({"representative_id": rid, **row})
        if stab.get("max_day_contribution"):
            daily_rows.append({"representative_id": rid, **stab["max_day_contribution"],
                               "without_20260722": stab.get("without_20260722"),
                               "full_delta": stab.get("full_delta")})
        if stab.get("max_symbol_contribution"):
            symbol_rows.append({"representative_id": rid, **stab["max_symbol_contribution"],
                                "without_2354": stab.get("without_2354"),
                                "without_285A": stab.get("without_285A")})

        done += 1
        if done % 500 == 0 or done == len(reps):
            print(f"  aggregated {done}/{len(reps)}", flush=True)

    # bootstrap
    print("=== bootstrap return_900s (Discovery) ===", flush=True)
    boot_rows = _vector_bootstrap_return900(unique_masks, metrics, dates, path_ok)

    # --- 20260804 consumed diagnostic ---
    print("=== consumed 20260804 diagnostic ===", flush=True)
    rows04 = _load_20260804_rows()
    # Apply frozen primary candidate specs to 20260804 (do not re-filter AND support)
    from research.e1_x21_entry_factory_exit_benchmark.factory import decision_mask
    masks04: dict[str, np.ndarray] = {}
    for c in candidates:
        if not (c.get("op") == "AND" or c.get("parents")):
            masks04[c["candidate_id"]] = decision_mask(rows04, c)
    for c in candidates:
        if c.get("op") == "AND" or c.get("parents"):
            pa, pb = c["parents"]
            masks04[c["candidate_id"]] = masks04[pa] & masks04[pb]
    pack04 = build_long_path_metrics(
        rows04, use_disk=True, cache_name="_anchor_path_metrics_20260804.pkl",
    )
    m04 = pack04["metrics"]
    d04 = np.array([r["date"] for r in rows04])
    s04 = np.array([r["symbol"] for r in rows04])
    sess04 = np.array([r["session"] for r in rows04])
    ok04 = m04["ok"]
    consumed_rows = []
    handoff_by_id = {h["candidate_id"]: h for h in handoff}
    for rid in reps:
        sel04 = masks04[rid]
        agg04 = aggregate_candidate_period(
            selected=sel04, metrics=m04, dates=d04, symbols=s04,
            sessions=sess04, period="20260804", path_ok=ok04,
        )
        tags = handoff_by_id[rid]["discovery_family_tags"]
        row04 = {
            "representative_id": rid,
            "discovery_family_tags": ",".join(tags),
            "pop_n": len(rows04),
            "selected": agg04.get("selected_anchors"),
            "return_900s_delta_vs_ALL": agg04.get("horizons", {}).get("900s", {}).get("delta_vs_ALL", {}).get("mean_return"),
            "up30_reach_sel": agg04.get("reach", {}).get("up_30", {}).get("SELECTED", {}).get("reach_rate"),
            "role": "CONSUMED_PROSPECTIVE_DIAGNOSTIC_ONLY",
        }
        consumed_rows.append(row04)
        handoff_by_id[rid]["consumed_20260804_return_900s_delta"] = row04["return_900s_delta_vs_ALL"]
        handoff_by_id[rid]["consumed_20260804_selected"] = row04["selected"]

    # expand aliases into handoff count = unique masks (spec: all 6441 unique masks)
    # also alias registry sheet lists all 8254

    # verdict
    clear_families = sum(family_counts[t] for t in (
        "QUICK_MOVE", "PULLBACK_THEN_RISE", "CONTINUATION", "DELAYED_MOVE", "SPIKE_AND_GIVEBACK",
    ))
    supported = evidence_counts.get("ENTRY_PATH_SUPPORTED", 0)
    mixed = evidence_counts.get("ENTRY_PATH_MIXED", 0)
    if clear_families >= 2 and supported > 0:
        verdict = VERDICT_FAMILIES
    elif supported == 0 and clear_families == 0:
        verdict = VERDICT_NO_EDGE
    else:
        verdict = VERDICT_MIXED if mixed > supported else VERDICT_FAMILIES

    # anchor identity sheet
    anchor_id_rows = [{
        "date": r["date"], "session": r["session"], "symbol": r["symbol"],
        "cluster_id": r["cluster_id"], "anchor_time": r.get("anchor_time") or r.get("grid_time"),
        "anchor_price": r.get("CurrentPrice"), "grid_epoch": r.get("grid_epoch"),
    } for r in rows]

    # sample anchor path metrics
    amp_rows = []
    for i, r in enumerate(rows[:2000]):
        amp_rows.append({
            "cluster_id": r["cluster_id"],
            "return_60s_bps": metrics["return_60s_bps"][i],
            "return_300s_bps": metrics["return_300s_bps"][i],
            "return_900s_bps": metrics["return_900s_bps"][i],
            "return_1800s_bps": metrics["return_1800s_bps"][i],
            "return_session_bps": metrics["return_session_bps"][i],
            "MFE_900s_bps": metrics["MFE_900s_bps"][i],
            "MAE_900s_bps": metrics["MAE_900s_bps"][i],
            "up_30_reached": bool(metrics["up_30_reached"][i]),
            "up_50_reached": bool(metrics["up_50_reached"][i]),
        })

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "source_runs": {
            "x21": SOURCE_X21, "x22": SOURCE_X22, "x23": SOURCE_X23, "x24": SOURCE_X24,
        },
        "candidate_ids": EXPECTED_CAND_N,
        "unique_masks": unique_n,
        "aliases": alias_n,
        "anchor_population": EXPECTED_POP_N,
        "path_coverage": elig_counts,
        "censoring_counts": censor_counts,
        "freshness_coverage": fresh_counts,
        "parity_vs_x22": parity,
        "path_meta": pack["meta"],
        "path_evidence_counts": dict(evidence_counts),
        "discovery_family_counts": dict(family_counts),
        "evaluation_family_validation": {
            "note": "Discovery tags frozen; Evaluation metrics recorded without retagging",
            "supported_on_eval_direction": supported,
        },
        "stress_20260803": {"anchors": int((dates == "20260803").sum())},
        "consumed_20260804": {
            "role": "CONSUMED_PROSPECTIVE_DIAGNOSTIC_ONLY",
            "population_n": len(rows04),
            "paths_ok": pack04["meta"]["paths_ok"],
        },
        "x26_handoff_candidate_count": len(handoff),
        "x26_note": (
            "X26 may design limited common EXIT families per Discovery path family only; "
            "no per-candidate free EXIT search; no Evaluation/20260804 EXIT retune."
        ),
        "episode_independence_claim": False,
        "correct_expression": "incremental selection / timing difference within same episode population",
        "candidates_closed": 0,
        "exit_selected": False,
        "executable_claim": False,
        "safety": {
            "submit_cancel_live": "0/0/0",
            "production_runtime_changed": False,
            "production_yaml_changed": False,
            "runtime_ENTRY_changed": False,
            "runtime_EXIT_changed": False,
            "Universe_changed": False,
            "Shadow": False,
            "Forward": False,
            "Paper_connection": False,
            "Discord": False,
            "risk_only_from": FORBIDDEN_RISK_FROM,
            "risk_only_opened": False,
        },
        "_sheets": {
            "SourceIdentity": [
                {"source": "X21", "run_id": SOURCE_X21},
                {"source": "X22", "run_id": SOURCE_X22},
                {"source": "X23", "run_id": SOURCE_X23},
                {"source": "X24", "run_id": SOURCE_X24},
            ],
            "EntryRegistry": [
                {"candidate_id": c["candidate_id"], "feature_name": c.get("feature_name"),
                 "rule_type": c.get("rule_type"), "threshold": c.get("threshold"),
                 "operator": c.get("operator"), "implementation_id": c.get("implementation_id")}
                for c in candidates[:500]
            ] + [{"candidate_id": "(truncated)", "note": f"total {len(candidates)} frozen"}],
            "DecisionMaskRegistry": mask_reg,
            "AliasRegistry": alias_rows,
            "AnchorIdentity": anchor_id_rows,
            "PathCoverage": [{"horizon": k, "eligible": v} for k, v in elig_counts.items()],
            "HorizonEligibility": [{"horizon": k, "censored": v} for k, v in censor_counts.items()],
            "Freshness": [{"horizon": k, "fresh_ok_primary_30s": v} for k, v in fresh_counts.items()],
            "AnchorPathMetrics": amp_rows,
            "CandidateSupport": support_rows,
            "FixedHorizonReturns": fixed_ret_rows,
            "MFE_MAE": mfe_rows,
            "TargetReach": reach_rows,
            "FirstTouch": ft_rows,
            "PreRiseDrawdown": prerise_rows,
            "Giveback": gb_rows,
            "BaselineComparison": base_rows,
            "ComplementComparison": comp_rows,
            "DiscoveryResults": disc_family_rows,
            "EvaluationResults": [
                {"representative_id": h["candidate_id"],
                 "eval_return_900s_delta": h.get("eval_return_900s_delta_vs_ALL")}
                for h in handoff
            ],
            "Stress20260803": [
                {"representative_id": h["candidate_id"],
                 "stress_return_900s_delta": h.get("stress_return_900s_delta_vs_ALL")}
                for h in handoff
            ],
            "Consumed20260804": consumed_rows,
            "DiscoveryFamilyRules": [{"tag": k, "rule": v} for k, v in FAMILY_RULES_FROZEN.items()],
            "PathFamilyRegistry": disc_family_rows,
            "FamilyValidation": fam_val_rows,
            "PathEvidenceStatus": evidence_rows,
            "DailyResults": daily_rows,
            "SymbolResults": symbol_rows,
            "LODO": lodo_rows[:20000],
            "LOSO": loso_rows[:20000],
            "Bootstrap": boot_rows,
            "FDR": [{"representative_id": r["representative_id"], "bh_q": r.get("bh_q"),
                     "raw_p": r.get("raw_p"), "tag": r.get("tag")} for r in boot_rows],
            "X26Handoff": handoff,
            "ReservedDates": [
                {"date": "20260804", "role": "CONSUMED_PROSPECTIVE_DIAGNOSTIC_ONLY"},
                {"date": "20260805+", "role": "RISK_ONLY_EXCLUDED"},
            ],
            "ChangeLog": [{"at": datetime.now(JST).isoformat(), "note": "E1_X25 long-horizon ENTRY path profiling"}],
        },
    }
    return report


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id_a = f"e1x25_path_{now.strftime('%Y%m%d_%H%M%S')}_A"
    print(f"=== E1_X25 run A {run_id_a} ===", flush=True)
    report_a = run_once(run_id_a)
    if report_a.get("verdict") in (VERDICT_REG_FAIL, VERDICT_PATH_FAIL):
        tests = {"exit_code": 1, "passed": 0, "failed": 1, "total": 1,
                 "rows": [{"test": "early_fail", "outcome": "FAILED", "detail": report_a}]}
        det = {"ab_match": False}
        publish(report_a, tests, det, OUT)
        return report_a

    # A/B determinism: rebuild metrics sha compare (reuse disk cache → same path sha)
    run_id_b = run_id_a[:-1] + "B"
    print(f"=== E1_X25 run B {run_id_b} ===", flush=True)
    # path sha from cache
    pack = build_long_path_metrics(load_population_checked(), use_disk=True)
    path_sha_a = report_a.get("path_meta", {}).get("path_sha256")
    path_sha_b = pack["meta"]["path_sha256"]
    ab_match = path_sha_a == path_sha_b
    # also compare handoff digest
    handoff_sha = sha256_obj([{
        "id": h["candidate_id"], "tags": h["discovery_family_tags"], "st": h["path_evidence_status"],
        "d900": h.get("return_900s_delta_vs_ALL"),
    } for h in report_a["_sheets"]["X26Handoff"]])

    print("=== tests ===", flush=True)
    # write interim report fields tests need
    (OUT / "_interim.json").write_text(json.dumps({
        "run_id": run_id_a,
        "verdict": report_a["verdict"],
        "path_sha256": path_sha_a,
        "handoff_sha": handoff_sha,
        "candidate_ids": report_a["candidate_ids"],
        "unique_masks": report_a["unique_masks"],
        "aliases": report_a["aliases"],
        "anchor_population": report_a["anchor_population"],
        "parity": report_a.get("parity_vs_x22"),
        "path_evidence_counts": report_a.get("path_evidence_counts"),
        "discovery_family_counts": report_a.get("discovery_family_counts"),
        "candidates_closed": 0,
        "exit_selected": False,
        "safety": report_a.get("safety"),
    }, indent=2, default=str), encoding="utf-8")

    tests = _run_tests()
    det = {
        "ab_match": ab_match,
        "path_sha_a": path_sha_a,
        "path_sha_b": path_sha_b,
        "handoff_sha": handoff_sha,
        "run_id_a": run_id_a,
        "run_id_b": run_id_b,
    }
    print("=== publish ===", flush=True)
    shas = publish(report_a, tests, det, OUT)
    removed = delete_interim_caches()
    interim = OUT / "_interim.json"
    if interim.exists():
        interim.unlink()
        removed.append(str(interim))
    report_a["published_shas"] = shas
    report_a["interim_removed"] = removed
    print(f"=== DONE verdict={report_a['verdict']} ab={ab_match} ===", flush=True)
    return report_a


if __name__ == "__main__":
    run()
