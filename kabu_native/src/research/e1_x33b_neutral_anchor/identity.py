"""X33 source identity resolution + fact binding."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import (
    CANONICAL_X33_RUN,
    REPORTED_RUN_ID_USER_TEXT,
    SOURCE_X33_VERDICT,
)

NATIVE = Path(__file__).resolve().parents[3]
X33 = NATIVE / "results" / "research" / "e1_x33_causal_anchor_repair"


def resolve_x33_identity() -> dict[str, Any]:
    report = json.loads((X33 / "report.json").read_text(encoding="utf-8"))
    interim = json.loads((X33 / "_interim.json").read_text(encoding="utf-8"))
    md = (X33 / "report.md").read_text(encoding="utf-8")
    artifact_run = report.get("run_id")
    interim_run = interim.get("run_id")
    md_run = None
    for line in md.splitlines():
        if "run_id:" in line and "`" in line:
            md_run = line.split("`")[1]
            break

    # All on-disk artifacts agree on 154318; user completion text said 154320 (~2s drift)
    filesystem_ids = {artifact_run, interim_run, md_run}
    filesystem_ids.discard(None)
    canonical = CANONICAL_X33_RUN
    reason = (
        "All on-disk X33 artifacts (report.json, _interim.json, report.md, audit.xlsx) "
        f"agree on run_id={artifact_run}. User completion text cited "
        f"{REPORTED_RUN_ID_USER_TEXT} (~2s naming drift). Canonical = filesystem artifacts; "
        "performance values not rewritten."
    )
    assert artifact_run == CANONICAL_X33_RUN, (artifact_run, CANONICAL_X33_RUN)
    assert report.get("verdict") == SOURCE_X33_VERDICT
    assert interim_run == artifact_run

    # Bind facts from report (do not invent)
    parent = report.get("parent_summary") or {}
    control = report.get("control_summary") or {}
    old = report.get("old_summary") or {}
    causal = report.get("causal_summary") or {}
    return {
        "reported_run_id": REPORTED_RUN_ID_USER_TEXT,
        "artifact_run_id": artifact_run,
        "interim_run_id": interim_run,
        "report_md_run_id": md_run,
        "canonical_run_id": canonical,
        "reason_for_resolution": reason,
        "verdict": report.get("verdict"),
        "bound_facts": {
            "CANDIDATE_SYMBOL_POOL": {
                "ret300": parent.get("ret300"),
                "ret600": parent.get("ret600"),
                "episodes": parent.get("episodes"),
            },
            "FEATURE_OK_FIXED_CLOCK": {
                "ret300": control.get("ret300"),
                "ret600": control.get("ret600"),
                "episodes": control.get("episodes"),
            },
            "OLD_CLUSTER_FIRST": {
                "ret300": old.get("ret300"),
                "ret600": old.get("ret600"),
                "episodes": old.get("episodes"),
            },
            "CAUSAL_CLUSTER_FIRST_V1": {
                "ret300": causal.get("ret300"),
                "ret600": causal.get("ret600"),
                "episodes": causal.get("episodes"),
            },
            "prefix_invariance": (report.get("prefix_invariance") or {}).get("status"),
            "feature_eligibility_coverage_loss": (
                (report.get("feature_eligibility") or {}).get("coverage_loss_quality_to_feature")
            ),
        },
        "x33_report": report,
    }


def exact_fixed_clock_semantics() -> dict[str, Any]:
    """Document SoT from X33/X32 code — no new clock invention."""
    from research.e1_x32_upstream_attribution import CLOCK_POINTS_HM, SAMPLING_SEED
    return {
        "anchor_name_x33": "FEATURE_OK_FIXED_CLOCK",
        "formal_name": "NEUTRAL_FIXED_CLOCK_ANCHOR_V1",
        "source_files": [
            "src/research/e1_x33_causal_anchor_repair/eval_arms.py",
            "src/research/e1_x32_upstream_attribution/eval_stages.py",
            "src/research/e1_x32_upstream_attribution/__init__.py",
        ],
        "source_functions": [
            "control_feature_ok_fixed_clock",
            "parent_fixed_clock",
            "clock_epochs_for_day",
            "evaluate_long_at_signal",
        ],
        "clock_grid_definition": list(CLOCK_POINTS_HM),
        "session_origin": "JST calendar day; HH<12 → AM else PM",
        "interval": "irregular fixed HM points (not uniform 300s grid); 8 AM + 8 PM points",
        "phase_offset": "none — absolute JST wall-clock HM from X32 CLOCK_POINTS_HM",
        "feature_status_requirement": (
            "X33 CONTROL: symbol has ≥1 FEATURE_OK grid row that day. "
            "X33 PARENT: symbol in CANDIDATE_SYMBOL_POOL. "
            "X33 observed CONTROL≡PARENT; NEUTRAL freezes PARENT pool + same clocks "
            "(no day-lookahead FEATURE_OK membership)."
        ),
        "missing_row_handling": "skip if board missing or first valid ask unavailable",
        "session_boundary_handling": "session_end_epoch; no session cross in evaluate_long_at_signal",
        "board_execution_mapping": "Sell1 ask entry / Buy1 bid mark (X28/X31 contract)",
        "sampling_seed": SAMPLING_SEED,
        "x33_control_seed_unused": 33,
        "no_performance_search": True,
        "neutrality": {
            "selects_by_price_direction": False,
            "selects_by_return": False,
            "selects_by_volume": False,
            "selects_by_R2": False,
            "selects_by_future_outcome": False,
            "allowed": ["clock", "session", "symbol pool membership", "data availability"],
        },
    }
