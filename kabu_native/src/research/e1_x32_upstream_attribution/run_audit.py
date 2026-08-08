"""E1_X32 runner: upstream attribution (no redesign, no 0810)."""
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
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    FORBIDDEN_FROM,
    HISTORICAL_DAYS,
    SAMPLING_SEED,
    SOURCE_X30_RUN,
    SOURCE_X31_RUN,
    SOURCE_X31_VERDICT,
)
from .analyze import (
    classify_effects,
    decide_verdict,
    refine_verdict_for_coverage,
    run_attribution,
    selection_characteristics,
)
from .eval_stages import build_stage_evals
from .funnel import freeze_canonical_funnel, transitions
from .membership import coverage_by_day
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x32_upstream_attribution"
X31 = NATIVE / "results" / "research" / "e1_x31_population_direction"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x32_upstream_attribution.py"
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


def _x31_identity() -> dict[str, Any]:
    rep = json.loads((X31 / "report.json").read_text(encoding="utf-8"))
    ok = (
        rep.get("run_id") == SOURCE_X31_RUN
        and rep.get("verdict") == SOURCE_X31_VERDICT
        and rep.get("population_n") == 22491
        and rep.get("valid_n") == 13104
    )
    return {
        "run_id": rep.get("run_id"),
        "verdict": rep.get("verdict"),
        "ok": ok,
        "candidate_ret300": rep.get("candidate_ret300"),
        "same_symbol_control_ret300": rep.get("same_symbol_control_ret300"),
        "market_time_control_ret300": rep.get("market_time_control_ret300"),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x32_attr_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok"), mapping
    assert mapping.get("mapping_sha") == BOARD_MAPPING_SHA

    print("=== X30/X31 identity ===", flush=True)
    rows, labels, identity = reproduce_population()
    ab = ab_identity(rows, labels, identity)
    assert ab["ok"], ab
    x31 = _x31_identity()
    assert x31["ok"], x31
    print(f"  pop={identity['population_n']} valid={identity['valid_n']} x31={x31['run_id']}", flush=True)

    funnel = freeze_canonical_funnel()
    print("=== coverage by day ===", flush=True)
    coverage = coverage_by_day(rows)
    for c in coverage:
        print(
            f"  {c['date']}: cap={c['captured_symbol_count']} uni={c['universe_csv_symbol_count']} "
            f"cand={c['candidate_symbol_count']} cap-uni={c['capture_minus_universe']}",
            flush=True,
        )

    print("=== stage clock evaluation ===", flush=True)
    stage_pack = build_stage_evals(cand_rows=rows, cand_labels=labels)
    for sid, sm in stage_pack["summaries"].items():
        print(f"  {sid}: n={sm['episodes']} ret300={sm.get('ret300')} ret600={sm.get('ret600')}", flush=True)

    print("=== attribution ===", flush=True)
    attr = run_attribution(stage_pack)
    effects = classify_effects(attr["transitions"], attr["symbol_vs_timing"])
    decision = decide_verdict(
        coverage=coverage,
        trans=attr["transitions"],
        effects=effects,
    )
    decision = refine_verdict_for_coverage(
        decision, attr["summaries"], coverage, attr["transitions"]
    )
    cul = decision.get("primary_culprit_transition") or ""
    if "CAPTURED_MARKET_PROXY→RUNTIME_UNIVERSE" in cul:
        d300 = (attr["transitions"].get(cul) or {}).get("delta300")
        if d300 is not None and d300 > 0:
            # universe better than capture → capture/coverage is the problem, not universe filter
            from . import VERDICT_COVERAGE
            decision["verdict"] = VERDICT_COVERAGE
            decision["recommended_next_phase"] = "X33_DATA_SOURCE_COVERAGE_REDESIGN"
            decision["primary_culprit_transition"] = (
                "CAPTURED_MARKET_PROXY (capture worse; universe filter helps or neutral)"
            )

    chars = selection_characteristics(
        stage_evals=stage_pack["evals"],
        cand_rows=rows,
        coverage=coverage,
    )

    # Compact transition summary for report
    transitions_summary = {
        k: {
            "delta300": v.get("delta300"),
            "delta600": v.get("delta600"),
            "negative_days_300": v.get("negative_days_300"),
            "negative_days_600": v.get("negative_days_600"),
            "attribution_strength": v.get("attribution_strength"),
            "parent_ret300": v.get("parent_ret300"),
            "child_ret300": v.get("child_ret300"),
        }
        for k, v in attr["transitions"].items()
    }
    largest = None
    if attr["transitions"]:
        largest = min(
            attr["transitions"].items(),
            key=lambda kv: (kv[1].get("delta300") if kv[1].get("delta300") is not None else 0),
        )
        largest = {"transition": largest[0], **transitions_summary[largest[0]]}

    interim = {
        "run_id": run_id,
        "analysis_id": ANALYSIS_ID,
        "source_x30_run_id": SOURCE_X30_RUN,
        "source_x31_run_id": SOURCE_X31_RUN,
        "population_n": identity["population_n"],
        "valid_n": identity["valid_n"],
        "opened_20260810": False,
        "sampling_seed": SAMPLING_SEED,
        "no_runtime_change": True,
        "no_entry_redesign": True,
        "no_exit": True,
        "no_short": True,
        "canonical_funnel_n": len(funnel),
        "verdict_preview": decision["verdict"],
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    print("=== tests ===", flush=True)
    tests = _run_tests()

    decomp = attr["symbol_vs_timing"]
    report = {
        **interim,
        "document_id": DOCUMENT_ID,
        "verdict": decision["verdict"],
        "primary_culprit_transition": decision.get("primary_culprit_transition"),
        "recommended_next_phase": decision.get("recommended_next_phase"),
        "board_mapping_sha": BOARD_MAPPING_SHA,
        "historical_days": list(HISTORICAL_DAYS),
        "forbidden_from": FORBIDDEN_FROM,
        "identity": identity,
        "x31_identity": x31,
        "ab_determinism": ab,
        "canonical_funnel": funnel,
        "attribution_stage_ids": [a for a, _ in transitions()] + ["CANDIDATE_CLUSTER_ANCHORS"],
        "source_coverage_by_day": coverage,
        "stage_summaries": attr["summaries"],
        "transitions": attr["transitions"],
        "transitions_summary": transitions_summary,
        "matched_parent": attr["matched_parent"],
        "largest_negative_transition": largest,
        "symbol_selection_delta_300": decomp.get("symbol_selection_delta_300"),
        "symbol_selection_delta_600": decomp.get("symbol_selection_delta_600"),
        "timing_delta_300": decomp.get("timing_delta_300"),
        "timing_delta_600": decomp.get("timing_delta_600"),
        "symbol_vs_timing": decomp,
        "effects": effects,
        "loso": attr["loso"],
        "selection_characteristics": chars,
        "decision": decision,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "must_be_false_20260810": True,
        "tests": tests,
        "safety": {
            "submit_cancel_live": "0/0/0",
            "paper_only": True,
            "no_runtime_universe_entry_exit_change": True,
            "no_short_implementation": True,
            "no_margin_path": True,
            "no_discord_production": True,
        },
        "artifacts": ["report.json", "report.md", "audit.xlsx"],
    }

    # slim transitions days in report already included — ok for json size

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "culprit": decision.get("primary_culprit_transition"),
            "next": decision.get("recommended_next_phase"),
            "opened_20260810": False,
        }],
        "funnel": funnel,
        "coverage": coverage,
        "stages": [
            {"stage": k, **{kk: vv for kk, vv in v.items() if not isinstance(vv, (dict, list))}}
            for k, v in attr["summaries"].items()
        ],
        "transitions": [
            {"transition": k, **{kk: vv for kk, vv in v.items() if kk != "days"}}
            for k, v in transitions_summary.items()
        ],
        "day_trans": _flatten_days(attr["transitions"]),
    }
    publish(OUT, report, sheets)
    print(f"=== DONE verdict={decision['verdict']} ===", flush=True)
    return report


def _flatten_days(trans: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for k, v in trans.items():
        for d in v.get("days") or []:
            rows.append({"transition": k, **d})
    return rows or [{"transition": None}]


if __name__ == "__main__":
    main()
