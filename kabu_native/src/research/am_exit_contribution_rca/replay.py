"""Replay frozen C0 overlay. No new fit. No rule change."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.am_current_utility_augment.ensemble import attach_ensemble
from research.am_current_utility_augment.overlay import overlay_replay
from research.am_entry_architecture_final_reassessment import A3_REUSE_ARM, A5_REUSE_ARM, C0
from research.am_entry_architecture_final_reassessment.consensus import cohort_decisions, merge_model_fields, tag_consensus
from research.am_entry_profit_improvement import ELIGIBLE_DAYS, REPRESENTATION_N
from research.am_entry_temporal_regime_information.oof import arm_spec_grid, process_oof_scores
from research.am_expanded_entry_risk_integration.ensemble import joint_map

NATIVE = Path(__file__).resolve().parents[3]
REGIME_PROBA = NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information"


def _load_scores(reuse_arm: str, rows_path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    specs = arm_spec_grid(reuse_arm)
    by_rep: dict[str, dict[str, Any]] = {}
    rep_ids = []
    for s in specs:
        rid = str(s.get("representation_id") or "")
        rep_ids.append(rid)
        cache = REGIME_PROBA / f"{reuse_arm}_{rid.replace('|', '_')}_oof_proba.json"
        body = process_oof_scores(
            {
                "spec_id": str(s.get("spec_id")),
                "spec": s,
                "days": list(ELIGIBLE_DAYS),
                "rows_path": str(rows_path),
                "cache_path": str(cache),
                "reuse_cache_path": str(cache),
            }
        )
        if not body.get("ok"):
            raise RuntimeError(f"OOF missing {reuse_arm} {rid}: {body.get('blocker')}")
        by_rep[rid] = dict(body.get("scores") or {})
    if len(by_rep) != int(REPRESENTATION_N):
        raise RuntimeError(f"rep count {reuse_arm}={len(by_rep)}")
    return by_rep, rep_ids


def replay_c0(rows: list[dict[str, Any]], rows_path: Path) -> dict[str, Any]:
    b0_scores, b0_reps = _load_scores(A3_REUSE_ARM, rows_path)
    b1_scores, b1_reps = _load_scores(A5_REUSE_ARM, rows_path)
    b0_tagged = attach_ensemble(rows, joint_map(b0_scores), b0_reps)
    b1_tagged = attach_ensemble(rows, joint_map(b1_scores), b1_reps)
    merged = merge_model_fields(b0_tagged, b1_tagged)
    dec = cohort_decisions(merged)
    tagged = tag_consensus(merged, set(dec.get("c0_keys") or set()), score_prefix="B0")
    baseline = overlay_replay(rows, include_augment=False)
    overlay = overlay_replay(tagged, include_augment=True, augment_rank="utility")
    cur = [t for t in (baseline.get("trades") or []) if str(t.get("arm") or "CURRENT") == "CURRENT"]
    aug = [t for t in (overlay.get("trades") or []) if str(t.get("arm") or "") == "AUGMENT"]
    ov_all = list(overlay.get("trades") or [])
    return {
        "architecture_id": C0,
        "current_trades": cur,
        "augment_trades": aug,
        "overlay_trades": ov_all,
        "baseline": baseline,
        "overlay": overlay,
        "geometry": dec.get("geometry"),
    }
