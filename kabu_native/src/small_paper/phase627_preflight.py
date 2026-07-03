"""
Phase627: startup preflight for the PBv2 cluster-guard collapse production fix.

Verifies, before every paper-trade session start:
  1. production YAML keeps entry_cluster_guard_reject_csubs == []
  2. cluster guard feature-completeness safety is active (functional check)
  3. PBv2 internal reason logging fields are wired into EVENT_FIELDS
  4. the OR overlay reason mask cannot erase pbv2_internal_reason (functional check)
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

PHASE627_VERDICT = "phase627_cluster_guard_production_fix_done"

_REQUIRED_EVENT_FIELDS = (
    "pbv2_internal_reason",
    "pbv2_internal_gate",
    "or_overlay_reason",
    "final_reject_reason",
)

# Minimal live-shaped candidate: none of the csub/cluster raw features present
# (reproduces the 6/29-6/30 degenerate classification input).
_INCOMPLETE_TRADE: dict[str, Any] = {
    "symbol": "6976.T",
    "entry_time": "2026-06-29T09:30:00+09:00",
}


def _check_reject_csubs_empty(config: Any) -> list[str]:
    raw = getattr(config, "raw", {}) or {}
    csubs = raw.get("entry_cluster_guard_reject_csubs", [0, 2, 3, 5])
    if list(csubs):
        return [
            "phase627: entry_cluster_guard_reject_csubs must stay [] "
            f"(Phase606 rollback), got {list(csubs)}"
        ]
    return []


def _check_feature_completeness_safety(config: Any, repo_root: Path) -> list[str]:
    from small_paper.entry_cluster_guard import (
        CLUSTER_GUARD_FEATURE_INCOMPLETE,
        FEATURE_COMPLETENESS_CHECK_ENABLED,
        build_entry_cluster_guard_state,
    )

    if not FEATURE_COMPLETENESS_CHECK_ENABLED:
        return ["phase627: cluster guard FEATURE_COMPLETENESS_CHECK_ENABLED is False"]
    if not getattr(config, "entry_cluster_guard_enabled", False):
        return []
    try:
        state = build_entry_cluster_guard_state(config, repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001 — preflight must surface any load failure
        return [f"phase627: cluster guard state build failed: {exc}"]
    if state is None:
        return ["phase627: cluster guard enabled but state is None"]

    # Force the incomplete candidate's own classification into the reject sets:
    # with the safety in place the guard must still NOT block.
    cls = state.model.classify(dict(_INCOMPLETE_TRADE))
    cid = int(cls.get("cluster_id") or -1)
    csub = int(cls.get("new_subcluster_id") or -1)
    forced = replace(
        state,
        config=replace(
            state.config,
            reject_clusters=frozenset({cid}),
            reject_csubs=frozenset({csub} if csub >= 0 else set()),
            exception_enabled=False,
        ),
    )
    chk = forced.check(dict(_INCOMPLETE_TRADE))
    errors: list[str] = []
    if chk.blocked:
        errors.append(
            "phase627: feature-incomplete candidate was REJECTED by cluster guard "
            f"(cluster_id={cid}, csub={csub}) — safety not active"
        )
    if chk.cluster_guard_status != CLUSTER_GUARD_FEATURE_INCOMPLETE:
        errors.append(
            "phase627: feature-incomplete candidate not tagged "
            f"FEATURE_INCOMPLETE (got {chk.cluster_guard_status!r})"
        )
    return errors


def _check_internal_reason_logging() -> list[str]:
    from small_paper.pilot_runner import EVENT_FIELDS

    missing = [f for f in _REQUIRED_EVENT_FIELDS if f not in EVENT_FIELDS]
    if missing:
        return [f"phase627: EVENT_FIELDS missing PBv2 internal reason fields: {missing}"]
    return []


def _check_or_overlay_mask_preserves_internal_reason() -> list[str]:
    from research.exposure_gate import GateDecision
    from small_paper.pilot_runner import _LiveRunState, _record_pbv2_internal_reject

    state = _LiveRunState(started_mono=0.0)
    trade: dict[str, Any] = {"symbol": "6976.T"}
    pbv2 = GateDecision(accept=False, reason="entry_cluster_guard")
    _record_pbv2_internal_reject(state, trade, pbv2)
    # Simulate the OR overlay masking the final decision reason.
    trade["or_overlay_reason"] = "or_overlay_not_candidate"
    trade["final_reject_reason"] = "or_overlay_not_candidate"
    errors: list[str] = []
    if trade.get("pbv2_internal_reason") != "entry_cluster_guard":
        errors.append(
            "phase627: pbv2_internal_reason lost after OR overlay mask "
            f"(got {trade.get('pbv2_internal_reason')!r})"
        )
    if trade.get("pbv2_internal_gate") != "entry_cluster_guard":
        errors.append(
            f"phase627: pbv2_internal_gate not recorded (got {trade.get('pbv2_internal_gate')!r})"
        )
    if state.pbv2_internal_reason_counts.get("entry_cluster_guard") != 1:
        errors.append("phase627: pbv2_internal_reason_counts not incremented")
    return errors


def phase627_preflight_checks(config: Any, *, repo_root: Optional[Path]) -> list[str]:
    """All Phase627 production-fix invariants; empty list means pass."""
    errors: list[str] = []
    errors.extend(_check_reject_csubs_empty(config))
    if repo_root is not None:
        errors.extend(_check_feature_completeness_safety(config, repo_root))
    else:
        errors.append("phase627: repo_root missing; cannot verify cluster guard safety")
    errors.extend(_check_internal_reason_logging())
    errors.extend(_check_or_overlay_mask_preserves_internal_reason())
    return errors
