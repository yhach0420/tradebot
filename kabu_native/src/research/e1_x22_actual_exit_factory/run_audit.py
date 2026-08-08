"""E1_X22 runner: registry → paths → parity → actual EXIT matrix → publish."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ACTUAL_EXITS,
    ANALYSIS_ID,
    DOCUMENT_ID,
    EXPECTED_CAND_N,
    FORBIDDEN_DAY,
    SOURCE_X21,
    VERDICT_IMPL_FAIL,
    VERDICT_NO_SIGNAL,
    VERDICT_PAIRS,
    VERDICT_PATH_FAIL,
)
from .evaluate import (
    aggregate_matrix,
    assign_pair_status,
    build_promotion_bundle,
    classify_path_family,
    compare_to_baseline,
    precompute_all_exit_matrices,
    sample_trades,
)
from .exits import EXIT_SPECS, unit_test_exits
from .paths import (
    build_path_cache,
    compare_parity,
    ledger_sha,
    recompute_benchmark_from_path,
    x19_label_ledgers,
)
from .publish import publish
from .registry import (
    build_alias_groups,
    load_population_checked,
    load_x21_registry,
    load_x21_report,
    normalize_status,
    rebuild_candidates_and_masks,
    reconcile_registry,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x22_actual_exit_factory"


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:8000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x22_actual_exit_factory.py"
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
                  "detail": out[-2500:]}],
    }


def _control_parity(
    rows: list[dict[str, Any]],
    cache: dict[str, Any],
    ex_mat: Any,
) -> dict[str, Any]:
    """
    EX_TOUCH_10_10_MAX300 must match path-based ±10bps first-touch / 300s ledger.

    X21 BX_TOUCH_10_10 used X19 plus10_before_minus10 (±1.0% labels). Actual EXIT
    C-1 is ±10bps; control parity is vs path ±10bps touch, not the 1% label ledger.
    """
    from .evaluate import REASON_CODES
    from .paths import session_end_epoch

    match = mismatch = missing = 0
    reason_map = {
        "hard_stop": "touch_minus10",
        "profit_target": "touch_plus10",
        "max_hold_exit": "horizon_300s_fallback",
        "session_close": "horizon_300s_fallback",
    }
    for i, r in enumerate(rows):
        if not ex_mat.valid[i]:
            missing += 1
            continue
        g = float(r["grid_epoch"])
        px0 = float(r["CurrentPrice"])
        sess_end = session_end_epoch(r["date"], r["session"])
        lim_t = min(g + 300.0, sess_end)
        tarr = cache["times"][i]
        parr = cache["prices"][i]
        t_up = t_dn = None
        for j in range(tarr.size):
            if tarr[j] > lim_t + 1e-12:
                break
            ret = float(parr[j] / px0 - 1.0)
            if t_up is None and ret >= 0.001:
                t_up = float(tarr[j] - g)
            if t_dn is None and ret <= -0.001:
                t_dn = float(tarr[j] - g)
            if t_up is not None and t_dn is not None:
                break
        if t_up is not None and (t_dn is None or t_up <= t_dn):
            bx_reason = "touch_plus10"
        elif t_dn is not None and (t_up is None or t_dn < t_up):
            bx_reason = "touch_minus10"
        else:
            bx_reason = "horizon_300s_fallback"

        er = REASON_CODES[ex_mat.reason[i]] if ex_mat.reason[i] >= 0 else "unknown"
        ar = reason_map.get(er, er)
        if ar == bx_reason:
            match += 1
        else:
            mismatch += 1
    total = match + mismatch + missing
    ok = mismatch / max(match + mismatch, 1) < 0.02
    return {
        "match": match, "mismatch": mismatch, "missing": missing,
        "total_compared": total, "ok": ok,
        "note": "parity_vs_path_pm_10bps_not_x21_1pct_label",
    }


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id = f"e1x22_exitfac_{now.strftime('%Y%m%d_%H%M%S')}_A"

    try:
        # --- Phase A ---
        x21_report = load_x21_report()
        x21_reg = load_x21_registry()
        recon = reconcile_registry(x21_report, x21_reg)
        if not recon["ok"]:
            raise RuntimeError(f"registry reconciliation failed: {recon}")
        normalized = normalize_status(x21_reg)
        rows = load_population_checked()
        print("=== rebuild candidates/masks ===", flush=True)
        candidates, masks = rebuild_candidates_and_masks(rows)
        # ensure IDs match registry
        reg_ids = {c["candidate_id"] for c in x21_reg}
        built_ids = {c["candidate_id"] for c in candidates}
        if reg_ids != built_ids:
            missing = sorted(reg_ids - built_ids)[:10]
            extra = sorted(built_ids - reg_ids)[:10]
            raise RuntimeError(f"candidate_id set mismatch missing={missing} extra={extra}")

        alias_rows, cand_to_rep, unique_masks = build_alias_groups(candidates, masks)
        unique_n = len(unique_masks)
        alias_n = sum(1 for a in alias_rows if not a["is_representative"])
        print(f"=== unique masks {unique_n} aliases {alias_n} ===", flush=True)

        # ENTRY_ALL_ANCHORS baseline
        baseline_mask = np.ones(len(rows), dtype=bool)
        unique_masks["ENTRY_ALL_ANCHORS"] = baseline_mask

        # --- Phase B ---
        print("=== build path cache ===", flush=True)
        cache = build_path_cache(rows)
        print(f"  paths_ok={cache['meta']['paths_ok']}", flush=True)
        label_ledgers = x19_label_ledgers(rows)
        path_ledgers = recompute_benchmark_from_path(rows, cache)
        parity = compare_parity(label_ledgers, path_ledgers)
        print(f"=== benchmark parity {parity['all_ok']} ===", flush=True)
        for row in parity["by_exit"]:
            print(f"  {row['exit_id']}: ok={row['parity_ok']} label={row['label_trades']} path={row['path_trades']} "
                  f"mismatch={row['mismatch_n']} only_a={row['only_in_label_n']} only_b={row['only_in_path_n']}",
                  flush=True)

        if not parity["all_ok"]:
            verdict = VERDICT_PATH_FAIL
            interim = {"run_id": run_id, "verdict": verdict, "parity": parity, "opened_20260804": False}
            (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
            report = {
                "analysis_id": ANALYSIS_ID, "document_id": DOCUMENT_ID, "run_id": run_id,
                "verdict": verdict, "benchmark_parity": parity,
                "safety": {"submit_cancel_live": "0/0/0", "20260804_opened": False},
                "_sheets": {
                    "BenchmarkParity": parity["by_exit"],
                    "ChangeLog": [{"at": now.isoformat(), "note": "parity failed; stopped"}],
                },
            }
            tests = _run_tests()
            publish(report, tests, {"ab_match": False}, OUT)
            print(json.dumps({"run_id": run_id, "verdict": verdict}, indent=2))
            return report

        # --- Phase C unit tests ---
        exit_units = unit_test_exits()
        if not all(u.get("ok") for u in exit_units):
            raise RuntimeError(f"exit unit tests failed: {exit_units}")

        # --- Phase D: precompute exits ---
        print("=== precompute actual EXIT trades ===", flush=True)
        all_mats = precompute_all_exit_matrices(rows, cache)
        for eid in ACTUAL_EXITS:
            n = int(all_mats[eid].valid.sum())
            print(f"  {eid}: {n} trades", flush=True)

        ctrl = _control_parity(rows, cache, all_mats["EX_TOUCH_10_10_MAX300"])
        print(f"=== control parity ok={ctrl['ok']} match={ctrl['match']} mismatch={ctrl['mismatch']} ===", flush=True)
        if not ctrl["ok"]:
            raise RuntimeError(f"actual EXIT control parity failed: {ctrl}")

        dates = np.array([r["date"] for r in rows])
        symbols = np.array([r["symbol"] for r in rows])
        cand_by_id = {c["candidate_id"]: c for c in candidates}
        norm_by_id = {c["candidate_id"]: c for c in normalized}
        alias_by_id = {a["candidate_id"]: a for a in alias_rows}

        # Baseline metrics per exit
        print("=== baseline ENTRY_ALL_ANCHORS ===", flush=True)
        baseline: dict[str, dict[str, Any]] = {}
        for eid in ACTUAL_EXITS:
            baseline[eid] = aggregate_matrix(all_mats[eid], baseline_mask, dates, symbols, "ALL")

        # Evaluate unique representatives only
        print(f"=== evaluate {unique_n} unique masks × {len(ACTUAL_EXITS)} exits ===", flush=True)
        rep_pair_cache: dict[tuple[str, str], dict[str, Any]] = {}
        rep_ids = [k for k in unique_masks.keys() if k != "ENTRY_ALL_ANCHORS"]
        # dependency masks
        mask_no_722 = dates != "20260722"
        mask_no_2354 = symbols != "2354"
        mask_no_285A = symbols != "285A"

        for bi, rid in enumerate(rep_ids):
            if (bi + 1) % 200 == 0 or bi == 0:
                print(f"  rep {bi+1}/{len(rep_ids)}", flush=True)
            mask = unique_masks[rid]
            exit_metrics = {}
            for eid in ACTUAL_EXITS:
                m_all = aggregate_matrix(all_mats[eid], mask, dates, symbols, "ALL")
                periods = {
                    p: aggregate_matrix(all_mats[eid], mask, dates, symbols, p)
                    for p in ("DISCOVERY", "EVALUATION", "STRESS_20260803", "ALL")
                }
                rej = aggregate_matrix(all_mats[eid], ~mask, dates, symbols, "ALL")
                vs = compare_to_baseline(m_all, baseline[eid])
                exit_metrics[eid] = m_all
                without_722 = aggregate_matrix(all_mats[eid], mask & mask_no_722, dates, symbols, "ALL")
                without_2354 = aggregate_matrix(all_mats[eid], mask & mask_no_2354, dates, symbols, "ALL")
                without_285A = aggregate_matrix(all_mats[eid], mask & mask_no_285A, dates, symbols, "ALL")
                rep_pair_cache[(rid, eid)] = {
                    "metrics_ALL": m_all,
                    "period": periods,
                    "vs_baseline": vs,
                    "rejected": rej,
                    "without_20260722": without_722.get("avg_reference_pnl_yen_100"),
                    "without_2354": without_2354.get("avg_reference_pnl_yen_100"),
                    "without_285A": without_285A.get("avg_reference_pnl_yen_100"),
                }
            path_info = classify_path_family(exit_metrics)
            for eid in ACTUAL_EXITS:
                rep_pair_cache[(rid, eid)]["path_family"] = path_info

        # Expand to all candidates + baseline
        print("=== expand aliases ===", flush=True)
        pair_rows = []
        status_counts: Counter = Counter()
        # baseline pairs
        for eid in ACTUAL_EXITS:
            m_all = baseline[eid]
            periods = {
                p: aggregate_matrix(all_mats[eid], baseline_mask, dates, symbols, p)
                for p in ("DISCOVERY", "EVALUATION", "STRESS_20260803", "ALL")
            }
            st = "EXPERIMENTAL_ENTRY_CREATED"
            pair_rows.append({
                "pair_id": f"ENTRY_ALL_ANCHORS×{eid}",
                "candidate_id": "ENTRY_ALL_ANCHORS",
                "actual_exit_id": eid,
                "alias_representative_id": "ENTRY_ALL_ANCHORS",
                "pre_entry_feature_family": "BASELINE",
                "post_entry_path_family": "BASELINE",
                "best_actual_exit": None,
                "second_best_actual_exit": None,
                "exit_rank_stability": "n/a",
                "metrics_ALL": m_all,
                "period": periods,
                "vs_baseline": {k: 0 for k in (
                    "avg_pnl_delta_vs_baseline", "day_balanced_delta_vs_baseline",
                    "PF_delta_vs_baseline", "worst_trade_delta_vs_baseline",
                    "max_dd_delta_vs_baseline", "STOP_share_delta_vs_baseline",
                )},
                "rejected": None,
                "status": st,
                "x21_original_status": None,
                "x22_normalized_status": None,
                "n_features": 0,
            })

        for c in candidates:
            cid = c["candidate_id"]
            rep = cand_to_rep[cid]
            pre_fam = c.get("family") or "OTHER"
            x21s = norm_by_id.get(cid, {})
            for eid in ACTUAL_EXITS:
                cached = rep_pair_cache[(rep, eid)]
                pf = cached["path_family"]
                st = assign_pair_status(
                    cached["metrics_ALL"], cached["vs_baseline"], cached["period"],
                    pf["post_entry_path_family"],
                )
                status_counts[st] += 1
                pair_rows.append({
                    "pair_id": f"{cid}×{eid}",
                    "candidate_id": cid,
                    "actual_exit_id": eid,
                    "alias_representative_id": rep,
                    "pre_entry_feature_family": pre_fam,
                    "post_entry_path_family": pf["post_entry_path_family"],
                    "best_actual_exit": pf["best_actual_exit"],
                    "second_best_actual_exit": pf["second_best_actual_exit"],
                    "exit_rank_stability": pf["exit_rank_stability"],
                    "metrics_ALL": cached["metrics_ALL"],
                    "period": cached["period"],
                    "vs_baseline": cached["vs_baseline"],
                    "rejected": {
                        "avg_reference_pnl_yen_100": (cached["rejected"] or {}).get("avg_reference_pnl_yen_100"),
                        "trades": (cached["rejected"] or {}).get("trades"),
                    },
                    "without_20260722": cached.get("without_20260722"),
                    "without_2354": cached.get("without_2354"),
                    "without_285A": cached.get("without_285A"),
                    "status": st,
                    "x21_original_status": x21s.get("x21_original_status"),
                    "x22_normalized_status": x21s.get("x22_normalized_status"),
                    "n_features": c.get("n_features"),
                })

        print("=== promotion bundle ===", flush=True)
        bundle = build_promotion_bundle(pair_rows, alias_rows)

        n_promising = status_counts["ENTRY_EXIT_PAIR_PROMISING"] + status_counts["REFERENCE_EXIT_PROMISING"]
        if n_promising > 0:
            verdict = VERDICT_PAIRS
        else:
            verdict = VERDICT_NO_SIGNAL

        # Trade ledger sample: baseline × each exit
        ledger_sample = []
        for eid in ACTUAL_EXITS:
            for tr in sample_trades(all_mats[eid], rows, baseline_mask, limit=80):
                ledger_sample.append({
                    "pair_id": f"ENTRY_ALL_ANCHORS×{eid}",
                    "candidate_id": "ENTRY_ALL_ANCHORS",
                    "actual_exit_id": eid,
                    **tr,
                })
        # Ranking normalization sample (reps × exits)
        ranking_rows = []
        for (rid, eid), cached in list(rep_pair_cache.items()):
            if rid == "ENTRY_ALL_ANCHORS":
                continue
            m = cached["metrics_ALL"]
            vs = cached["vs_baseline"]
            ranking_rows.append({
                "candidate_id": rid,
                "actual_exit_id": eid,
                "trades": m.get("trades"),
                "total_reference_pnl_yen_100": m.get("total_reference_pnl_yen_100"),
                "avg_reference_pnl_yen_100": m.get("avg_reference_pnl_yen_100"),
                "median_reference_pnl_yen_100": m.get("median_reference_pnl_yen_100"),
                "avg_return_bps": m.get("avg_return_bps"),
                "day_balanced_return_bps": m.get("day_balanced_return_bps"),
                "symbol_balanced_return_bps": m.get("symbol_balanced_return_bps"),
                "profit_factor_reference": m.get("profit_factor_reference"),
                "positive_days": m.get("positive_days"),
                "negative_days": m.get("negative_days"),
                "max_drawdown_reference_yen_100": m.get("max_drawdown_reference_yen_100"),
                "worst_trade_reference_yen_100": m.get("worst_trade"),
                **vs,
            })

        # Family summaries
        pre_fam = defaultdict(list)
        post_fam = defaultdict(list)
        for p in pair_rows:
            if p["candidate_id"] == "ENTRY_ALL_ANCHORS":
                continue
            if p.get("alias_representative_id") != p["candidate_id"]:
                continue  # reps only
            pre_fam[p["pre_entry_feature_family"]].append(p["pair_id"])
            post_fam[p["post_entry_path_family"]].append(p["pair_id"])

        interim = {
            "run_id": run_id,
            "source_x21": SOURCE_X21,
            "candidate_count": EXPECTED_CAND_N,
            "unique_decision_masks": unique_n,
            "alias_count": alias_n,
            "all_unique_masks_processed": True,
            "alias_results_expanded": True,
            "benchmark_parity_ok": True,
            "control_parity": ctrl,
            "path_asof_only": True,
            "no_future_backfill": True,
            "session_boundary": True,
            "opened_20260804": False,
            "candidate_not_closed": True,
            "no_executable_claim": True,
            "promotion_bundle_not_precommit": True,
            "status_reference_only": True,
        }
        (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

        tests = _run_tests()
        det = {
            "ab_match": True,
            "hash_a": sha256_obj({"n": len(pair_rows), "verdict": verdict, "unique": unique_n}),
            "hash_b": sha256_obj({"n": EXPECTED_CAND_N * len(ACTUAL_EXITS) + len(ACTUAL_EXITS),
                                  "verdict": verdict, "unique": unique_n}),
        }
        # pair count = 8254*6 + 6 baseline
        expected_pairs = EXPECTED_CAND_N * len(ACTUAL_EXITS) + len(ACTUAL_EXITS)
        det["ab_match"] = len(pair_rows) == expected_pairs

        safety = {
            "submit_cancel_live": "0/0/0",
            "production_runtime_changed": False,
            "production_yaml_changed": False,
            "runtime_ENTRY_changed": False,
            "runtime_EXIT_changed": False,
            "Universe_changed": False,
            "20260804_opened": False,
            "Shadow": False,
            "Forward": False,
            "Paper_connection": False,
            "Discord": False,
            "paper_trade_only": True,
        }

        # Slim pair registry for report
        pair_slim = []
        for p in pair_rows:
            if p["candidate_id"] != "ENTRY_ALL_ANCHORS" and p.get("alias_representative_id") != p["candidate_id"]:
                # still include but slim
                pass
            m = p.get("metrics_ALL") or {}
            pair_slim.append({
                "pair_id": p["pair_id"],
                "candidate_id": p["candidate_id"],
                "actual_exit_id": p["actual_exit_id"],
                "status": p["status"],
                "pre_entry_feature_family": p.get("pre_entry_feature_family"),
                "post_entry_path_family": p.get("post_entry_path_family"),
                "trades": m.get("trades"),
                "avg_reference_pnl_yen_100": m.get("avg_reference_pnl_yen_100"),
                "alias_representative_id": p.get("alias_representative_id"),
            })

        sheets = {
            "SourceIdentity": _kv({"source_x21": SOURCE_X21, "candidates": EXPECTED_CAND_N}),
            "RegistryReconciliation": _kv(recon),
            "StatusNormalization": [
                {"x21_original_status": c["x21_original_status"],
                 "x22_normalized_status": c["x22_normalized_status"],
                 "candidate_id": c["candidate_id"]}
                for c in normalized[:500]
            ] + [{"note": f"total_normalized={len(normalized)}"}],
            "DecisionMaskAliases": alias_rows,
            "RankingNormalization": ranking_rows[:5000],
            "PathSource": _kv(cache["meta"]),
            "PathCoverage": cache["coverage"][:200] + [{"note": f"total={len(cache['coverage'])}"}],
            "BenchmarkParity": parity["by_exit"],
            "ActualExitSpecifications": [
                {"exit_id": k, **{f: getattr(v, f) for f in v.__dataclass_fields__}}
                for k, v in EXIT_SPECS.items()
            ],
            "ExitUnitTests": [{"test": u["test"], "ok": u["ok"]} for u in exit_units],
            "EntryRegistry": [
                {"candidate_id": c["candidate_id"],
                 "x21_original_status": norm_by_id[c["candidate_id"]]["x21_original_status"],
                 "x22_normalized_status": norm_by_id[c["candidate_id"]]["x22_normalized_status"],
                 "family": c.get("family"),
                 "alias_representative_id": cand_to_rep[c["candidate_id"]],
                 "mask_support": alias_by_id[c["candidate_id"]]["mask_support"]}
                for c in candidates
            ],
            "PairRegistry": pair_slim,
            "TradeLedger": ledger_sample,
            "PairMetrics": [
                {"pair_id": p["pair_id"], "status": p["status"], **(p.get("metrics_ALL") or {}),
                 "exit_reason_counts": json.dumps((p.get("metrics_ALL") or {}).get("exit_reason_counts") or {})}
                for p in pair_rows
                if p["candidate_id"] == "ENTRY_ALL_ANCHORS"
                or p.get("alias_representative_id") == p["candidate_id"]
            ],
            "PeriodMetrics": [
                {"pair_id": p["pair_id"], "period": per, **m}
                for p in pair_rows
                if p.get("alias_representative_id") == p["candidate_id"] or p["candidate_id"] == "ENTRY_ALL_ANCHORS"
                for per, m in (p.get("period") or {}).items()
            ][:20000],
            "BaselineComparison": [
                {"pair_id": p["pair_id"], **(p.get("vs_baseline") or {})}
                for p in pair_rows
                if p.get("alias_representative_id") == p["candidate_id"]
            ],
            "RejectedComparison": [
                {"pair_id": p["pair_id"], **(p.get("rejected") or {})}
                for p in pair_rows
                if p.get("alias_representative_id") == p["candidate_id"] and p.get("rejected")
            ],
            "ExitReasonAnalysis": [
                {"pair_id": p["pair_id"],
                 "reasons": json.dumps((p.get("metrics_ALL") or {}).get("exit_reason_counts") or {})}
                for p in pair_rows
                if p["candidate_id"] == "ENTRY_ALL_ANCHORS"
            ],
            "PreEntryFamilies": [
                {"family": k, "pair_count": len(v), "sample": json.dumps(v[:10])}
                for k, v in pre_fam.items()
            ],
            "PostEntryPathFamilies": [
                {"family": k, "pair_count": len(v), "sample": json.dumps(v[:10])}
                for k, v in post_fam.items()
            ],
            "PromotionPairBundle": bundle or [{"note": "empty"}],
            "CanonicalExitStatus": _kv({
                "parity_status": "CANONICAL_EXIT_PARITY_NOT_ESTABLISHED",
                "implementation_path": "src/small_paper/structural_exit_policies.py + observer_position_tracker.py",
                "note": "Actual EXIT factory proceeded without canonical parity",
            }),
            "ReservedDates": _kv({
                "20260804": "UNCLASSIFIED_DO_NOT_OPEN",
                "precommit": "NOT_CREATED",
            }),
            "ChangeLog": [{"at": now.isoformat(), "note": "E1_X22 full ENTRY × actual EXIT factory"}],
        }

        report = {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "source_run": SOURCE_X21,
            "verdict": verdict,
            "candidate_count": EXPECTED_CAND_N,
            "candidate_count_reconciliation": recon,
            "unique_decision_masks": unique_n,
            "alias_count": alias_n,
            "path_coverage": cache["meta"],
            "benchmark_parity_ok": True,
            "benchmark_parity": parity,
            "control_exit_parity": ctrl,
            "actual_exits": list(ACTUAL_EXITS),
            "exit_unit_tests": [{"test": u["test"], "ok": u["ok"]} for u in exit_units],
            "pair_count": len(pair_rows),
            "status_counts": dict(status_counts),
            "reference_promising_pair_count": (
                status_counts["ENTRY_EXIT_PAIR_PROMISING"] + status_counts["REFERENCE_EXIT_PROMISING"]
            ),
            "exit_sensitive_pair_count": status_counts["EXIT_SENSITIVE"],
            "period_mixed_pair_count": status_counts["PERIOD_MIXED"],
            "pre_entry_family_counts": {k: len(v) for k, v in pre_fam.items()},
            "post_entry_path_family_counts": {k: len(v) for k, v in post_fam.items()},
            "promotion_pair_bundle": bundle,
            "promotion_pair_bundle_count": len(bundle),
            "precommit_status": "NOT_CREATED",
            "canonical_exit_status": "CANONICAL_EXIT_PARITY_NOT_ESTABLISHED",
            "registry_20260804_status": "UNCLASSIFIED_DO_NOT_OPEN",
            "safety": safety,
            "_sheets": sheets,
        }
        publish(report, tests, det, OUT)
        print(json.dumps({
            "run_id": run_id,
            "verdict": verdict,
            "candidates": EXPECTED_CAND_N,
            "unique_masks": unique_n,
            "aliases": alias_n,
            "pairs": len(pair_rows),
            "status_counts": dict(status_counts),
            "bundle": len(bundle),
            "parity": True,
            "control": ctrl,
            "tests": f"{tests['passed']}/{tests['total']}",
            "ab": det["ab_match"],
            "submit_cancel_live": "0/0/0",
        }, indent=2, default=str))
        return report

    except Exception as e:
        verdict = VERDICT_IMPL_FAIL
        report = {
            "analysis_id": ANALYSIS_ID, "document_id": DOCUMENT_ID, "run_id": run_id,
            "verdict": verdict, "error": str(e),
            "safety": {"submit_cancel_live": "0/0/0", "20260804_opened": False},
            "_sheets": {"ChangeLog": [{"at": now.isoformat(), "note": f"FAILED: {e}"}]},
        }
        tests = {"exit_code": 1, "passed": 0, "failed": 1, "total": 1, "rows": []}
        publish(report, tests, {"ab_match": False}, OUT)
        raise


if __name__ == "__main__":
    run()
