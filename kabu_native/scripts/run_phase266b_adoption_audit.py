#!/usr/bin/env python3
"""
Phase266b: Inventory Phase230+ adoption candidates vs implementation (review only).

Output: kabu_native/results/reports/phase266b_adoption_audit.json
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"
OUT = REPORTS / "phase266b_adoption_audit.json"
SRC = REPO / "kabu_native" / "src"
CONFIGS = REPO / "kabu_native" / "configs"
SCRIPTS = REPO / "kabu_native" / "scripts"

PHASE_MIN = 230


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _phase_num(name: str) -> Optional[int]:
    m = re.search(r"phase(\d+)", name)
    return int(m.group(1)) if m else None


def _grep_file(path: Path, pattern: str) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE))
    except OSError:
        return False


def _code_snapshot() -> dict[str, Any]:
    prf = SRC / "universe" / "price_risk_filter.py"
    score = SRC / "small_paper" / "entry_expectancy_score_shadow.py"
    gate = SRC / "research" / "exposure_gate.py"
    pilot = SRC / "small_paper" / "pilot_runner.py"
    runner = SCRIPTS / "run_core10_dynamic40_am_pm_daily_runner.py"
    am_runner = SRC / "runner" / "am_pm_daily_runner.py"
    pilot_yaml = CONFIGS / "small_paper_pilot.yaml"
    q070_yaml = CONFIGS / "small_paper_pilot_q070_cap3.yaml"

    min_close = None
    rolling_mid = None
    if prf.is_file():
        m = re.search(r"MIN_CLOSE_PRICE\s*=\s*([\d.]+)", prf.read_text(encoding="utf-8"))
        min_close = float(m.group(1)) if m else None
    if score.is_file():
        m = re.search(r'"RollingMAE:mid":\s*(\d+)', score.read_text(encoding="utf-8"))
        rolling_mid = int(m.group(1)) if m else None

    yaml_quality: dict[str, float] = {}
    for p in (pilot_yaml, q070_yaml):
        if p.is_file():
            m = re.search(r"min_continuation_quality:\s*([\d.]+)", p.read_text(encoding="utf-8"))
            if m:
                yaml_quality[p.name] = float(m.group(1))

    return {
        "MIN_CLOSE_PRICE": min_close,
        "SCORE_POINTS_RollingMAE_mid": rolling_mid,
        "exposure_gate_has_low_quality_reject": _grep_file(gate, r'REJECT_LOW_QUALITY'),
        "pilot_rejects_on_entry_score_v2": _grep_file(
            pilot, r"entry_expectancy_score_v2.*reject|reject.*entry_expectancy_score_v2"
        ),
        "pilot_open_syms_before_refresh_emit": _grep_file(
            pilot, r"open_syms = observer\.open_symbols\(\).*_emit_intraday_refresh"
        )
        or (
            _grep_file(pilot, r"open_syms = observer\.open_symbols\(\)")
            and _grep_file(pilot, r"_emit_intraday_refresh_event")
        ),
        "intraday_refresh_merge_cap50": _grep_file(
            SRC / "universe" / "intraday_refresh.py", r"def merge_universe_with_open_symbols"
        ),
        "daily_runner_default_universe_mode": (
            re.search(
                r'UNIVERSE_MODE_DEFAULT\s*=\s*"([^"]+)"',
                am_runner.read_text(encoding="utf-8"),
            ).group(1)
            if am_runner.is_file()
            else None
        ),
        "yaml_min_continuation_quality": yaml_quality,
    }


def _extract_report_signals(data: dict[str, Any], phase: str, fname: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []

    def add(kind: str, detail: Any, path: str = "") -> None:
        signals.append({"kind": kind, "path": path, "detail": detail})

    if data.get("adoption_candidate") is True:
        add("adoption_candidate_true", data.get("decision") or True, "adoption_candidate")
    if data.get("adoption_candidate") is False:
        add("adoption_candidate_false", data.get("adoption_status"), "adoption_candidate")

    dec = data.get("adoption_decision") or {}
    if isinstance(dec, dict) and dec.get("verdict"):
        add("adoption_decision", dec.get("verdict"), "adoption_decision.verdict")

    npr = data.get("next_phase_recommendation") or {}
    if isinstance(npr, dict) and npr.get("action"):
        add("next_phase_recommendation", npr, "next_phase_recommendation")

    if data.get("executive_summary"):
        add("executive_summary", data["executive_summary"], "executive_summary")

    if data.get("recommended_rollout_sequence"):
        add("recommended_rollout_sequence", len(data["recommended_rollout_sequence"]), "recommended_rollout_sequence")

    if data.get("adoption_candidates"):
        add("adoption_candidates", [x.get("id") for x in data["adoption_candidates"]], "adoption_candidates")

    impl = data.get("implementation_status") or data.get("status")
    if impl in ("complete", "fixed", "deployed"):
        add("implementation_report", impl, "implementation_status")

    rec = data.get("recommendation")
    if isinstance(rec, dict) and rec.get("best_priority_factor"):
        add("recommendation", rec.get("best_priority_factor"), "recommendation")

    if isinstance(data.get("verdict"), str) and "reject" in data["verdict"]:
        add("negative_verdict", data["verdict"], "verdict")

    summ = data.get("summary") or {}
    if isinstance(summ, dict):
        if summ.get("best_score_ge5_pf_scenario"):
            add(
                "best_scenario",
                summ["best_score_ge5_pf_scenario"],
                "summary.best_score_ge5_pf_scenario",
            )

    constraints = data.get("constraints") or {}
    if constraints.get("review_only") or constraints.get("production_change_forbidden"):
        add("review_only", True, "constraints")

    return signals


# Curated inventory (cross-checked with code_snapshot).
ITEMS: list[dict[str, Any]] = [
    {
        "id": "universe_close_min_300",
        "title": "Universe dynamic40 — exclude close < 300 JPY",
        "evidence_phases": [251, 252, 253, 254, 255, 249, 250],
        "source_reports": [
            "phase254_price_floor_adoption_review.json",
            "phase249_implementation_plan.json",
            "phase250_implementation_report.json",
        ],
        "signal": "Phase249/254 review → Phase250 MIN_CLOSE_PRICE=300",
        "category": "2_implemented",
        "code_refs": ["kabu_native/src/universe/price_risk_filter.py"],
    },
    {
        "id": "entry_score_v1_rollingmae_mid_zero",
        "title": "Entry score v1 — RollingMAE:mid +0 (align with v2 / Phase236 B)",
        "evidence_phases": [236, 237, 238, 249, 250],
        "source_reports": [
            "phase236_entry_score_counterfactual_repair.json",
            "phase250_implementation_report.json",
        ],
        "signal": "scenario_B_rollingmae_zero; Phase250 SCORE_POINTS mid 2→0",
        "category": "2_implemented",
        "code_refs": ["kabu_native/src/small_paper/entry_expectancy_score_shadow.py"],
        "notes": "Logging score changes; not an ENTRY hard reject by itself.",
    },
    {
        "id": "phase250a_intraday_refresh_crash",
        "title": "Intraday refresh — open_syms before emit (UnboundLocalError fix)",
        "evidence_phases": ["250a"],
        "source_reports": ["phase250a_intraday_refresh_crash_fix.json"],
        "signal": "implementation_status=fixed",
        "category": "2_implemented",
        "code_refs": ["kabu_native/src/small_paper/pilot_runner.py"],
    },
    {
        "id": "phase242b_intraday_refresh_merge_cap50",
        "title": "Intraday refresh — merge open symbols with cap-50 register",
        "evidence_phases": ["242b"],
        "source_reports": ["phase242b_intraday_refresh_fix_report.json"],
        "signal": "fix in universe.intraday_refresh.merge_universe_with_open_symbols",
        "category": "2_implemented",
        "code_refs": ["kabu_native/src/universe/intraday_refresh.py"],
    },
    {
        "id": "entry_expectancy_score_shadow_logging",
        "title": "Entry expectancy score v1/v2 — shadow logging on accept",
        "evidence_phases": [230, 233, 237],
        "source_reports": [
            "phase230_entry_expectancy_shadow_observation.json",
            "phase233_entry_expectancy_shadow_validation.json",
        ],
        "signal": "hard_reject_forbidden; shadow fields in pilot_runner",
        "category": "3_shadow_only",
        "code_refs": [
            "kabu_native/src/small_paper/entry_expectancy_score_shadow.py",
            "kabu_native/src/small_paper/pilot_runner.py",
        ],
    },
    {
        "id": "entry_score_v2_ge5_hard_gate",
        "title": "ENTRY hard reject — entry_expectancy_score_v2 >= 5",
        "evidence_phases": [238, 239, 248, 249],
        "source_reports": [
            "phase248_v2_adoption_decision.json",
            "phase249_implementation_plan.json",
            "phase239_entry_score_ge5_gate_system_comparison.json",
        ],
        "signal": "adoption_candidate=true (248); Phase249 step 5 optional",
        "category": "4_not_implemented",
        "code_refs": [],
        "notes": "Phase250 explicitly left v2_ge5_reject=false.",
    },
    {
        "id": "replace_quality_with_entry_score_v2_ge4",
        "title": "Replace quality>=0.70 gate with entry_score_v2>=4",
        "evidence_phases": [265, 266],
        "source_reports": [
            "phase266_quality_replacement_score_gate.json",
            "phase265_entry_quality_concentration_window.json",
        ],
        "signal": "adopt_entry_score_v2_gate_next_phase; primary_candidate=3_score_v2_ge4",
        "category": "4_not_implemented",
        "code_refs": [],
    },
    {
        "id": "entry_score_v2_ge5_strict_tier",
        "title": "Optional strict tier — entry_score_v2>=5 without quality",
        "evidence_phases": [266, 248],
        "source_reports": ["phase266_quality_replacement_score_gate.json"],
        "signal": "strict_candidate; combined PF>1 but low live volume",
        "category": "4_not_implemented",
        "code_refs": [],
        "notes": "Secondary to ge4; Phase266 executive_summary.",
    },
    {
        "id": "max_concurrent_v2_priority_order",
        "title": "max_concurrent admission — prioritize v2>=5 within event_time group",
        "evidence_phases": [246],
        "source_reports": ["phase246_v2_priority_simulation.json"],
        "signal": "B_v2_priority marginal PF uplift vs A; review only",
        "category": "4_not_implemented",
        "code_refs": [],
        "notes": "Not a explicit adoption_candidate flag; weak production signal.",
    },
    {
        "id": "max_concurrent_entry_score_priority",
        "title": "max_concurrent — prioritize by entry_score at reject instant",
        "evidence_phases": [260],
        "source_reports": ["phase260_priority_analysis.json"],
        "signal": "recommendation.best_priority_factor=entry_score",
        "category": "4_not_implemented",
        "code_refs": [],
    },
    {
        "id": "phase231_score_ge5_feature_clusters",
        "title": "Phase231 discovered score>=5 feature clusters as ENTRY rules",
        "evidence_phases": [231, 232],
        "source_reports": [
            "phase231_score_cohort_expectancy_discovery.json",
            "phase232_entry_feature_leak_audit.json",
        ],
        "signal": "adopted_count=18 clusters; leak audit invalidated future-leak combos",
        "category": "4_not_implemented",
        "code_refs": [],
        "notes": "Discovery artifact; no production promote flag.",
    },
    {
        "id": "universe_price_risk_daily_runner_default",
        "title": "Operational daily runner — default universe-mode uses price-risk filter",
        "evidence_phases": [249, 250, 254],
        "source_reports": ["phase249_implementation_plan.json"],
        "signal": "recommended_rollout_sequence step 2; dry-run pass in Phase250",
        "category": "7_implemented_code_command_not_reflected",
        "code_refs": [
            "kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py",
            "kabu_native/src/runner/am_pm_daily_runner.py",
        ],
        "notes": "MIN_CLOSE=300 in code but CLI default remains core10-dynamic40.",
    },
    {
        "id": "entry_score_v2_gate_exposure",
        "title": "Score v2 threshold wired into ExposureGate / pilot accept path",
        "evidence_phases": [248, 249, 266, 250],
        "source_reports": [
            "phase250_implementation_report.json",
            "phase266_quality_replacement_score_gate.json",
        ],
        "signal": "Score definition deployed (250) but reject_entry not added",
        "category": "6_implemented_code_gate_not_reflected",
        "code_refs": [
            "kabu_native/src/research/exposure_gate.py",
            "kabu_native/src/small_paper/pilot_runner.py",
        ],
        "notes": "Still rejects via min_continuation_quality / low_quality only.",
    },
    {
        "id": "quality_gate_q070_shadow_yaml",
        "title": "Shadow pilot YAMLs — min_continuation_quality 0.70 unchanged",
        "evidence_phases": [266, 249],
        "source_reports": ["phase266_quality_replacement_score_gate.json"],
        "signal": "Phase266 targets replacing q070 0.70 gate; not updated",
        "category": "5_implemented_code_yaml_not_reflected",
        "code_refs": [
            "kabu_native/configs/small_paper_pilot_q070_cap3.yaml",
            "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml",
        ],
        "notes": "small_paper_pilot.yaml remains 0.55 (separate production path).",
    },
    {
        "id": "phase230_formal_adoption_ge5",
        "title": "Phase230 formal adoption — score>=5 PF/OOS criteria",
        "evidence_phases": [230],
        "source_reports": ["phase230_entry_expectancy_shadow_observation.json"],
        "signal": "adoption_candidate=false; collecting_sessions",
        "category": "4_not_implemented",
        "code_refs": [],
        "notes": "Superseded by 236–248 evidence; never flipped adoption_candidate.",
    },
]

NEGATIVE_OR_REVIEW_ONLY: list[dict[str, Any]] = [
    {
        "id": "phase264_slot_replacement",
        "verdict": "rejected_not_consistently_better_than_occupiers",
        "phase": 264,
    },
    {
        "id": "phase262_slow_sideways_hogging",
        "verdict": "weak_support_for_slow_sideways_slot_hogging_at_mc_instant",
        "phase": 262,
    },
    {
        "id": "phase241_245_mc_reviews",
        "note": "max_concurrent counterfactual / fast validation — no production_candidate flag",
        "phases": [241, 242, 243, 244, 245],
    },
]


def _classify_with_code(items: list[dict[str, Any]], snap: dict[str, Any]) -> None:
    """Refine categories using live code checks."""
    if snap.get("MIN_CLOSE_PRICE") == 300.0:
        for it in items:
            if it["id"] == "universe_close_min_300" and it["category"] == "2_implemented":
                it["verified_in_code"] = True
    if snap.get("SCORE_POINTS_RollingMAE_mid") == 0:
        for it in items:
            if it["id"] == "entry_score_v1_rollingmae_mid_zero":
                it["verified_in_code"] = True
    if not snap.get("pilot_rejects_on_entry_score_v2"):
        for it in items:
            if it["id"] in (
                "entry_score_v2_ge5_hard_gate",
                "replace_quality_with_entry_score_v2_ge4",
                "entry_score_v2_gate_exposure",
            ):
                it["verified_absent_in_gate"] = True
    if snap.get("daily_runner_default_universe_mode") == "core10-dynamic40":
        for it in items:
            if it["id"] == "universe_price_risk_daily_runner_default":
                it["verified_default_mode"] = snap["daily_runner_default_universe_mode"]


def _per_phase_index(reports: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in reports:
        pn = _phase_num(p.name)
        if pn is None or pn < PHASE_MIN:
            continue
        data = _read_json(p) or {}
        rows.append(
            {
                "phase": pn,
                "file": p.name,
                "review_only": (data.get("constraints") or {}).get("review_only"),
                "implementation_status": data.get("implementation_status") or data.get("status"),
                "signals": _extract_report_signals(data, str(pn), p.name),
            }
        )
    return sorted(rows, key=lambda r: (r["phase"], r["file"]))


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    snap = _code_snapshot()
    items = [dict(x) for x in ITEMS]
    _classify_with_code(items, snap)

    reports = sorted(REPORTS.glob("phase*.json"))
    per_phase = _per_phase_index(reports)

    ADOPTION_CANDIDATE_IDS = {
        "universe_close_min_300",
        "entry_score_v1_rollingmae_mid_zero",
        "entry_score_v2_ge5_hard_gate",
        "replace_quality_with_entry_score_v2_ge4",
        "entry_score_v2_ge5_strict_tier",
        "universe_price_risk_daily_runner_default",
        "entry_score_v2_gate_exposure",
        "quality_gate_q070_shadow_yaml",
    }

    cats: dict[str, list[dict[str, Any]]] = {f"{i}_{k}": [] for i, k in [
        (1, "adoption_candidates"),
        (2, "implemented"),
        (3, "shadow_only"),
        (4, "not_implemented"),
        (5, "implemented_code_yaml_not_reflected"),
        (6, "implemented_code_gate_not_reflected"),
        (7, "implemented_code_command_not_reflected"),
    ]}

    for it in items:
        primary = it.pop("category")
        row = dict(it)
        cats[primary].append(row)
        if row["id"] in ADOPTION_CANDIDATE_IDS:
            cats["1_adoption_candidates"].append({**row, "listed_in": primary})

    unimplemented_candidate_ids = [
        x["id"]
        for x in cats["4_not_implemented"]
        if x["id"] in ADOPTION_CANDIDATE_IDS
    ]

    report = {
        "phase": "266b",
        "mode": "adoption_inventory_audit",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {"review_only": True, "implementation_applied": False},
        "scope": {
            "phase_min": PHASE_MIN,
            "reports_dir": str(REPORTS.relative_to(REPO)).replace("\\", "/"),
            "reports_scanned": len([p for p in reports if (_phase_num(p.name) or 0) >= PHASE_MIN]),
        },
        "detection_patterns": [
            "adoption_candidate",
            "adoption_decision",
            "adoption_candidates",
            "recommended_rollout_sequence",
            "next_phase_recommendation",
            "executive_summary",
            "recommendation",
            "implementation_status",
            "verdict (negative)",
        ],
        "code_snapshot": snap,
        "categories": cats,
        "adoption_candidates_summary": {
            "open_production_items": unimplemented_candidate_ids,
            "primary_next_phase": "replace_quality_with_entry_score_v2_ge4",
            "secondary_next_phase": "entry_score_v2_ge5_hard_gate",
            "operational_gap": "universe_price_risk_daily_runner_default",
        },
        "negative_or_review_only": NEGATIVE_OR_REVIEW_ONLY,
        "per_phase_index": per_phase,
        "gaps_count": {name: len(entries) for name, entries in cats.items()},
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    for name, entries in cats.items():
        print(f"  {name}: {len(entries)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
