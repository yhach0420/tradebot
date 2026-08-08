"""E1_X33B runner: validate & freeze NEUTRAL_FIXED_CLOCK_ANCHOR_V1."""
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
from research.e1_x32_upstream_attribution.eval_stages import load_boards_for_symbols
from research.e1_x33_causal_anchor_repair.eval_arms import (
    old_from_labels,
    parent_fixed_clock,
)

from . import (
    ANALYSIS_ID,
    ANCHOR_ID,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    FORBIDDEN_FROM,
    HISTORICAL_DAYS,
)
from .analyze import (
    coverage_audit,
    day_level,
    lodo,
    loso,
    matched_comparison,
    pass_and_verdict,
    summarize_arm,
    tod_coverage,
)
from .identity import exact_fixed_clock_semantics, resolve_x33_identity
from .neutral import (
    candidate_symbols_by_day,
    dependency_manifest,
    evaluate_neutral,
    planned_neutral_anchors,
    prefix_invariance_neutral,
)
from .publish import freeze_anchor_manifest, publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x33b_neutral_anchor.py"
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
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-1500:]}


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x33b_neutral_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok"), mapping
    assert mapping.get("mapping_sha") == BOARD_MAPPING_SHA

    print("=== X33 identity resolution ===", flush=True)
    x33 = resolve_x33_identity()
    print(
        f"  reported={x33['reported_run_id']} artifact={x33['artifact_run_id']} "
        f"canonical={x33['canonical_run_id']}",
        flush=True,
    )
    print(f"  reason: {x33['reason_for_resolution'][:120]}...", flush=True)

    semantics = exact_fixed_clock_semantics()

    print("=== population (same pool) ===", flush=True)
    rows, labels, identity = reproduce_population()
    ab = ab_identity(rows, labels, identity)
    assert ab["ok"], ab
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows)
    pool = candidate_symbols_by_day(rows)

    print("=== prefix invariance ===", flush=True)
    prefix = prefix_invariance_neutral(pool)
    print(f"  prefix={prefix['status']} n={prefix['n_tests']} viol={prefix['violations']}", flush=True)
    dep = dependency_manifest(prefix, semantics)

    print("=== boards + neutral/parent eval ===", flush=True)
    pairs = sorted({(d, s) for d, syms in pool.items() for s in syms})
    boards = load_boards_for_symbols(pairs)
    planned = planned_neutral_anchors(pool)
    neutral = evaluate_neutral(planned, boards)
    parent = parent_fixed_clock(rows, boards)
    old = old_from_labels(rows, labels)
    print(f"  planned={len(planned)} neutral_exec={len(neutral)} parent={len(parent)}", flush=True)

    # A/B: rebuild parent path twice
    parent_b = parent_fixed_clock(rows, boards)
    ab_parent = len(parent) == len(parent_b) and abs(
        (summarize_arm(parent).get("ret300_episode") or 0)
        - (summarize_arm(parent_b).get("ret300_episode") or 0)
    ) < 1e-9

    neu_s = summarize_arm(neutral)
    par_s = summarize_arm(parent)
    old_s = summarize_arm(old)
    # also bind X33 causal from report for diagnostic
    causal_bound = x33["bound_facts"]["CAUSAL_CLUSTER_FIRST_V1"]

    matched = matched_comparison(neutral, parent)
    days = day_level(neutral, parent)
    cov = coverage_audit(planned, neutral, parent)
    tod = tod_coverage(neutral)
    loso_res = loso(neutral, parent)
    lodo_res = lodo(days)

    # balanced deltas
    bal_d300 = (
        (neu_s["ret300_balanced"] - par_s["ret300_balanced"])
        if neu_s["ret300_balanced"] is not None and par_s["ret300_balanced"] is not None
        else None
    )
    bal_d600 = (
        (neu_s["ret600_balanced"] - par_s["ret600_balanced"])
        if neu_s["ret600_balanced"] is not None and par_s["ret600_balanced"] is not None
        else None
    )

    decision = pass_and_verdict(
        prefix_ok=bool(prefix.get("prefix_invariance")),
        uses_future=False,
        matched=matched,
        day=days,
        cov=cov,
        loso_res=loso_res,
    )

    manifest = None
    manifest_sha = None
    if decision.get("freeze_manifest"):
        (OUT / "NEUTRAL_ANCHOR_DEPENDENCY_MANIFEST_V1.json").write_text(
            json.dumps(dep, indent=2, default=str), encoding="utf-8"
        )
        manifest = freeze_anchor_manifest(semantics=semantics, dependency=dep, prefix=prefix)
        manifest_sha = manifest["sha256"]
        (OUT / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )
    else:
        # still save dependency for audit
        (OUT / "NEUTRAL_ANCHOR_DEPENDENCY_MANIFEST_V1.json").write_text(
            json.dumps(dep, indent=2, default=str), encoding="utf-8"
        )

    interim = {
        "run_id": run_id,
        "analysis_id": ANALYSIS_ID,
        "canonical_x33_run_id": x33["canonical_run_id"],
        "opened_20260810": False,
        "no_runtime_change": True,
        "no_entry_search": True,
        "no_exit": True,
        "no_short": True,
        "no_anchor_performance_search": True,
        "population_n": identity["population_n"],
        "valid_n": identity["valid_n"],
        "verdict_preview": decision["verdict"],
        "prefix_invariance": prefix["status"],
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
        "historical_days": list(HISTORICAL_DAYS),
        "forbidden_from": FORBIDDEN_FROM,
        "x33_identity": {
            "reported_run_id": x33["reported_run_id"],
            "artifact_run_id": x33["artifact_run_id"],
            "canonical_run_id": x33["canonical_run_id"],
            "reason_for_resolution": x33["reason_for_resolution"],
        },
        "bound_x33_facts": x33["bound_facts"],
        "neutral_anchor_exact_semantics": semantics,
        "identity": identity,
        "ab_determinism": {**ab, "parent_rebuild": ab_parent},
        "future_dependency": False,
        "prefix_invariance": prefix,
        "dependency_manifest": dep,
        "parent": par_s,
        "neutral": neu_s,
        "old_diagnostic": old_s,
        "causal_diagnostic_from_x33": causal_bound,
        "raw_delta300": (
            neu_s["ret300_episode"] - par_s["ret300_episode"]
            if neu_s["ret300_episode"] is not None and par_s["ret300_episode"] is not None else None
        ),
        "raw_delta600": (
            neu_s["ret600_episode"] - par_s["ret600_episode"]
            if neu_s["ret600_episode"] is not None and par_s["ret600_episode"] is not None else None
        ),
        "matched": matched,
        "symbol_session_balanced_delta300": bal_d300,
        "symbol_session_balanced_delta600": bal_d600,
        "day_level": days,
        "daily_median_abs_delta300": days.get("median_abs_delta300"),
        "daily_median_abs_delta600": days.get("median_abs_delta600"),
        "negative_days": {
            "delta300": days.get("negative_delta_days_300"),
            "delta600": days.get("negative_delta_days_600"),
            "of": 14,
        },
        "coverage": cov,
        "time_of_day_coverage": tod,
        "lodo": lodo_res,
        "loso": loso_res,
        "pass_checks": decision.get("checks"),
        "old_cluster_reference_only": True,
        "neutral_manifest_created": bool(manifest),
        "manifest_sha": manifest_sha,
        "anchor_id": ANCHOR_ID,
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
            "NEUTRAL_ANCHOR_DEPENDENCY_MANIFEST_V1.json",
            *(["NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json"] if manifest else []),
        ],
    }

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "canonical_x33": x33["canonical_run_id"],
            "matched_d300": matched.get("delta300"),
            "matched_d600": matched.get("delta600"),
            "coverage": cov.get("coverage_share"),
            "manifest_sha": manifest_sha,
            "opened_20260810": False,
        }],
        "days": days["days"],
        "tod": tod["buckets"],
        "checks": [{"check": k, "ok": v} for k, v in (decision.get("checks") or {}).items()],
        "lodo": lodo_res.get("rows") or [{"empty": True}],
    }
    publish(OUT, report, sheets)
    print(f"=== DONE verdict={decision['verdict']} ===", flush=True)
    return report


if __name__ == "__main__":
    main()
