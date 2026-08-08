"""E1_X33 runner: future-free causal anchor repair."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x28_executable_joint.board import verify_board_mapping
from research.e1_x31_population_direction.identity import ab_identity, reproduce_population

from . import (
    ANALYSIS_ID,
    ANCHOR_ID,
    BOARD_MAPPING_SHA,
    CLUSTER_WINDOW_SEC,
    DOCUMENT_ID,
    FORBIDDEN_FROM,
    HISTORICAL_DAYS,
    SOURCE_X30_RUN,
    SOURCE_X32_RUN,
    SOURCE_X32_VERDICT,
    VERDICT_CAUSALITY,
)
from .analyze import (
    anchor_spacing,
    day_level,
    decide_verdict,
    feature_eligibility_audit,
    loso_causal_parent,
    matched_delta,
    pass_criteria,
    raw_delta,
    session_end_censoring,
)
from .causality import dependency_manifest, prefix_invariance_test
from .eval_arms import (
    build_board_cache,
    control_feature_ok_fixed_clock,
    enrich_summary,
    evaluate_timestamps,
    old_from_labels,
    parent_fixed_clock,
)
from .grid_rebuild import rebuild_all_days
from .publish import freeze_manifest, publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x33_causal_anchor_repair"
X32 = NATIVE / "results" / "research" / "e1_x32_upstream_attribution"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x33_causal_anchor_repair.py"
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


def _x32_identity() -> dict[str, Any]:
    rep = json.loads((X32 / "report.json").read_text(encoding="utf-8"))
    ok = (
        rep.get("run_id") == SOURCE_X32_RUN
        and rep.get("verdict") == SOURCE_X32_VERDICT
        and "CANDIDATE_CLUSTER_ANCHORS" in str(rep.get("primary_culprit_transition") or "")
    )
    return {
        "run_id": rep.get("run_id"),
        "verdict": rep.get("verdict"),
        "primary_culprit_transition": rep.get("primary_culprit_transition"),
        "ok": ok,
        "parent_ret300": (rep.get("stage_summaries") or {}).get("CANDIDATE_SYMBOL_POOL", {}).get("ret300"),
        "parent_ret600": (rep.get("stage_summaries") or {}).get("CANDIDATE_SYMBOL_POOL", {}).get("ret600"),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x33_causal_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok"), mapping
    assert mapping.get("mapping_sha") == BOARD_MAPPING_SHA
    assert CLUSTER_WINDOW_SEC == 300

    print("=== X32 / X30 identity ===", flush=True)
    x32 = _x32_identity()
    assert x32["ok"], x32
    rows, labels, identity = reproduce_population()
    ab = ab_identity(rows, labels, identity)
    assert ab["ok"], ab
    assert identity["source_x30_run_id"] == SOURCE_X30_RUN
    print(f"  x32={x32['run_id']} pop={identity['population_n']}", flush=True)

    print("=== future-free grid rebuild ===", flush=True)
    rebuilt = rebuild_all_days(max_workers=6)
    grids, anchors = rebuilt["grids"], rebuilt["anchors"]

    print("=== prefix invariance ===", flush=True)
    prefix = prefix_invariance_test()
    print(f"  prefix={prefix['status']} tests={prefix['n_tests']} viol={prefix['violations']}", flush=True)
    dep = dependency_manifest()
    (OUT / "CAUSAL_ANCHOR_DEPENDENCY_MANIFEST_V1.json").write_text(
        json.dumps(dep, indent=2, default=str), encoding="utf-8"
    )

    if prefix["status"] == "CAUSALITY_VIOLATION":
        interim = {
            "run_id": run_id, "analysis_id": ANALYSIS_ID,
            "verdict_preview": VERDICT_CAUSALITY,
            "opened_20260810": False, "prefix_invariance": prefix["status"],
            "source_x32_run_id": SOURCE_X32_RUN, "population_n": identity["population_n"],
            "valid_n": identity["valid_n"], "cluster_window_sec": 300,
            "no_runtime_change": True, "no_entry_rule_search": True,
            "no_exit": True, "no_short": True,
        }
        (OUT / "_interim.json").write_text(json.dumps(interim, indent=2), encoding="utf-8")
        tests = _run_tests()
        report = {
            **interim, "verdict": VERDICT_CAUSALITY, "document_id": DOCUMENT_ID,
            "prefix_invariance": prefix, "dependency_manifest": dep,
            "tests": tests, "ab_determinism": ab,
            "opened_20260810": False, "must_be_false_20260810": True,
            "causal_anchor_manifest_created": False, "manifest_sha": None,
            "next_phase": None,
            "safety": {"submit_cancel_live": "0/0/0"},
            "artifacts": ["report.json", "report.md", "audit.xlsx",
                          "CAUSAL_ANCHOR_DEPENDENCY_MANIFEST_V1.json"],
        }
        publish(OUT, report, {"summary": [{"run_id": run_id, "verdict": VERDICT_CAUSALITY}]})
        print("=== STOP CAUSALITY_VIOLATION ===", flush=True)
        return report

    print("=== boards + arm evaluation ===", flush=True)
    boards = build_board_cache(rows, grids, anchors)
    old_evals = old_from_labels(rows, labels)
    print(f"  OLD n={len(old_evals)}", flush=True)
    parent_evals = parent_fixed_clock(rows, boards)
    print(f"  PARENT n={len(parent_evals)}", flush=True)
    causal_evals = evaluate_timestamps(rows=anchors, board_by_key=boards)
    print(f"  CAUSAL n={len(causal_evals)}", flush=True)
    control_evals = control_feature_ok_fixed_clock(grids, boards)
    print(f"  CONTROL n={len(control_evals)}", flush=True)

    old_s = enrich_summary(old_evals)
    parent_s = enrich_summary(parent_evals)
    causal_s = enrich_summary(causal_evals)
    control_s = enrich_summary(control_evals)

    cp_raw = raw_delta(causal_evals, parent_evals)
    cp_matched = matched_delta(causal_evals, parent_evals)
    co_raw = raw_delta(causal_evals, old_evals)
    days = day_level(parent_evals, old_evals, causal_evals)
    loso = loso_causal_parent(causal_evals, parent_evals)
    feat = feature_eligibility_audit(rebuilt["by_day"], grids, control_evals, parent_evals)
    censor = session_end_censoring(old_evals, causal_evals)
    spacing = anchor_spacing(rows, anchors)

    criteria = pass_criteria(
        prefix_ok=bool(prefix.get("prefix_invariance")),
        uses_future=False,
        matched=cp_matched,
        day=days,
    )
    decision = decide_verdict(
        criteria=criteria, feat_audit=feat, prefix_status=prefix["status"]
    )

    manifest = None
    manifest_sha = None
    if decision.get("freeze_manifest"):
        manifest = freeze_manifest(dependency=dep, prefix=prefix)
        manifest_sha = manifest["sha256"]
        (OUT / "CAUSAL_ANCHOR_MANIFEST_V1.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )

    interim = {
        "run_id": run_id,
        "analysis_id": ANALYSIS_ID,
        "source_x32_run_id": SOURCE_X32_RUN,
        "source_x30_run_id": SOURCE_X30_RUN,
        "population_n": identity["population_n"],
        "valid_n": identity["valid_n"],
        "opened_20260810": False,
        "cluster_window_sec": CLUSTER_WINDOW_SEC,
        "anchor_id": ANCHOR_ID,
        "no_runtime_change": True,
        "no_entry_rule_search": True,
        "no_anchor_grid_search": True,
        "no_exit": True,
        "no_short": True,
        "prefix_invariance": prefix["status"],
        "verdict_preview": decision["verdict"],
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    print("=== tests ===", flush=True)
    tests = _run_tests()

    report = {
        **interim,
        "document_id": DOCUMENT_ID,
        "verdict": decision["verdict"],
        "next_phase": decision.get("next_phase"),
        "board_mapping_sha": BOARD_MAPPING_SHA,
        "forbidden_from": FORBIDDEN_FROM,
        "historical_days": list(HISTORICAL_DAYS),
        "x32_identity": x32,
        "identity": identity,
        "ab_determinism": ab,
        "grid_rebuild_by_day": rebuilt["by_day"],
        "feature_eligibility": feat,
        "old_summary": old_s,
        "parent_summary": parent_s,
        "causal_summary": causal_s,
        "control_summary": control_s,
        "causal_parent": {**cp_raw, **cp_matched},
        "causal_old": co_raw,
        "day_level": days,
        "negative_days": {
            "causal_parent_300": days["negative_delta_days_300"],
            "causal_parent_600": days["negative_delta_days_600"],
            "of": 14,
        },
        "loso": loso,
        "prefix_invariance": prefix,
        "dependency_manifest": dep,
        "pass_criteria": criteria,
        "session_end_censoring": censor,
        "anchor_spacing": spacing,
        "old_reference_only": True,
        "causal_anchor_manifest_created": bool(manifest),
        "manifest_sha": manifest_sha,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "must_be_false_20260810": True,
        "tests": tests,
        "safety": {
            "submit_cancel_live": "0/0/0",
            "paper_only": True,
            "no_runtime_universe_entry_exit_change": True,
            "no_short": True,
            "no_discord_production": True,
        },
        "artifacts": [
            "report.json", "report.md", "audit.xlsx",
            "CAUSAL_ANCHOR_DEPENDENCY_MANIFEST_V1.json",
            *(["CAUSAL_ANCHOR_MANIFEST_V1.json"] if manifest else []),
        ],
    }

    sheets = {
        "summary": [{
            "run_id": run_id, "verdict": decision["verdict"],
            "matched_d300": cp_matched.get("matched_delta300"),
            "matched_d600": cp_matched.get("matched_delta600"),
            "pass": criteria["pass"], "next": decision.get("next_phase"),
            "opened_20260810": False,
        }],
        "arms": [
            {"arm": "OLD", **{k: v for k, v in old_s.items() if not isinstance(v, (dict, list))}},
            {"arm": "PARENT", **{k: v for k, v in parent_s.items() if not isinstance(v, (dict, list))}},
            {"arm": "CAUSAL", **{k: v for k, v in causal_s.items() if not isinstance(v, (dict, list))}},
            {"arm": "CONTROL", **{k: v for k, v in control_s.items() if not isinstance(v, (dict, list))}},
        ],
        "days": days["days"],
        "grid_meta": rebuilt["by_day"],
        "prefix": prefix.get("sample") or [{"status": prefix["status"]}],
    }
    publish(OUT, report, sheets)
    print(f"=== DONE verdict={decision['verdict']} ===", flush=True)
    return report


if __name__ == "__main__":
    main()
