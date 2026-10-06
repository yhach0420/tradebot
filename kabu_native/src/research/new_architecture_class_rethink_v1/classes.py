"""C1-C4 Architecture Class eligibility. No ENTRY/EXIT rules. No event-count ranking."""
from __future__ import annotations

from typing import Any

from research.new_architecture_class_rethink_v1 import (
    CLASS_IDS,
    CLOSED_LINEAGE_IDS,
    ELIGIBILITY_FIELDS,
)
from research.new_architecture_class_rethink_v1.spec import selection_priority
from research.systematic_state_transition_library_precommit_v1.inventory_correction import remaining_eligible_ids


def _row(**kwargs: Any) -> dict[str, Any]:
    row = {k: kwargs[k] for k in ELIGIBILITY_FIELDS}
    if str(row["CLASS_ID"]) not in CLASS_IDS:
        raise ValueError(f"illegal CLASS_ID {row['CLASS_ID']}")
    if kwargs.get("ELIGIBLE") is None:
        raise ValueError("ELIGIBLE_UNJUDGED")
    return row


def closed_classes() -> list[str]:
    extra = list(CLOSED_LINEAGE_IDS)
    remaining = remaining_eligible_ids()
    return extra + [f"REMAINING_ELIGIBLE={remaining}"]


def evaluate_classes(cov: dict[str, Any]) -> list[dict[str, Any]]:
    htf_proven = bool(cov.get("HIGHER_TF_COMPLETED_EVENT_SEMANTICS_PROVEN"))
    tf3_days = int(cov.get("TF3_AVAILABLE_DAY_N") or 0)
    tf5_days = int(cov.get("TF5_AVAILABLE_DAY_N") or 0)
    tf1_days = int(cov.get("TF1_AVAILABLE_DAY_N") or 0)
    stream_days = int(cov.get("CANDIDATE_STREAM_DAY_N") or 0)
    fill_days = int(cov.get("POST_FILL_FIELD_DAY_N") or 0)
    v7_missing = int(cov.get("V7_MISSING_DAY_N") or 0)
    st_missing = int(cov.get("ST_MISSING_DAY_N") or 0)
    mixed_n = int(cov.get("MIXED_TF_STRATEGY_N") or 0)
    future_bar = int(cov.get("FUTURE_BAR_N") or 0)

    c1_inputs = tf3_days == 10 and tf5_days == 10 and tf1_days == 10 and v7_missing == 0
    c1_ts = bool(htf_proven and future_bar == 0)
    c1_eligible = bool(
        c1_inputs
        and c1_ts
        and mixed_n == 0
    )
    c1 = _row(
        CLASS_ID="C1_MULTI_TIMEFRAME",
        STRUCTURALLY_DISTINCT=True,
        PRIOR_FULL_CAUSAL_TESTED=False,
        CLOSED_LINEAGE_MATCH=False,
        CAUSAL_INPUTS_AVAILABLE=bool(c1_inputs),
        TIMESTAMP_SEMANTICS_PROVEN=bool(c1_ts),
        FULL_CAUSAL_REPLAY_FEASIBLE=bool(c1_inputs and c1_ts),
        NEW_PARAMETER_REQUIRED=False,
        LABEL_LEAKAGE_REQUIRED=False,
        PRIMITIVE_AVAILABLE_DAY_N=int(min(tf1_days, tf3_days, tf5_days)),
        MISSING_REQUIRED_FIELDS=[] if c1_inputs else ["completed_3m_5m_state_on_all_DEV_days"],
        ELIGIBLE=bool(c1_eligible),
        WHY=(
            "1m signal + last completed 3m/5m bar with finalize_t<=t0. "
            "aggregate_bars drops partial buckets; asof at mid-bucket has no HTF bar; "
            "same-bucket V7 diagnostic join is rejected. Distinct from 1m persist/handoff, "
            "V7 role RCA (not Full Causal mixed-TF), and V28 3m EXIT. Existing V7 widths "
            "180/300 and existing 1m state predicates are finite without search. "
            f"MIXED_TF_STRATEGY_N={mixed_n}."
            if c1_eligible
            else (
                "C1 ineligible: higher-TF completed asof semantics not proven or DEV 3m/5m "
                f"primitive days incomplete (tf1={tf1_days} tf3={tf3_days} tf5={tf5_days} "
                f"htf_proven={c1_ts} missing={v7_missing} future_bar={future_bar})."
            )
        ),
    )

    c2 = _row(
        CLASS_ID="C2_EPISODE_AGE",
        STRUCTURALLY_DISTINCT=False,
        PRIOR_FULL_CAUSAL_TESTED=True,
        CLOSED_LINEAGE_MATCH=True,
        CAUSAL_INPUTS_AVAILABLE=bool(tf1_days == 10 and v7_missing == 0),
        TIMESTAMP_SEMANTICS_PROVEN=True,
        FULL_CAUSAL_REPLAY_FEASIBLE=True,
        NEW_PARAMETER_REQUIRED=True,
        LABEL_LEAKAGE_REQUIRED=False,
        PRIMITIVE_AVAILABLE_DAY_N=int(tf1_days),
        MISSING_REQUIRED_FIELDS=[],
        ELIGIBLE=False,
        WHY=(
            "Causal FALSE→TRUE onset is observable on existing 1m states, and age can be "
            "added in event time without a future episode endpoint. Any finite precommit "
            "without a new age threshold is PERSIST_NEXT wait 1→2→3 bars, which matches "
            "closed TEMPORAL_STATE_TRANSITION. Hindsight segmentation unused."
        ),
    )

    c3 = _row(
        CLASS_ID="C3_FAILURE_ROUTING",
        STRUCTURALLY_DISTINCT=False,
        PRIOR_FULL_CAUSAL_TESTED=True,
        CLOSED_LINEAGE_MATCH=True,
        CAUSAL_INPUTS_AVAILABLE=bool(fill_days == 10 and st_missing == 0),
        TIMESTAMP_SEMANTICS_PROVEN=True,
        FULL_CAUSAL_REPLAY_FEASIBLE=True,
        NEW_PARAMETER_REQUIRED=True,
        LABEL_LEAKAGE_REQUIRED=False,
        PRIMITIVE_AVAILABLE_DAY_N=int(fill_days),
        MISSING_REQUIRED_FIELDS=[],
        ELIGIBLE=False,
        WHY=(
            "Post-fill price/VWAP/EMA/BB/RCI/volume/elapsed fields exist on DEV fills. "
            "never-BE vs eventual-BE is RCA-only and is not used as a trigger label. "
            "Label-guided feature pick is forbidden, so a routing split cannot be frozen "
            "without search. Using existing V26/Branch-U/P primitives is closed EXIT lineage."
        ),
    )

    stream_ok = stream_days == 10 and st_missing == 0
    c4 = _row(
        CLASS_ID="C4_PORTFOLIO_CROWDING",
        STRUCTURALLY_DISTINCT=True,
        PRIOR_FULL_CAUSAL_TESTED=False,
        CLOSED_LINEAGE_MATCH=False,
        CAUSAL_INPUTS_AVAILABLE=bool(stream_ok),
        TIMESTAMP_SEMANTICS_PROVEN=bool(stream_ok),
        FULL_CAUSAL_REPLAY_FEASIBLE=bool(stream_ok),
        NEW_PARAMETER_REQUIRED=False,
        LABEL_LEAKAGE_REQUIRED=False,
        PRIMITIVE_AVAILABLE_DAY_N=int(stream_days),
        MISSING_REQUIRED_FIELDS=[] if stream_ok else ["pre_admission_candidate_stream"],
        ELIGIBLE=bool(stream_ok),
        WHY=(
            "Existing ST library harvest rows are a pre-admission candidate stream with "
            "candidate timestamps; occupancy and same-symbol are restorable by event-time "
            "portfolio_replay. Not reverse-engineered from a completed trade ledger. "
            "CAP stays an occupancy constraint. Quality top5 / symbol ranking unused. "
            "Distinct from CAP-as-hard-block and from closed ENTRY identities. "
            "Finite no-search form is timestamp uniqueness, not a searched occupancy k."
            if stream_ok
            else "C4 ineligible: pre-admission candidate stream not restorable on all DEV days."
        ),
    )
    rows = [c1, c2, c3, c4]
    if [r["CLASS_ID"] for r in rows] != list(CLASS_IDS):
        raise RuntimeError("CLASS_ID_ORDER")
    return rows


def finite_precommit_without_search(row: dict[str, Any]) -> bool:
    cid = str(row["CLASS_ID"])
    if cid == "C1_MULTI_TIMEFRAME":
        return bool(row["ELIGIBLE"]) and not row["NEW_PARAMETER_REQUIRED"]
    if cid == "C4_PORTFOLIO_CROWDING":
        return bool(row["ELIGIBLE"]) and not row["NEW_PARAMETER_REQUIRED"]
    return False


def primary_objective_rank(row: dict[str, Any]) -> int:
    """Lower is better. Does not use PnL or event counts."""
    cid = str(row["CLASS_ID"])
    if cid == "C1_MULTI_TIMEFRAME":
        return 0
    if cid == "C3_FAILURE_ROUTING":
        return 1
    if cid == "C4_PORTFOLIO_CROWDING":
        return 2
    if cid == "C2_EPISODE_AGE":
        return 3
    return 9


def reconstruction_rank(row: dict[str, Any]) -> int:
    cid = str(row["CLASS_ID"])
    if cid == "C4_PORTFOLIO_CROWDING":
        return 0
    if cid == "C1_MULTI_TIMEFRAME":
        return 1
    if cid == "C3_FAILURE_ROUTING":
        return 2
    return 3


def priority_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if row.get("STRUCTURALLY_DISTINCT") else 1,
        0 if row.get("CAUSAL_INPUTS_AVAILABLE") else 1,
        0 if finite_precommit_without_search(row) else 1,
        0 if row.get("FULL_CAUSAL_REPLAY_FEASIBLE") else 1,
        int(primary_objective_rank(row)),
        int(reconstruction_rank(row)),
        0 if not row.get("NEW_PARAMETER_REQUIRED") else 1,
        str(row.get("CLASS_ID") or ""),
    )


def select_class(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [r for r in rows if r.get("ELIGIBLE")]
    if not eligible:
        return None
    ordered = sorted(eligible, key=priority_key)
    return ordered[0]


def causality_flags(cov: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = {r["CLASS_ID"]: r for r in rows}
    stream_ok = bool(by["C4_PORTFOLIO_CROWDING"]["CAUSAL_INPUTS_AVAILABLE"])
    return {
        "HIGHER_TF_COMPLETED_EVENT_SEMANTICS_PROVEN": bool(cov.get("HIGHER_TF_COMPLETED_EVENT_SEMANTICS_PROVEN")),
        "EPISODE_START_CAUSALLY_OBSERVABLE": True,
        "FUTURE_EPISODE_ENDPOINT_USED": False,
        "FUTURE_OUTCOME_LABEL_USED_IN_TRIGGER": False,
        "LABEL_GUIDED_FEATURE_SELECTION": False,
        "PRE_ADMISSION_CANDIDATE_STREAM_AVAILABLE": bool(stream_ok),
        "C4_DATA_FEASIBLE": bool(stream_ok),
        "PNL_USED_TO_SELECT_CLASS": False,
        "EVENT_COUNT_USED_TO_SELECT_CLASS": False,
        "SELECTION_PRIORITY": selection_priority(),
        "CLOSED_LINEAGE_IDS": list(CLOSED_LINEAGE_IDS),
    }
