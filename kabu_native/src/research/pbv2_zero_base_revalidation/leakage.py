"""Point-in-time and chronological leakage audits."""
from __future__ import annotations

from typing import Any, Sequence

from research.pbv2_zero_base_revalidation.constants import TIME_FEATURE_BLOCKLIST
from research.pbv2_zero_base_revalidation.labels import assert_no_future_in_features
from research.pbv2_zero_base_revalidation.panel import CandidateRow


def audit_panel_leakage(panel: Sequence[CandidateRow]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for row in panel:
        try:
            assert_no_future_in_features(row)
        except AssertionError as e:
            issues.append({"type": "future_feature", "id": row.evaluation_event_id, "detail": str(e)})
        for k in row.features:
            lk = k.lower()
            if any(b in lk for b in TIME_FEATURE_BLOCKLIST):
                issues.append({"type": "time_feature", "id": row.evaluation_event_id, "feature": k})
            if lk.startswith("symbol_") or lk in ("symbol_id", "symbol_code_hash"):
                issues.append({"type": "symbol_feature", "id": row.evaluation_event_id, "feature": k})
        # ages / times must be causal relative to evaluation_time
        if row.current_price_time is not None and row.current_price_time > row.evaluation_time:
            issues.append({"type": "price_time_future", "id": row.evaluation_event_id})
        if row.board_time is not None and row.board_time > row.evaluation_time:
            issues.append({"type": "board_time_future", "id": row.evaluation_event_id})
        if row.board_age_sec is not None and row.board_age_sec < -1e-6:
            issues.append({"type": "negative_board_age", "id": row.evaluation_event_id})
        if row.price_age_sec is not None and row.price_age_sec < -1e-6:
            issues.append({"type": "negative_price_age", "id": row.evaluation_event_id})
    blocked = len(issues) > 0
    return {
        "leakage_blocked": blocked,
        "n_issues": len(issues),
        "issues_sample": issues[:50],
        "verdict": "DATA_LEAKAGE_BLOCKED" if blocked else "LEAKAGE_AUDIT_PASS",
    }


def audit_fold(train_days: Sequence[str], test_day: str) -> dict[str, Any]:
    max_train = max(train_days) if train_days else ""
    ok = bool(train_days) and max_train < test_day
    return {
        "train_start": min(train_days) if train_days else "",
        "train_end": max_train,
        "test_date": test_day,
        "max_train_date": max_train,
        "max_train_date_lt_test": ok,
        "train_days_n": len(train_days),
    }
