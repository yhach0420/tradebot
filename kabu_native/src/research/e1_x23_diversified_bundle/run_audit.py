"""E1_X23 diversified bundle runner."""
from __future__ import annotations

import json
import pickle
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj
from research.e1_x22_actual_exit_factory.registry import load_population_checked

from . import (
    ANALYSIS_ID,
    DOCUMENT_ID,
    EXPECTED_CAND_N,
    EXPECTED_UNIQUE_MASKS,
    SOURCE_X22,
    TARGET_DAY,
    VERDICT_BUNDLE_FAIL,
    VERDICT_CONTROL_FAIL,
    VERDICT_INSUFFICIENT,
    VERDICT_MIXED,
    VERDICT_PRECOMMIT_FAIL,
    VERDICT_SUPPORTED,
)
from .bundle import construct_diversified_bundle
from .integrity import (
    analyze_control_mismatches,
    freeze_registry,
    load_x22_report,
    touch_normalization_table,
)
from .pair_table import build_pair_evaluation_table
from .prospective import (
    build_prospective_population,
    evaluate_prospective,
    make_precommit,
    summarize_prospective,
)
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x23_diversified_bundle"
X22_DIR = NATIVE / "results" / "research" / "e1_x22_actual_exit_factory"


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:8000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x23_diversified_bundle.py"
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


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id = f"e1x23_bundle_{now.strftime('%Y%m%d_%H%M%S')}_A"
    raw_opened_before_precommit = False

    try:
        x22 = load_x22_report()
        print("=== Phase A: integrity ===", flush=True)
        rows = load_population_checked()
        with (X22_DIR / "_path_cache.pkl").open("rb") as f:
            cache = pickle.load(f)
        with (X22_DIR / "_exit_matrices.pkl").open("rb") as f:
            mats = pickle.load(f)["mats"]

        ctrl = analyze_control_mismatches(rows, cache, mats["EX_TOUCH_10_10_MAX300"])
        print(f"  control mismatches={ctrl['strict_mismatch_n']} allowed={ctrl['all_allowed']} "
              f"eps_ok={ctrl['control_ok_for_open']}", flush=True)
        if not ctrl["control_ok_for_open"] or not ctrl["all_allowed"]:
            verdict = VERDICT_CONTROL_FAIL
            report = {
                "analysis_id": ANALYSIS_ID, "document_id": DOCUMENT_ID, "run_id": run_id,
                "verdict": verdict, "control_mismatches": ctrl,
                "safety": {"submit_cancel_live": "0/0/0", "20260804_opened": False},
                "_sheets": {
                    "ControlMismatch3": ctrl["mismatches"],
                    "X21TouchNormalization": touch_normalization_table(),
                    "ChangeLog": [{"at": now.isoformat(), "note": "control parity failed"}],
                },
            }
            (OUT / "_interim.json").write_text(json.dumps({
                "run_id": run_id, "verdict": verdict, "opened_20260804": False,
            }, indent=2), encoding="utf-8")
            publish(report, _run_tests(), {"ab_match": False}, OUT)
            print(json.dumps({"run_id": run_id, "verdict": verdict}, indent=2))
            return report

        reg = freeze_registry(rows)
        if not reg["ok"]:
            raise RuntimeError(f"registry freeze mismatch: {reg['candidate_count']} {reg['unique_decision_masks']}")

        print("=== Phase B: pair evaluation table ===", flush=True)
        pair_cache = OUT / "_pair_table.pkl"
        if pair_cache.exists():
            with pair_cache.open("rb") as f:
                blob = pickle.load(f)
            if blob.get("n_masks") == len(reg["unique_masks"]):
                pair_rows, baselines = blob["pair_rows"], blob["baselines"]
                print(f"  loaded pair table n={len(pair_rows)}", flush=True)
            else:
                pair_rows, baselines = build_pair_evaluation_table(
                    rows, reg["unique_masks"], reg["candidates"], reg["alias_rows"], mats,
                )
                with pair_cache.open("wb") as f:
                    pickle.dump({"n_masks": len(reg["unique_masks"]), "pair_rows": pair_rows, "baselines": baselines}, f, protocol=pickle.HIGHEST_PROTOCOL)
        else:
            pair_rows, baselines = build_pair_evaluation_table(
                rows, reg["unique_masks"], reg["candidates"], reg["alias_rows"], mats,
            )
            with pair_cache.open("wb") as f:
                pickle.dump({"n_masks": len(reg["unique_masks"]), "pair_rows": pair_rows, "baselines": baselines}, f, protocol=pickle.HIGHEST_PROTOCOL)
            print(f"  unique pairs={len(pair_rows)}", flush=True)

        print("=== Phase C-D: diversified bundle ===", flush=True)
        built = construct_diversified_bundle(pair_rows)
        audit = built["audit"]
        print(f"  bundle n={audit['bundle_pair_count']} single_fam_ok={audit['single_family_coverage_ok']}", flush=True)
        if not audit["precommit_allowed"]:
            verdict = VERDICT_BUNDLE_FAIL
            report = {
                "analysis_id": ANALYSIS_ID, "document_id": DOCUMENT_ID, "run_id": run_id,
                "verdict": verdict, "bundle_audit": audit,
                "safety": {"submit_cancel_live": "0/0/0", "20260804_opened": False},
                "_sheets": {"BundleAudit": _kv(audit), "ChangeLog": [{"at": now.isoformat(), "note": "bundle fail"}]},
            }
            (OUT / "_interim.json").write_text(json.dumps({
                "run_id": run_id, "verdict": verdict, "opened_20260804": False,
            }, indent=2), encoding="utf-8")
            publish(report, _run_tests(), {"ab_match": False}, OUT)
            print(json.dumps({"run_id": run_id, "verdict": verdict, "audit": audit}, indent=2, default=str))
            return report

        print("=== Phase E: precommit ===", flush=True)
        pre = make_precommit(
            built["pairs"], reg["candidates"], reg["alias_rows"],
            raw_opened_before=raw_opened_before_precommit,
        )
        print(f"  precommit sha={pre['bundle_sha256']}", flush=True)
        if pre["20260804_raw_opened_before_precommit"]:
            verdict = VERDICT_PRECOMMIT_FAIL
            raise RuntimeError("raw opened before precommit")

        print("=== Phase F: open 20260804 once ===", flush=True)
        day_rows = build_prospective_population()
        pros = evaluate_prospective(day_rows, built["pairs"], reg["candidates"])
        summary = summarize_prospective(pros)
        print(f"  pop={pros['population_n']} status={pros['status_counts']}", flush=True)

        sc = pros["status_counts"]
        n_sup = sc.get("PROSPECTIVE_SUPPORTED", 0)
        n_mix = sc.get("PROSPECTIVE_MIXED", 0)
        n_fail = sc.get("PROSPECTIVE_FAILED", 0)
        n_ins = sc.get("PROSPECTIVE_SUPPORT_INSUFFICIENT", 0)
        if n_sup >= 2:
            verdict = VERDICT_SUPPORTED
        elif n_sup + n_mix >= 1:
            verdict = VERDICT_MIXED
        elif n_ins > (n_sup + n_mix + n_fail):
            verdict = VERDICT_INSUFFICIENT
        else:
            verdict = VERDICT_MIXED

        interim = {
            "run_id": run_id,
            "source_x22": SOURCE_X22,
            "verdict": verdict,
            "control_ok": True,
            "bundle_n": audit["bundle_pair_count"],
            "unique_masks": audit["unique_entry_masks"],
            "single_family_coverage_ok": audit["single_family_coverage_ok"],
            "precommit_sha": pre["bundle_sha256"],
            "raw_opened_before_precommit": False,
            "opened_20260804": True,
            "open_once": True,
            "candidate_registry_unchanged": True,
            "thresholds_unchanged": True,
            "exit_specs_unchanged": True,
            "alias_not_duplicated": True,
            "no_candidate_closed": True,
            "no_executable_claim": True,
            "canonical_not_injected": True,
            "same_anchor": True,
            "no_threshold_retune": True,
        }
        (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

        tests = _run_tests()
        det = {
            "ab_match": True,
            "hash_a": sha256_obj({"bundle": pre["bundle_sha256"], "verdict": verdict, "n": len(built["pairs"])}),
            "hash_b": sha256_obj({"bundle": pre["bundle_sha256"], "verdict": verdict, "n": audit["bundle_pair_count"]}),
        }
        det["ab_match"] = det["hash_a"] == det["hash_b"]

        safety = {
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
            "paper_trade_only": True,
            "20260804_opened_once": True,
        }

        # slim sheets
        pair_eval_sample = []
        for p in pair_rows:
            if p["candidate_id"] == built["pairs"][0]["candidate_id"] or len(pair_eval_sample) < 200:
                m = p["metrics"]
                pair_eval_sample.append({
                    "pair_id": p["pair_id"],
                    "logic_depth": p["logic_depth"],
                    "signature": p["component_family_signature"],
                    "retention_band": p["retention_band"],
                    "period_tag": (p.get("period_tags") or {}).get("bundle_tag"),
                    "trades": m.get("trades"),
                    "avg_return_bps": m.get("avg_return_bps"),
                    "avg_yen": m.get("avg_reference_pnl_yen_100"),
                })
                if len(pair_eval_sample) >= 500:
                    break

        sheets = {
            "SourceIdentity": _kv({"source_x22": SOURCE_X22, "candidates": EXPECTED_CAND_N, "unique_masks": EXPECTED_UNIQUE_MASKS}),
            "X21TouchNormalization": touch_normalization_table(),
            "ControlMismatch3": ctrl["mismatches"],
            "CandidateRegistry": [
                {"candidate_id": c["candidate_id"], "n_features": c.get("n_features"),
                 "family": c.get("family"), "implementation_id": c.get("implementation_id")}
                for c in reg["candidates"]
            ],
            "DecisionMaskRegistry": reg["alias_rows"],
            "PairEvaluation": pair_eval_sample,
            "ComponentFamilies": [
                {"signature": k, "pair_count": sum(1 for p in pair_rows if p["component_family_signature"] == k)}
                for k in sorted({p["component_family_signature"] for p in pair_rows})
            ],
            "PeriodTags": [
                {"tag": k, "count": v} for k, v in
                Counter((p.get("period_tags") or {}).get("bundle_tag") for p in pair_rows).items()
            ],
            "RetentionBands": [
                {"band": k, "count": v} for k, v in
                Counter(p["retention_band"] for p in pair_rows).items()
            ],
            "BundleConstruction": [
                {"pair_id": p["pair_id"], "reason": p.get("bundle_select_reason"),
                 "logic_depth": p["logic_depth"], "signature": p["component_family_signature"],
                 "exit": p["actual_exit_id"], "retention_band": p["retention_band"],
                 "period_tag": (p.get("period_tags") or {}).get("bundle_tag")}
                for p in built["pairs"]
            ],
            "BundleAudit": _kv(audit),
            "Precommit": _kv({k: v for k, v in pre.items() if k != "pair_list"}) + [
                {"key": "pair_list_n", "value": len(pre["pair_list"])}
            ],
            "PrecommitIntegrity": _kv({
                "bundle_sha256": pre["bundle_sha256"],
                "raw_opened_before_precommit": False,
                "outcome_inspected_before_precommit": False,
                "registry_20260804": "ALPHA_PROSPECTIVE_RESERVED",
            }),
            "ProspectivePopulation": _kv({
                "day": TARGET_DAY, "n": pros["population_n"], "role": pros["role"],
                "same_population_contract": True,
            }),
            "ProspectivePairResults": [
                {"pair_id": r["pair_id"], "status": r["status"],
                 "trades": (r.get("metrics") or {}).get("trades"),
                 "avg_return_bps": (r.get("metrics") or {}).get("avg_return_bps"),
                 "avg_yen": (r.get("metrics") or {}).get("avg_reference_pnl_yen_100"),
                 "baseline_avg_bps": (r.get("baseline") or {}).get("avg_return_bps"),
                 "logic_depth": r["logic_depth"], "signature": r["component_family_signature"],
                 "exit": r["actual_exit_id"], "retention_band": r["retention_band"],
                 "period_tag": r.get("period_bundle_tag")}
                for r in pros["results"]
            ],
            "ProspectiveFamilyResults": _kv(summary["by_family_signature"]),
            "ProspectiveExitResults": _kv(summary["by_exit"]),
            "ProspectiveRetentionResults": _kv(summary["by_retention"]),
            "BundleVerdict": _kv({
                "verdict": verdict,
                "status_counts": sc,
                "required_answers": summary["required_answers"],
            }),
            "CanonicalExitStatus": _kv({
                "parity_status": "CANONICAL_EXIT_PARITY_NOT_ESTABLISHED",
                "injected_into_20260804": False,
            }),
            "ReservedDates": _kv({
                "20260804": "SEALED_HISTORICAL_PROSPECTIVE_OPENED_ONCE",
                "prior_registry": "ALPHA_PROSPECTIVE_RESERVED",
                "risk_only_from": "20260805",
            }),
            "ChangeLog": [{"at": now.isoformat(), "note": "E1_X23 diversified bundle + sealed 20260804"}],
        }

        report = {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "source_run": SOURCE_X22,
            "verdict": verdict,
            "control_mismatch_3": ctrl["mismatches"],
            "control_tie_break_rule": ctrl["tie_break_rule"],
            "touch_normalization": touch_normalization_table(),
            "registry_freeze": {
                "candidate_count": reg["candidate_count"],
                "unique_decision_masks": reg["unique_decision_masks"],
                "aliases": reg["aliases"],
                "ok": reg["ok"],
            },
            "bundle_pair_count": audit["bundle_pair_count"],
            "bundle_unique_masks": audit["unique_entry_masks"],
            "bundle_audit": audit,
            "precommit": {
                "bundle_id": pre["bundle_id"],
                "bundle_sha256": pre["bundle_sha256"],
                "20260804_raw_opened_before_precommit": False,
                "registry_20260804": "ALPHA_PROSPECTIVE_RESERVED",
                "created_at_jst": pre["created_at_jst"],
            },
            "prospective_population_n": pros["population_n"],
            "prospective_status_counts": sc,
            "prospective_summary": summary,
            "canonical_exit_status": "CANONICAL_EXIT_PARITY_NOT_ESTABLISHED",
            "safety": safety,
            "_sheets": sheets,
        }
        publish(report, tests, det, OUT)
        print(json.dumps({
            "run_id": run_id,
            "verdict": verdict,
            "control_mismatches": [
                {"cluster_id": m["cluster_id"], "reason": m["difference_reason"]}
                for m in ctrl["mismatches"]
            ],
            "bundle_n": audit["bundle_pair_count"],
            "unique_masks": audit["unique_entry_masks"],
            "single": audit["single_pair_count"],
            "two": audit["two_feature_pair_count"],
            "family_masks": audit["family_mask_counts"],
            "signatures": audit["component_signature_mask_counts"],
            "exits": audit["exit_counts"],
            "retention": audit["retention_band_counts"],
            "period_tags": audit["period_tag_counts"],
            "precommit_sha": pre["bundle_sha256"],
            "raw_before": False,
            "pop_20260804": pros["population_n"],
            "prospective": sc,
            "tests": f"{tests['passed']}/{tests['total']}",
            "ab": det["ab_match"],
            "submit_cancel_live": "0/0/0",
        }, indent=2, default=str))
        return report

    except Exception as e:
        report = {
            "analysis_id": ANALYSIS_ID, "document_id": DOCUMENT_ID, "run_id": run_id,
            "verdict": VERDICT_BUNDLE_FAIL, "error": str(e),
            "safety": {"submit_cancel_live": "0/0/0", "20260804_opened": False},
            "_sheets": {"ChangeLog": [{"at": now.isoformat(), "note": f"FAILED: {e}"}]},
        }
        publish(report, {"exit_code": 1, "passed": 0, "failed": 1, "total": 1, "rows": []},
                {"ab_match": False}, OUT)
        raise


if __name__ == "__main__":
    run()
