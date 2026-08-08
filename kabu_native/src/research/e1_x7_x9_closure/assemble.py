"""Assemble E1_X7–X9 closure from frozen source reports — no new computation."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_file, sha256_obj

from . import (
    ANALYSIS_ID,
    DOCUMENT_ID,
    EXPECTED_SOURCES,
    FINAL_VERDICT,
    REQUIRED_VERDICT_CHECKS,
    SUPERSEDED_SOURCES,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
RESEARCH = NATIVE / "results" / "research"
PUBLISH = RESEARCH / "e1_x7_x9_closure"


def _load_source(meta: dict[str, Any]) -> dict[str, Any]:
    d = RESEARCH / meta["dir"]
    jp = d / "report.json"
    xp = d / "audit.xlsx"
    if not jp.exists():
        raise FileNotFoundError(f"missing report: {jp}")
    report = json.loads(jp.read_text(encoding="utf-8"))
    return {
        "key": None,
        "run_id": report.get("run_id"),
        "analysis_id": report.get("analysis_id"),
        "verdict": report.get("verdict"),
        "generated_at_jst": report.get("generated_at_jst") or report.get("generated_at"),
        "report_sha": sha256_file(jp),
        "audit_sha": sha256_file(xp) if xp.exists() else None,
        "dir": str(d),
        "expected_run_id": meta["run_id"],
        "expected_verdict": meta["expected_verdict"],
        "canonical": meta["canonical"],
        "role": meta["role"],
        "superseded": meta.get("superseded", False),
        "superseded_by": meta.get("superseded_by"),
        "supersede_reason": meta.get("reason"),
        "run_id_match": str(report.get("run_id")) == meta["run_id"],
        "verdict_match": str(report.get("verdict")) == meta["expected_verdict"],
    }


def _safety() -> dict[str, Any]:
    return {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_yaml_changed": False,
        "entry_changed": False,
        "exit_changed": False,
        "universe_changed": False,
        "unused_data_used": False,
        "prospective": False,
        "shadow": False,
        "forward": False,
        "paper": False,
        "discord": False,
        "pfq_revived": False,
        "new_candidate": False,
        "new_computation": False,
    }


def assemble(*, label: str = "A") -> dict[str, Any]:
    run_id = f"e1x7x9_closure_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{label}"

    sources: dict[str, dict[str, Any]] = {}
    for key, meta in EXPECTED_SOURCES.items():
        row = _load_source(meta)
        row["key"] = key
        sources[key] = row

    superseded: dict[str, dict[str, Any]] = {}
    for key, meta in SUPERSEDED_SOURCES.items():
        row = _load_source(meta)
        row["key"] = key
        superseded[key] = row

    # Exact verdict gate (section 2)
    mismatches = []
    for key, expected in REQUIRED_VERDICT_CHECKS.items():
        got = sources[key]["verdict"]
        rid_ok = sources[key]["run_id_match"]
        if got != expected or not rid_ok:
            mismatches.append({
                "key": key,
                "expected_verdict": expected,
                "got_verdict": got,
                "expected_run_id": sources[key]["expected_run_id"],
                "got_run_id": sources[key]["run_id"],
                "run_id_match": rid_ok,
            })
    if mismatches:
        return {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "label": label,
            "verdict": "E1_X7_X9_CLOSURE_SOURCE_IDENTITY_MISMATCH",
            "mismatches": mismatches,
            "stop": True,
            "determinism_shas": {"verdict": "E1_X7_X9_CLOSURE_SOURCE_IDENTITY_MISMATCH"},
            "safety": _safety(),
            "_sheets": {},
        }

    # Also verify superseded identity (history only)
    for key, row in superseded.items():
        if not row["run_id_match"] or not row["verdict_match"]:
            mismatches.append({
                "key": key,
                "expected_verdict": row["expected_verdict"],
                "got_verdict": row["verdict"],
                "expected_run_id": row["expected_run_id"],
                "got_run_id": row["run_id"],
                "note": "superseded identity check",
            })
    if mismatches:
        return {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "label": label,
            "verdict": "E1_X7_X9_CLOSURE_SOURCE_IDENTITY_MISMATCH",
            "mismatches": mismatches,
            "stop": True,
            "determinism_shas": {"verdict": "E1_X7_X9_CLOSURE_SOURCE_IDENTITY_MISMATCH"},
            "safety": _safety(),
            "_sheets": {},
        }

    # --- Integrated findings (quoted from frozen conclusions; no recompute) ---
    pfq_entry = {
        "candidate": "PFQ_UPDATE_Q70",
        "entry_path": "fixed-grid first-touch ENTRY path signal supported",
        "plus5_vs_minus10_difference_approx": 0.143,
        "ci95_approx": [0.075, 0.219],
        "positive_direction_days": "8/9",
        "survives_ex_285A": True,
        "not_event_density_proxy_only": True,
        "source": sources["bridge_v2"]["run_id"],
    }
    pfq_flow_joint = {
        "PFQ_FLOW_Q30": "ENTRY path support none",
        "PFQ_JOINT": "support 41 < 50; economic pair not executed",
        "source": sources["bridge_v2"]["run_id"],
    }
    pfq_economics = {
        "existing_4_pairs": "all rejected",
        "all_pairs_pnl": "< 0",
        "all_pairs_pf": "< 1",
        "day_stability": "none",
        "source": sources["bridge_v2"]["run_id"],
    }
    pfq_exit = {
        "sole_revision_baseline": "PFQ_UPDATE_Q70 | PFQ_X_PROGRESS_STRUCT",
        "primary_failure": "PLUS5_REACHED_BEFORE_EXIT_GIVEN_BACK_TO_NONPOSITIVE",
        "single_revision": "PFQ_X_PROGRESS_BE5_FLOOR0",
        "baseline_pnl": -23623.65,
        "baseline_pf": 0.917,
        "revision_pnl": -37223.65,
        "revision_pf": 0.846,
        "mechanism": {
            "original_giveback": 31,
            "prevented": 11,
            "required_prevented": 16,
            "floor_triggered": 16,
            "gap_through": 16,
            "positive_to_nonpositive": 5,
            "required_side_effect": 0,
        },
        "formal_status": "PFQ_CURRENT_LINE_CLOSED_REJECTED",
        "closure_reason": "EXIT_REVISION_DID_NOT_FIX_TARGET_FAILURE",
        "no_further_arm_floor_trail_time_trials": True,
        "sources": [
            sources["exit_gate_v2"]["run_id"],
            sources["exit_revision"]["run_id"],
        ],
    }
    kioxia = {
        "threshold_leverage": {
            "ex_285A_update_q70": "8 → 7",
            "ex_285A_flow_q30": "0.79917 → 0.82299",
            "update_influence_rank": 1,
            "flow_influence_rank": 1,
            "size_matched_percentile_update": "100%",
            "size_matched_percentile_flow": "100%",
            "update_cross_symbol_membership_flip_approx": "5.65%",
            "conclusion": "285A strongly moved cross-symbol raw thresholds",
        },
        "signal_dependence": {
            "frozen_threshold": 8,
            "ex_285A_support": True,
            "loso_support_preserved": "65/65",
            "conclusion": "ENTRY path signal does not depend on 285A alone",
        },
        "economic_dependence": {
            "baseline_ex_285A_pnl": -83411.65,
            "revision_ex_285A_pnl": -133011.65,
            "top_trade_dependence": "strong",
            "interpretation": (
                "285A is not the sole source of ENTRY signal; "
                "but strongly distorted raw threshold, candidate concentration, and economic result"
            ),
        },
        "source": sources["symbol_leverage"]["run_id"],
    }
    universe = {
        "evaluable_proxies": ["market_segment", "index_status", "turnover_20d"],
        "not_evaluable": [
            "as-of market cap",
            "direct institutional ownership",
            "foreign ownership",
            "free-float",
        ],
        "coverage": {
            "market_index_approx": "98%",
            "turnover_symbol_approx": "95%",
            "turnover_episode_approx": "91%",
            "market_cap": "0%",
            "direct_ownership": "0%",
        },
        "turnover_first_touch_plus5_vs_minus10": {
            "LOW": 0.152,
            "MID": 0.237,
            "HIGH": 0.274,
        },
        "low_turnover_advantage": False,
        "forbidden_claims": [
            "low institutional ownership is disadvantageous",
            "high institutional ownership is advantageous",
        ],
        "formal_statement": (
            "On available proxy axes, no evidence to adopt a low-institutional / "
            "low-large-capital-participation Universe"
        ),
        "direct_ownership_status": "DIRECT_INSTITUTIONAL_DATA_NOT_EVALUABLE",
        "source": sources["universe_regime"]["run_id"],
    }
    update_heavy = {
        "status": "DESCRIPTIVE_NEAR_SIGNAL_NOT_PROMOTED",
        "UPDATE_HEAVY_plus5_vs_minus10": 0.424,
        "UPDATE_LIGHT_plus5_vs_minus10": 0.218,
        "difference_approx": 0.206,
        "ci95_lower_approx": -0.003,
        "positive_days": "6/8",
        "precommitted_gate_passed": False,
        "promoted_to_new_family": False,
        "source": sources["universe_regime"]["run_id"],
    }
    within_symbol = {
        "raw_ge8_plus5_before_minus10": 0.432,
        "within_symbol_p70_plus5_before_minus10": 0.341,
        "within_relative_superior_to_raw": False,
        "raw_cross_symbol_distortion_confirmed": True,
        "new_normalization_threshold_created": False,
        "future_design_may_consider": [
            "within-symbol normalization",
            "liquidity/update regime split",
            "raw vs relative precommit comparison",
        ],
        "source": sources["universe_regime"]["run_id"],
    }

    final_statuses = {
        "E1_X7_PFQ": {
            "status": "CLOSED_REJECTED",
            "robust_entry_exit_pair": None,
            "frozen_candidate": None,
            "prospective": "NOT_STARTED",
            "shadow": "NOT_STARTED",
            "forward": "NOT_STARTED",
            "paper": "NOT_STARTED",
            "runtime": "UNCHANGED",
            "formal_line_status": "PFQ_CURRENT_LINE_CLOSED_REJECTED",
            "closure_reason": "EXIT_REVISION_DID_NOT_FIX_TARGET_FAILURE",
        },
        "E1_X8": {"status": "CLOSED_DIAGNOSTIC_COMPLETE"},
        "E1_X9": {"status": "CLOSED_NO_STABLE_UNIVERSE_REGIME"},
        "program": {
            "pfq_closed": True,
            "robust_strategy": False,
            "prospective_allowed": False,
            "shadow_allowed": False,
            "runtime_impact": False,
        },
    }

    rejected_paths = [
        "PFQ_UPDATE_Q70 as standalone ENTRY candidate revival",
        "Further tuning of PFQ_X_PROGRESS_STRUCT",
        "Alternate BE5 floor thresholds",
        "Rescue of PFQ_X_PROTECT",
        "Re-run PFQ excluding only 285A",
        "Re-run with q70 changed to 7",
        "Post-hoc selection of turnover HIGH only",
        "Promote UPDATE_HEAVY to new family from current near-signal alone",
        "All existing 4 ENTRY+EXIT economic pairs",
        "PFQ_FLOW_Q30 ENTRY path",
        "PFQ_JOINT economic pair (support insufficient)",
        "Low-turnover / low-participation Universe adoption",
    ]

    restart_prohibitions = [
        "Do not restart PFQ_UPDATE_Q70 as a standalone ENTRY candidate",
        "Do not further adjust PFQ_X_PROGRESS_STRUCT",
        "Do not try alternate BE5 floor thresholds",
        "Do not rescue PFQ_X_PROTECT",
        "Do not re-run PFQ excluding only 285A",
        "Do not re-run with q70 changed to 7",
        "Do not post-hoc select turnover HIGH only",
        "Do not promote UPDATE_HEAVY to a new family from current results alone",
        "Restart requires an independent new hypothesis, new precommit, and new family identity outside the current 9-day design period",
    ]

    future_principles = [
        "Independent hypothesis — not PFQ threshold micro-tuning",
        "ENTRY features preferably 1–3",
        "Audit symbol leverage before using raw cross-symbol thresholds",
        "Confirm ENTRY path first-touch before EXIT design",
        "Adopt/reject ENTRY+EXIT as a set",
        "Do not support candidates on oracle max profit alone",
        "Use cost-inclusive executable bid/ask",
        "day × symbol bootstrap",
        "Require day deletion / symbol deletion / top trade exclusion",
        "Separate design period from unused Prospective",
    ]

    open_items = [
        {
            "item": "historical as-of market cap store",
            "purpose": "data infrastructure — not PFQ rescue",
        },
        {
            "item": "historical institutional / foreign ownership store",
            "purpose": "data infrastructure — not PFQ rescue",
        },
        {
            "item": "historical free-float store",
            "purpose": "data infrastructure — not PFQ rescue",
        },
    ]

    next_step = {
        "allowed": "selection of a new independent ENTRY family hypothesis only",
        "forbidden": [
            "automatic implementation",
            "candidate generation",
            "Prospective consumption",
            "Shadow start",
            "Runtime change",
        ],
        "auto_start_new_study": False,
    }

    source_registry = {
        k: {
            "run_id": v["run_id"],
            "analysis_id": v["analysis_id"],
            "verdict": v["verdict"],
            "report_sha": v["report_sha"],
            "audit_sha": v["audit_sha"],
            "generated_at_jst": v["generated_at_jst"],
            "superseded": v["superseded"],
            "canonical": v["canonical"],
            "role": v["role"],
        }
        for k, v in {**sources, **superseded}.items()
    }

    findings = {
        "pfq_entry": pfq_entry,
        "pfq_flow_joint": pfq_flow_joint,
        "pfq_economics": pfq_economics,
        "pfq_exit": pfq_exit,
        "kioxia": kioxia,
        "universe": universe,
        "update_heavy": update_heavy,
        "within_symbol": within_symbol,
        "final_statuses": final_statuses,
    }

    det = {
        "source_registry_sha": sha256_obj(source_registry),
        "canonical_run_sha": sha256_obj({
            k: {"run_id": sources[k]["run_id"], "verdict": sources[k]["verdict"],
                "report_sha": sources[k]["report_sha"], "audit_sha": sources[k]["audit_sha"]}
            for k in REQUIRED_VERDICT_CHECKS
        }),
        "final_findings_sha": sha256_obj(findings),
        "rejected_paths_sha": sha256_obj(rejected_paths),
        "future_principles_sha": sha256_obj(future_principles),
        "open_items_sha": sha256_obj(open_items),
        "verdict": FINAL_VERDICT,
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "label": label,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "verdict": FINAL_VERDICT,
        "verdict_detail": {
            "verdict": FINAL_VERDICT,
            "pfq_closed": True,
            "robust_strategy": False,
            "prospective_allowed": False,
            "shadow_allowed": False,
            "runtime_impact": False,
            "next": next_step,
        },
        "source_identity_ok": True,
        "sources": sources,
        "superseded_runs": superseded,
        "source_registry": source_registry,
        "findings": findings,
        "rejected_paths": rejected_paths,
        "restart_prohibitions": restart_prohibitions,
        "future_design_principles": future_principles,
        "open_items": open_items,
        "next_step": next_step,
        "program_status": final_statuses,
        "determinism_shas": det,
        "safety": _safety(),
        "stop": True,
        "_sheets": {
            "SourceRuns": [
                {"key": k, **{kk: vv for kk, vv in v.items() if kk != "key"}}
                for k, v in sources.items()
            ],
            "SourceIdentity": [
                {
                    "key": k,
                    "run_id": v["run_id"],
                    "analysis_id": v["analysis_id"],
                    "verdict": v["verdict"],
                    "report_sha": v["report_sha"],
                    "audit_sha": v["audit_sha"],
                    "generated_at_jst": v["generated_at_jst"],
                    "superseded": v["superseded"],
                    "run_id_match": v["run_id_match"],
                    "verdict_match": v["verdict_match"],
                }
                for k, v in {**sources, **superseded}.items()
            ],
            "SupersededRuns": [
                {
                    "key": k,
                    "run_id": v["run_id"],
                    "verdict": v["verdict"],
                    "superseded_by": v["superseded_by"],
                    "reason": v["supersede_reason"],
                    "report_sha": v["report_sha"],
                    "audit_sha": v["audit_sha"],
                }
                for k, v in superseded.items()
            ],
            "PFQEntryFindings": [
                {"field": k, "value": v} for k, v in pfq_entry.items()
            ] + [
                {"field": f"flow_joint.{k}", "value": v} for k, v in pfq_flow_joint.items()
            ] + [
                {"field": f"economics.{k}", "value": v} for k, v in pfq_economics.items()
            ],
            "PFQExitFindings": [
                {"field": k, "value": (json.dumps(v) if isinstance(v, (dict, list)) else v)}
                for k, v in pfq_exit.items()
            ],
            "RevisionFailure": [
                {"metric": k, "value": v} for k, v in pfq_exit["mechanism"].items()
            ] + [
                {"metric": "baseline_pnl", "value": pfq_exit["baseline_pnl"]},
                {"metric": "revision_pnl", "value": pfq_exit["revision_pnl"]},
                {"metric": "formal_status", "value": pfq_exit["formal_status"]},
                {"metric": "closure_reason", "value": pfq_exit["closure_reason"]},
            ],
            "SymbolLeverage": [
                {"section": sk, "field": fk, "value": fv}
                for sk, block in kioxia.items() if isinstance(block, dict)
                for fk, fv in block.items()
            ] + [{"section": "meta", "field": "source", "value": kioxia["source"]}],
            "KioxiaDependence": [
                {"aspect": "threshold_leverage", "conclusion": kioxia["threshold_leverage"]["conclusion"]},
                {"aspect": "signal_dependence", "conclusion": kioxia["signal_dependence"]["conclusion"]},
                {"aspect": "economic_dependence", "conclusion": kioxia["economic_dependence"]["interpretation"]},
            ],
            "UniverseRegime": [
                {"field": k, "value": (json.dumps(v) if isinstance(v, (dict, list)) else v)}
                for k, v in universe.items()
            ],
            "MetadataLimitations": [
                {"item": x, "status": "NOT_EVALUABLE"} for x in universe["not_evaluable"]
            ] + [
                {"item": "UPDATE_HEAVY", "status": update_heavy["status"]},
                {"item": "within_symbol_normalization", "status": "DESCRIPTIVE_NOT_SUPERIOR_TO_RAW"},
            ],
            "FinalStatuses": [
                {"program": k, "status_json": json.dumps(v)}
                for k, v in final_statuses.items()
            ],
            "RejectedPaths": [{"path": p} for p in rejected_paths] + [
                {"path": f"PROHIBITION: {p}"} for p in restart_prohibitions
            ],
            "FutureDesignPrinciples": [
                {"n": i + 1, "principle": p} for i, p in enumerate(future_principles)
            ],
            "OpenItems": open_items,
        },
    }
    return report
