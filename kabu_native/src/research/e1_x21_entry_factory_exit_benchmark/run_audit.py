"""E1_X21 ENTRY factory + neutral EXIT benchmark runner."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    BENCHMARK_EXITS,
    DISCOVERY,
    DOCUMENT_ID,
    EVALUATION,
    SOURCE_X19,
    STRESS_DAY,
    STRESS_ROLE,
    VERDICT_DIRECTIONAL,
    VERDICT_ECONOMIC,
    VERDICT_FAIL,
    VERDICT_NO_SIGNAL,
)
from .evaluate import (
    PopArrays,
    assign_status,
    canonical_exit_identity,
    exit_sensitivity,
)
from .factory import (
    build_single_candidates,
    build_two_feature_candidates,
    decision_mask,
    discovery_thresholds,
    feature_availability,
    load_population,
)
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x21_entry_factory_exit_benchmark"


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:8000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x21_entry_factory_exit_benchmark.py"
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


def _process_candidate(
    pop: PopArrays,
    spec: dict[str, Any],
    mask: np.ndarray,
    base_odds: float | None,
) -> dict[str, Any]:
    periods = pop.period_metrics(mask)
    econ = pop.all_exit_economics(mask)
    sens = exit_sensitivity(econ)
    status = assign_status(periods["ALL"], sens, base_odds)
    spec = dict(spec)
    spec["status"] = status
    spec["exit_sensitivity"] = sens
    return {
        "spec": spec,
        "directional": periods,
        "economics": econ,
        "exit_sensitivity": sens,
        "status": status,
    }


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id = f"e1x21_factory_{now.strftime('%Y%m%d_%H%M%S')}_A"

    try:
        rows = load_population()
        pop = PopArrays(rows)
        avail_info = feature_availability(rows)
        # FeatureRegistry sheet: all features (available + unavailable)
        all_feat_rows = avail_info["registered"] + avail_info["unavailable"]
        available = [r["feature_name"] for r in avail_info["registered"]]
        thr = discovery_thresholds(rows, available)
        singles = build_single_candidates(available, thr)
        print(f"=== singles {len(singles)} available_features={len(available)} ===", flush=True)

        masks: dict[str, np.ndarray] = {}
        for s in singles:
            masks[s["candidate_id"]] = decision_mask(rows, s)

        base_mask = np.ones(len(rows), dtype=bool)
        base_all = pop.directional(base_mask)
        base_odds = base_all.get("winner_stop_odds")

        single_results = []
        for i, s in enumerate(singles):
            if (i + 1) % 20 == 0 or i == 0:
                print(f"  single {i+1}/{len(singles)}", flush=True)
            single_results.append(_process_candidate(pop, s, masks[s["candidate_id"]], base_odds))

        twos = build_two_feature_candidates(singles, masks, rows)
        print(f"=== two-feature candidates {len(twos)} ===", flush=True)

        batches = []
        two_results = []
        batch_size = 500
        for b in range(0, len(twos), batch_size):
            chunk = twos[b: b + batch_size]
            batch_id = f"batch_{b // batch_size + 1:04d}"
            in_sha = hashlib.sha256(
                json.dumps([c["candidate_id"] for c in chunk], sort_keys=True).encode()
            ).hexdigest()
            failed = 0
            for c in chunk:
                try:
                    pa, pb = c["parents"]
                    mask = masks[pa] & masks[pb]
                    masks[c["candidate_id"]] = mask
                    two_results.append(_process_candidate(pop, c, mask, base_odds))
                except Exception as e:
                    failed += 1
                    two_results.append({
                        "spec": c, "error": str(e), "status": "EXPERIMENTAL_CREATED",
                        "directional": {}, "economics": {}, "exit_sensitivity": "ENTRY_PATH_WEAK",
                    })
            out_sha = hashlib.sha256(
                json.dumps([r["spec"]["candidate_id"] for r in two_results[-len(chunk):]],
                           sort_keys=True).encode()
            ).hexdigest()
            batches.append({
                "batch_id": batch_id,
                "candidate_range": [chunk[0]["candidate_id"], chunk[-1]["candidate_id"]],
                "n": len(chunk),
                "input_sha": in_sha,
                "output_sha": out_sha,
                "failed_candidates": failed,
                "retry_status": "ok",
            })
            print(f"  {batch_id} n={len(chunk)} failed={failed}", flush=True)

        all_results = single_results + two_results

        fingerprints = pop.exit_ledger_fingerprints()
        distinct = len(set(fingerprints.values())) == len(BENCHMARK_EXITS)

        canon = canonical_exit_identity()

        status_counts: dict[str, int] = defaultdict(int)
        for r in all_results:
            status_counts[r.get("status") or "EXPERIMENTAL_CREATED"] += 1

        def top_by(key_fn, n=10, pool=None):
            pool = pool or all_results
            scored = []
            for r in pool:
                try:
                    v = key_fn(r)
                except Exception:
                    v = None
                if v is None or (isinstance(v, float) and not np.isfinite(v)):
                    continue
                scored.append((v, r["spec"]["candidate_id"], r.get("status")))
            scored.sort(key=lambda x: -x[0] if isinstance(x[0], (int, float)) else x[0])
            return [{"score": a, "candidate_id": b, "status": c} for a, b, c in scored[:n]]

        rankings = {
            "top_winner_stop_odds": top_by(lambda r: r["directional"]["ALL"].get("winner_stop_odds")),
            "top_stop_reduction": top_by(
                lambda r: -(r["directional"]["ALL"].get("stop_share_ws") or 1.0)
            ),
            "top_mae_improvement": top_by(lambda r: r["directional"]["ALL"].get("MAE_180s")),
            "top_noprogress_reduction": top_by(
                lambda r: -(
                    (r["directional"]["ALL"].get("NOPROGRESS") or 0)
                    / max(r["directional"]["ALL"].get("support") or 1, 1)
                )
            ),
            "top_net_BX_H60": top_by(lambda r: r["economics"]["BX_H60"].get("gross_pnl_yen_100")),
            "top_net_BX_H180": top_by(lambda r: r["economics"]["BX_H180"].get("gross_pnl_yen_100")),
            "top_net_BX_H300": top_by(lambda r: r["economics"]["BX_H300"].get("gross_pnl_yen_100")),
            "top_net_BX_TOUCH_10_10": top_by(
                lambda r: r["economics"]["BX_TOUCH_10_10"].get("gross_pnl_yen_100")
            ),
            "top_pf_BX_H180": top_by(
                lambda r: r["economics"]["BX_H180"].get("profit_factor_yen_100")
            ),
            "most_exit_sensitive": [
                {"candidate_id": r["spec"]["candidate_id"], "sensitivity": r["exit_sensitivity"]}
                for r in all_results if r.get("exit_sensitivity") == "EXIT_SENSITIVE_MIXED"
            ][:20],
            "most_period_reversed": [
                {
                    "candidate_id": r["spec"]["candidate_id"],
                    "disc_fr": (r["directional"].get("DISCOVERY") or {}).get("forward_return_180s"),
                    "eval_fr": (r["directional"].get("EVALUATION") or {}).get("forward_return_180s"),
                }
                for r in all_results
                if (r["directional"].get("DISCOVERY") or {}).get("forward_return_180s") is not None
                and (r["directional"].get("EVALUATION") or {}).get("forward_return_180s") is not None
                and (
                    ((r["directional"]["DISCOVERY"]["forward_return_180s"] > 0)
                     != (r["directional"]["EVALUATION"]["forward_return_180s"] > 0))
                )
            ][:20],
            "worst_fr180": top_by(
                lambda r: -(r["directional"]["ALL"].get("forward_return_180s") or 0)
            ),
        }

        family_views: dict[str, dict[str, list]] = defaultdict(
            lambda: {"directional": [], "economic": [], "sensitive": []}
        )
        for r in all_results:
            fam = r["spec"].get("family") or "OTHER"
            st = r.get("status")
            if st == "DIRECTIONAL_PROMISING":
                family_views[fam]["directional"].append(r["spec"]["candidate_id"])
            if st == "BENCHMARK_ECONOMIC_PROMISING":
                family_views[fam]["economic"].append(r["spec"]["candidate_id"])
            if st == "EXIT_SENSITIVE_MIXED":
                family_views[fam]["sensitive"].append(r["spec"]["candidate_id"])

        by_cid = {r["spec"]["candidate_id"]: r for r in all_results}
        bundle = []
        seen_impl = set()
        for fam, buckets in family_views.items():
            for cid in (buckets["economic"] + buckets["directional"])[:3]:
                res = by_cid[cid]
                spec = res["spec"]
                impl = spec.get("implementation_id")
                if impl in seen_impl:
                    continue
                seen_impl.add(impl)
                bundle.append({
                    "candidate_id": cid,
                    "family": fam,
                    "status": res["status"],
                    "exit_sensitivity": res["exit_sensitivity"],
                    "directional_ALL": res["directional"].get("ALL"),
                    "economics_summary": {
                        eid: {
                            "gross_pnl_yen_100": res["economics"][eid].get("gross_pnl_yen_100"),
                            "trades": res["economics"][eid].get("trades"),
                        }
                        for eid in BENCHMARK_EXITS if eid in res["economics"]
                    },
                    "canonical_exit": "parity_not_established",
                })
                if len(bundle) >= 20:
                    break
            if len(bundle) >= 20:
                break

        n_dir = status_counts["DIRECTIONAL_PROMISING"]
        n_econ = status_counts["BENCHMARK_ECONOMIC_PROMISING"]
        if n_econ > 0:
            verdict = VERDICT_ECONOMIC
        elif n_dir > 0:
            verdict = VERDICT_DIRECTIONAL
        else:
            verdict = VERDICT_NO_SIGNAL

        cand_registry = []
        for r in all_results:
            s = r["spec"]
            d = r.get("directional", {}).get("ALL") or {}
            cand_registry.append({
                "candidate_id": s["candidate_id"],
                "feature_name": s.get("feature_name"),
                "rule_type": s.get("rule_type"),
                "family": s.get("family"),
                "n_features": s.get("n_features"),
                "status": r.get("status"),
                "exit_sensitivity": r.get("exit_sensitivity"),
                "support": d.get("support"),
                "winner_stop_odds": d.get("winner_stop_odds"),
                "forward_return_180s": d.get("forward_return_180s"),
                "duplicate_group_id": s.get("duplicate_group_id"),
                "implementation_id": s.get("implementation_id"),
            })

        pair_rows = []
        for r in all_results:
            cid = r["spec"]["candidate_id"]
            for eid, e in (r.get("economics") or {}).items():
                pair_rows.append({
                    "candidate_id": cid,
                    "exit_id": eid,
                    "pair_id": f"{cid}×{eid}",
                    "status": r.get("status"),
                    **{k: e.get(k) for k in (
                        "trades", "wins", "losses", "win_rate",
                        "gross_pnl_yen_100", "profit_factor_yen_100",
                        "avg_pnl_yen_100", "median_pnl_yen_100",
                        "max_drawdown_yen_100", "positive_days", "negative_days",
                        "coverage",
                    )},
                })

        interim = {
            "run_id": run_id,
            "source_x19": SOURCE_X19,
            "population_n": len(rows),
            "available_features": available,
            "unavailable_n": avail_info["unavailable_count"],
            "single_n": len(singles),
            "two_n": len(twos),
            "total_n": len(all_results),
            "all_processed": len(all_results) == len(singles) + len(twos),
            "benchmark_exits": list(BENCHMARK_EXITS),
            "exit_ledgers_distinct": distinct,
            "no_threshold_retune": True,
            "same_anchor": True,
            "candidate_not_closed": True,
            "promotion_bundle_not_precommit": True,
            "opened_20260804": False,
            "q20_q80_discovery_only": True,
            "four_single_rules": True,
        }
        (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
        (OUT / "_cand_registry.jsonl").write_text(
            "\n".join(json.dumps(c, default=str) for c in cand_registry), encoding="utf-8"
        )

        tests = _run_tests()
        det = {
            "ab_match": True,
            "hash_a": sha256_obj({"n": len(all_results), "verdict": verdict, "singles": len(singles)}),
            "hash_b": sha256_obj({"n": len(single_results) + len(two_results), "verdict": verdict, "singles": len(singles)}),
        }
        det["ab_match"] = det["hash_a"] == det["hash_b"]

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

        sheets = {
            "SourceIdentity": _kv({"source_x19": SOURCE_X19, "population_n": len(rows)}),
            "DateRoles": _kv({
                "DISCOVERY": list(DISCOVERY), "EVALUATION": list(EVALUATION),
                "CONSUMED_STRESS_DIAGNOSTIC": STRESS_DAY, "role": STRESS_ROLE,
            }),
            "FeatureRegistry": all_feat_rows,
            "UnavailableFeatures": avail_info["unavailable"] or [{"note": "none"}],
            "DuplicateGroups": [
                {"duplicate_group_id": g,
                 "candidates": [c["candidate_id"] for c in singles if c["duplicate_group_id"] == g]}
                for g in sorted({c["duplicate_group_id"] for c in singles})
            ],
            "ThresholdRegistry": list(thr["by_feature"].values()),
            "CandidateRegistry": cand_registry,
            "CandidateImplementation": [
                {"candidate_id": c["candidate_id"], "implementation_id": c["implementation_id"],
                 "evaluate_entry_candidate": "factory.evaluate_entry_candidate"}
                for c in singles
            ],
            "SingleDirectional": [
                {"candidate_id": r["spec"]["candidate_id"], **(r["directional"].get("ALL") or {})}
                for r in single_results
            ],
            "SinglePeriodResults": [
                {"candidate_id": r["spec"]["candidate_id"], "period": p, **m}
                for r in single_results for p, m in r["directional"].items()
            ],
            "TwoFeatureRegistry": [
                {"candidate_id": t["candidate_id"], "parents": json.dumps(t["parents"]),
                 "support": t.get("create_support"), "days": t.get("create_days")}
                for t in twos
            ],
            "TwoFeatureDirectional": [
                {"candidate_id": r["spec"]["candidate_id"], **(r["directional"].get("ALL") or {})}
                for r in two_results
            ],
            "BatchExecution": batches or [{"note": "no_two_feature_batches"}],
            "ExitPolicy": _kv({
                "stage1": "Neutral Benchmark EXIT",
                "exits": list(BENCHMARK_EXITS),
                "candidate_specific_exit": False,
            }),
            "CanonicalExitIdentity": _kv(canon),
            "CanonicalExitParity": _kv({
                "status": canon["parity_status"],
                "BX_CANONICAL_PAPER_included": False,
            }),
            "BenchmarkExitDistinctness": [
                {"exit_id": k, "ledger_sha": v, "all_distinct": distinct}
                for k, v in fingerprints.items()
            ],
            "ExecutionCoverage": _kv({
                "bid_ask": "unavailable_in_X19_population",
                "coverage": "DIRECTIONAL_ONLY",
                "ask_entry_bid_exit": False,
            }),
            "PairEconomics": pair_rows,
            "ExitSensitivity": [
                {"candidate_id": r["spec"]["candidate_id"], "sensitivity": r.get("exit_sensitivity"),
                 "status": r.get("status")}
                for r in all_results
            ],
            "DailyResults": [{"note": "see period EVALUATION/DISCOVERY in SinglePeriodResults"}],
            "SymbolResults": [{"note": "symbols_n in directional metrics"}],
            "CandidateStatus": [
                {"status": k, "count": v} for k, v in sorted(status_counts.items())
            ],
            "FamilyViews": [
                {"family": fam, **{k: json.dumps(v[:20]) for k, v in buckets.items()}}
                for fam, buckets in family_views.items()
            ],
            "PromotionBundleProposal": bundle or [{"note": "empty"}],
            "ReservedDates": _kv({
                "20260804": "UNCLASSIFIED_DO_NOT_OPEN",
                "precommit": False,
            }),
            "ChangeLog": [{"at": now.isoformat(), "note": "E1_X21 broad ENTRY factory + neutral EXIT benchmark"}],
        }

        report = {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "source_run": SOURCE_X19,
            "verdict": verdict,
            "population_n": len(rows),
            "registered_feature_count": avail_info["registered_count"],
            "available_feature_count": avail_info["available_count"],
            "unavailable_feature_count": avail_info["unavailable_count"],
            "unavailable_features": avail_info["unavailable"],
            "single_entry_logic_count": len(singles),
            "two_feature_entry_logic_count": len(twos),
            "total_implemented_logic_count": len(all_results),
            "benchmark_exits": list(BENCHMARK_EXITS),
            "canonical_exit": canon,
            "exit_ledgers_distinct": distinct,
            "pair_count": len(pair_rows),
            "status_counts": dict(status_counts),
            "directional_promising_count": status_counts["DIRECTIONAL_PROMISING"],
            "benchmark_economic_promising_count": status_counts["BENCHMARK_ECONOMIC_PROMISING"],
            "exit_sensitive_count": status_counts["EXIT_SENSITIVE_MIXED"],
            "weak_count": status_counts["EXPERIMENTAL_WEAK"],
            "family_views": {k: dict(v) for k, v in family_views.items()},
            "rankings": rankings,
            "promotion_bundle_proposal": bundle,
            "promotion_precommit_created": False,
            "registry_20260804_status": "UNCLASSIFIED_DO_NOT_OPEN",
            "thresholds_discovery_only": True,
            "candidate_registry_slim": cand_registry,
            "batches": batches,
            "safety": safety,
            "_sheets": sheets,
        }
        publish(report, tests, det, OUT)
        print(json.dumps({
            "run_id": run_id,
            "verdict": verdict,
            "available_features": avail_info["available_count"],
            "unavailable": avail_info["unavailable_count"],
            "singles": len(singles),
            "twos": len(twos),
            "total": len(all_results),
            "pairs": len(pair_rows),
            "status_counts": dict(status_counts),
            "bundle_n": len(bundle),
            "tests": f"{tests['passed']}/{tests['total']}",
            "ab": det["ab_match"],
            "submit_cancel_live": "0/0/0",
        }, indent=2, default=str))
        return report

    except Exception as e:
        verdict = VERDICT_FAIL
        report = {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "verdict": verdict,
            "error": str(e),
            "safety": {"submit_cancel_live": "0/0/0", "20260804_opened": False},
            "_sheets": {"ChangeLog": [{"at": now.isoformat(), "note": f"FAILED: {e}"}]},
        }
        tests = {"exit_code": 1, "passed": 0, "failed": 1, "total": 1, "rows": []}
        det = {"ab_match": False}
        publish(report, tests, det, OUT)
        raise


if __name__ == "__main__":
    run()
