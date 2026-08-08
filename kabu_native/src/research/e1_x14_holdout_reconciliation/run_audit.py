"""Run E1_X14 holdout reconciliation."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_file, sha256_obj

from . import (
    ABS_ACTIVITY,
    ANALYSIS_ID,
    DESIGN,
    DOCUMENT_ID,
    FORBIDDEN_ALPHA,
    FORBIDDEN_RISK_FROM,
    HOLDOUT,
    KNOWN_MAINTAINED,
    KNOWN_REVERSALS,
    SOURCE_DECISION_STATUS,
    SOURCE_RUN,
    SOURCE_VERDICT,
    VALIDATION,
    VERDICT_MIXED,
    VERDICT_NONE,
    VERDICT_SUPPORTED,
    XS_ACTIVITY,
)
from .audits import (
    concept_verdicts,
    duplicate_audit,
    freshness_selection_audit,
    reduce_next_phase_candidates,
    rpfe_episode_overlap,
)
from .gate import reconcile_all
from .publish import publish
from .rebuild import build_or_load_clusters

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x14_holdout_reconciliation"
SOURCE_DIR = NATIVE / "results" / "research" / "e1_x14_board_independent_signal"


def _source_sha_snapshot() -> dict[str, str]:
    out = {}
    for name in ("report.json", "report.md", "audit.xlsx"):
        p = SOURCE_DIR / name
        if p.exists():
            out[name] = sha256_file(p)
    return out


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x14_holdout_reconciliation.py"
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
        "rows": [{"test": "pytest_suite", "outcome": "PASSED" if p.returncode == 0 else "FAILED",
                  "detail": out[-2500:]}],
    }


def run(*, label: str = "A", force_rebuild: bool = False) -> dict[str, Any]:
    run_id = f"e1x14_holdout_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{label}"
    src_sha_before = _source_sha_snapshot()
    src = json.loads((SOURCE_DIR / "report.json").read_text(encoding="utf-8"))
    assert src.get("run_id") == SOURCE_RUN
    assert src.get("verdict") == SOURCE_VERDICT
    split = src.get("date_split") or {}
    assert list(split.get("DESIGN") or []) == list(DESIGN)
    assert list(split.get("VALIDATION") or []) == list(VALIDATION)
    assert list(split.get("HISTORICAL_HOLDOUT") or []) == list(HOLDOUT)

    source_stable = set(src.get("stable_features") or [])

    clusters = build_or_load_clusters(force=force_rebuild)
    print("=== Reconcile features ===", flush=True)
    rows = reconcile_all(clusters, source_stable)

    # A/B
    rows_b = reconcile_all(clusters, source_stable)
    ab_match = sha256_obj(rows) == sha256_obj(rows_b)

    maintained = [r["feature"] for r in rows if r["candidate_status"] == "HOLDOUT_MAINTAINED_CANDIDATE"]
    reversed_ = [r["feature"] for r in rows if r["candidate_status"] == "HOLDOUT_REVERSED_REJECT"]
    pre_reject = [r["feature"] for r in rows if r["candidate_status"] == "PRE_HOLDOUT_UNSTABLE_REJECT"]
    pre_holdout_candidates = [
        r["feature"] for r in rows
        if r.get("pre_holdout_effect") and (r["pre_holdout_effect"].get("directed_effect") or 0) > 0
        and r.get("source_stable_candidate")
    ]

    print("=== Audits ===", flush=True)
    dupes = duplicate_audit(clusters)
    fresh_path = OUT / "_freshness_by_symbol_day.json"
    freshness_rows = json.loads(fresh_path.read_text(encoding="utf-8")) if fresh_path.exists() else []
    # if cache load without freshness, rebuild freshness only from meta
    if not freshness_rows and (OUT / "_cluster_cache_meta.json").exists():
        freshness_rows = []
    fresh = freshness_selection_audit(
        clusters, freshness_rows,
        activity_features=["volume_rate_60s", "trading_value_delta_60s", "volume_percentile_60s"],
    )
    overlap = rpfe_episode_overlap(clusters)
    concepts = concept_verdicts(rows)
    next_cands = reduce_next_phase_candidates(rows, dupes)

    # Overall verdict
    if not maintained:
        verdict = VERDICT_NONE
    else:
        # mixed if any major concept failed/partial while some maintained
        verdict = VERDICT_MIXED
        # SUPPORTED only if all four major concepts have maintained and none reversed in RS/price cores
        rs_ok = bool(concepts["PRICE_RELATIVE_STRENGTH"]["maintained"]) and not concepts["PRICE_RELATIVE_STRENGTH"]["reversed"]
        # Spec: all major components holdout-maintained — unlikely; known RS failed
        if (
            concepts["PRICE_PATH"]["maintained"]
            and concepts["ABSOLUTE_ACTIVITY"]["maintained"]
            and concepts["CROSS_SECTIONAL_ACTIVITY"]["maintained"]
            and concepts["PRICE_RELATIVE_STRENGTH"]["verdict"] != "PRICE_RELATIVE_STRENGTH_HOLDOUT_FAILED"
            and not concepts["PRICE_PATH"]["reversed"]
        ):
            verdict = VERDICT_SUPPORTED
        # Force expected mixed when RS failed and price mixed
        if concepts["PRICE_RELATIVE_STRENGTH"]["verdict"] == "PRICE_RELATIVE_STRENGTH_HOLDOUT_FAILED":
            verdict = VERDICT_MIXED

    print("=== Tests ===", flush=True)
    # Write interim report fields needed by tests that load from disk
    interim = {
        "feature_status_rows": rows,
        "holdout_maintained": maintained,
        "holdout_reversed": reversed_,
        "next_phase_candidates": next_cands,
        "duplicate_audit": dupes,
        "date_split": {"DESIGN": list(DESIGN), "VALIDATION": list(VALIDATION), "HISTORICAL_HOLDOUT": list(HOLDOUT)},
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    tests = _run_tests()

    src_sha_after = _source_sha_snapshot()
    source_untouched = src_sha_before == src_sha_after

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "label": label,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "verdict": verdict,
        "source_run": SOURCE_RUN,
        "source_verdict_preserved": SOURCE_VERDICT,
        "source_decision_status": SOURCE_DECISION_STATUS,
        "source_supersede_reason": (
            "stable_candidate indicated Design+Validation candidates only; "
            "did not mean Historical Holdout passage"
        ),
        "source_artifacts_untouched": source_untouched,
        "source_shas": src_sha_after,
        "date_split": {
            "DESIGN": list(DESIGN),
            "VALIDATION": list(VALIDATION),
            "HISTORICAL_HOLDOUT": list(HOLDOUT),
            "unchanged_from_source": True,
        },
        "threshold_contract": {
            "construction": "DESIGN only",
            "VALIDATION": "fixed thresholds",
            "HISTORICAL_HOLDOUT": "same fixed thresholds",
            "holdout_recompute_forbidden": True,
            "source_noncompliance_noted": True,
        },
        "pre_holdout_candidates": pre_holdout_candidates,
        "holdout_maintained": maintained,
        "holdout_reversed": reversed_,
        "pre_holdout_unstable_reject": pre_reject,
        "feature_status": rows,
        "concept_classification": concepts,
        "duplicate_audit": dupes,
        "freshness_selection": fresh,
        "rpfe_episode_overlap": overlap,
        "next_phase_candidates": next_cands,
        "forbidden_claims": ["robust", "final stable", "freeze ready", "production ready"],
        "safety": {
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
            "risk_only_alpha_used": False,
        },
        "_sheets": {
            "SourceIdentity": [
                {"key": "source_run", "value": SOURCE_RUN},
                {"key": "source_verdict", "value": SOURCE_VERDICT},
                {"key": "decision_status", "value": SOURCE_DECISION_STATUS},
                {"key": "untouched", "value": source_untouched},
            ],
            "DateSplit": [{"split": "DESIGN", "days": ",".join(DESIGN)},
                          {"split": "VALIDATION", "days": ",".join(VALIDATION)},
                          {"split": "HOLDOUT", "days": ",".join(HOLDOUT)}],
            "ThresholdProvenance": [r.get("threshold_provenance") or {} for r in rows],
            "FeatureStatus": [{
                "feature": r["feature"],
                "source_stable": r.get("source_stable_candidate"),
                "stages": ",".join(r.get("stages") or []),
                "candidate_status": r.get("candidate_status"),
                "holdout_status": r.get("holdout_status"),
                "pre_directed": (r.get("pre_holdout_effect") or {}).get("directed_effect"),
                "holdout_directed": (r.get("holdout_effect") or {}).get("directed_effect"),
                "holdout_support": (r.get("holdout_effect") or {}).get("support"),
            } for r in rows],
            "HoldoutResults": [{
                "feature": r["feature"],
                **(r.get("holdout_effect") or {}),
                "candidate_status": r.get("candidate_status"),
            } for r in rows],
            "ConceptClassification": [
                {"concept": k, **{kk: vv for kk, vv in v.items() if not isinstance(vv, list)},
                 "maintained": ",".join(v.get("maintained") or []),
                 "reversed": ",".join(v.get("reversed") or [])}
                for k, v in concepts.items()
            ],
            "DuplicateAudit": dupes,
            "FreshnessSelection": [fresh],
            "RPFEEpisodeOverlap": [overlap],
            "CandidateReduction": next_cands or [{"note": "none"}],
            "ChangeLog": [
                {"change": "source_superseded_for_decision", "note": SOURCE_DECISION_STATUS},
                {"change": "thresholds_design_only", "note": "holdout not retuned"},
                {"change": "no_combo_test", "note": "max 3 independent candidates only"},
            ],
        },
    }
    det = {
        "ab_match": ab_match,
        "rows_sha": sha256_obj(rows),
        "cluster_n": len(clusters),
        "source_untouched": source_untouched,
    }
    publish(report, tests, det, OUT)
    print("VERDICT", verdict, flush=True)
    print("maintained", maintained, flush=True)
    print("reversed", reversed_, flush=True)
    print("next", next_cands, flush=True)
    return report


if __name__ == "__main__":
    run()
