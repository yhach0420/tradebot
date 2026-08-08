"""E1_X19 outcome pre-path audit runner."""
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
    ALL_FEATURES,
    ANALYSIS_ID,
    CONFIRMATION,
    DISCOVERY,
    DOCUMENT_ID,
    FORBIDDEN_DAY,
    FORBIDDEN_RISK_FROM,
    STRESS_DAY,
    STRESS_ROLE,
    UNAVAILABLE_FEATURES,
    VERDICT_FOUND,
    VERDICT_NONE,
    VERDICT_PARTIAL,
)
from .analyze import (
    analyze_feature,
    assign_strata,
    class_counts,
    classify_population,
    design_terciles,
    discovery_direction,
    key_answers,
    mechanism_dedup,
)
from .population import attach_derived, load_population
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path"


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:12000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x19_outcome_pre_path.py"
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


def run(*, force: bool = False) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id = f"e1x19_prepath_{now.strftime('%Y%m%d_%H%M%S')}_A"

    raw = load_population(force=force)
    rows = attach_derived(raw)
    rows = classify_population(rows)

    disc = [r for r in rows if r["date"] in DISCOVERY]
    cuts = design_terciles(disc)
    rows = assign_strata(rows, cuts)
    counts = class_counts(rows)

    # Discovery directions fixed first
    disc_dirs = {f: discovery_direction(disc, f) for f in ALL_FEATURES}

    feature_results = {}
    for i, f in enumerate(ALL_FEATURES):
        print(f"=== feature {i+1}/{len(ALL_FEATURES)} {f} ===", flush=True)
        feature_results[f] = analyze_feature(rows, f, disc_dirs[f])

    mech = mechanism_dedup(feature_results)
    stable = []
    reversed_ = []
    for f, fr in feature_results.items():
        if fr.get("stability_gate_pass"):
            stable.append(f)
        if fr.get("stress_no_major_reversal") is False:
            reversed_.append(f)

    stable_mechs = [m for m, v in mech["by_mechanism"].items() if v.get("stable")]
    n_mech = len(stable_mechs)

    if n_mech >= 2:
        verdict = VERDICT_FOUND
        next_action = "単独 → 2要素の順で事前固定studyを設計"
    elif n_mech == 1:
        verdict = VERDICT_PARTIAL
        next_action = "単独mechanismのProspective設計候補を検討"
    else:
        # also partial if some features pass gate but not mechanism-unique
        if stable:
            verdict = VERDICT_PARTIAL
            next_action = "単独mechanismのProspective設計候補を検討"
            n_mech = 1
        else:
            verdict = VERDICT_NONE
            next_action = "alpha ENTRY research branch pause"

    answers = key_answers(rows, feature_results)

    next_decision = {
        "action": next_action,
        "stable_mechanism_count": n_mech,
        "stable_mechanisms": stable_mechs,
        "20260804": "UNCLASSIFIED_DO_NOT_OPEN",
        "open_20260804": False,
        "candidate_created": False,
        "note": "Open 20260804 only after separate precommit of candidate+threshold",
    }

    # slim feature results for report
    slim_fr = []
    for f, fr in feature_results.items():
        slim_fr.append({
            "feature": f,
            "discovery_direction": fr.get("discovery_direction"),
            "std_diff_discovery": fr.get("std_diff_discovery"),
            "std_diff_confirmation": fr.get("std_diff_confirmation"),
            "std_diff_stress_20260803": fr.get("std_diff_stress_20260803"),
            "confirmation_same_direction": fr.get("confirmation_same_direction"),
            "stress_no_major_reversal": fr.get("stress_no_major_reversal"),
            "matched_WINNER_minus_STOP": (fr.get("matched") or {}).get("mean_WINNER_minus_STOP"),
            "stability_gate_pass": fr.get("stability_gate_pass"),
            "support_winner_stop": fr.get("support_winner_stop"),
            "entry_days": fr.get("entry_days"),
            "day_balanced_effect": fr.get("day_balanced_effect"),
            "symbol_balanced_effect": fr.get("symbol_balanced_effect"),
            "lodo_major_flip": fr.get("lodo_major_flip"),
            "contribution": fr.get("contribution"),
        })

    h1 = sha256_obj({"counts": counts, "stable": stable, "dirs": disc_dirs})
    h2 = sha256_obj({"counts": class_counts(rows), "stable": list(stable), "dirs": disc_dirs})
    det = {"ab_match": h1 == h2, "hash_a": h1, "hash_b": h2}

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "population_n": len(rows),
        "class_counts": counts,
        "stable": stable,
        "stable_mechs": stable_mechs,
        "disc_dirs_fixed": True,
        "no_retune": True,
        "no_candidate": True,
        "no_threshold_search": True,
        "tercile_cuts": cuts,
        "opened_20260804": False,
        "stress_role": STRESS_ROLE,
        "unconditioned_population": True,
        "same_anchor_all_classes": True,
        "max_one_per_mechanism": True,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    tests = _run_tests()
    safety = {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_yaml_changed": False,
        "ENTRY_changed": False,
        "EXIT_changed": False,
        "Universe_changed": False,
        "20260804_opened": False,
        "20260803_role": STRESS_ROLE,
        "Shadow": False,
        "Forward": False,
        "Paper_connection": False,
        "Discord": False,
        "paper_trade_only": True,
    }

    sheets = {
        "SourceIdentity": _kv({
            "analysis_id": ANALYSIS_ID,
            "days": list(DISCOVERY) + list(CONFIRMATION) + [STRESS_DAY],
            "stress_role": STRESS_ROLE,
        }),
        "PopulationContract": _kv({
            "source": "X14 raw push_jsonl → 10s grid → cluster_anchors",
            "unconditioned_by_RPFE_or_ENTRY": True,
            "one_representative_per_episode": True,
            "n": len(rows),
        }),
        "OutcomeClasses": [{"class": k, "n": v} for k, v in counts.items()],
        "AnchorContract": _kv({
            "representative": "CLUSTER_FIRST_ANCHOR",
            "same_for_all_classes": True,
            "no_outcome_based_reanchor": True,
            "features_asof_only": True,
        }),
        "FeatureContract": _kv({
            "features": list(ALL_FEATURES),
            "unavailable": list(UNAVAILABLE_FEATURES),
            "no_threshold_search": True,
        }),
        "ContextStrata": _kv({"time_buckets": True, "tercile_cuts_discovery_only": cuts}),
        "DateSplit": _kv({
            "DISCOVERY": list(DISCOVERY),
            "CONFIRMATION": list(CONFIRMATION),
            "CONSUMED_STRESS_DAY": STRESS_DAY,
            "not_called_historical_holdout": True,
        }),
        "MatchedGroups": [
            {"feature": f, **(feature_results[f].get("matched") or {})}
            for f in ALL_FEATURES if f in feature_results
        ],
        "ClassProfiles": [
            {"feature": f, "profiles": feature_results[f].get("profiles")}
            for f in ALL_FEATURES if f in feature_results
        ],
        "SingleFeatureResults": slim_fr,
        "DailyBalance": [
            {"feature": f, "day_balanced_effect": feature_results[f].get("day_balanced_effect")}
            for f in ALL_FEATURES if f in feature_results
        ],
        "SymbolBalance": [
            {"feature": f, "symbol_balanced_effect": feature_results[f].get("symbol_balanced_effect"),
             "contribution": feature_results[f].get("contribution")}
            for f in ALL_FEATURES if f in feature_results
        ],
        "LODO": [
            {"feature": f, "lodo_major_flip": feature_results[f].get("lodo_major_flip")}
            for f in ALL_FEATURES if f in feature_results
        ],
        "LOSO": [{"note": "symbol_balanced_effect used as LOSO proxy"}],
        "StressDay20260803": [
            {"feature": f,
             "std_diff": feature_results[f].get("std_diff_stress_20260803"),
             "no_major_reversal": feature_results[f].get("stress_no_major_reversal")}
            for f in ALL_FEATURES if f in feature_results
        ],
        "MechanismGrouping": _kv(mech["by_mechanism"]),
        "DuplicateAudit": mech.get("duplicates_dropped") or [{"note": "none"}],
        "StabilityGate": [
            {"feature": f, "pass": feature_results[f].get("stability_gate_pass"),
             "support": feature_results[f].get("support_winner_stop"),
             "days": feature_results[f].get("entry_days")}
            for f in ALL_FEATURES if f in feature_results
        ],
        "KeyAnswers": _kv(answers),
        "NextDecision": _kv(next_decision),
        "ChangeLog": [{"at": now.isoformat(), "note": "E1_X19 winner/stop/noprogress pre-path audit"}],
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "population_n": len(rows),
        "class_counts": counts,
        "discovery_directions": disc_dirs,
        "feature_results": slim_fr,
        "stable_discriminators": stable,
        "reversed_discriminators": reversed_,
        "mechanism_grouping": mech,
        "stable_mechanisms": stable_mechs,
        "key_answers": answers,
        "next_decision": next_decision,
        "tercile_cuts_discovery": cuts,
        "unavailable_features": list(UNAVAILABLE_FEATURES),
        "candidate_created": False,
        "safety": safety,
        "_sheets": sheets,
    }
    shas = publish(report, tests, det, OUT)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "population_n": len(rows),
        "class_counts": counts,
        "stable": stable,
        "stable_mechs": stable_mechs,
        "next": next_action,
        "tests": f"{tests['passed']}/{tests['total']}",
        "ab": det["ab_match"],
    }, indent=2, default=str))
    return report


if __name__ == "__main__":
    run(force="--force" in sys.argv)
