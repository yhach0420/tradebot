"""Calendar reason erratum. Eligibility fields and stopping rule stay frozen."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from research.pb1_v4_prospective_semantic_validation_preflight import (
    CALENDAR_CORRECTED_PRECOMMIT_SHA256,
    CALENDAR_REASON,
    FIRST_ELIGIBLE_JP_CASH_SESSION,
    FREEZE_PRECOMMIT_SHA256,
    NON_CASH_DATES,
)

ELIGIBILITY_KEYS = (
    "rule",
    "first_eligible_session",
    "calendar_plus_one_is_not_a_session",
    "tse_cash_only",
    "derivative_holiday_sessions_not_eligible",
    "20260921_COUNTS_AS_SESSION",
    "20260922_COUNTS_AS_SESSION",
    "20260923_COUNTS_AS_SESSION",
    "non_cash_dates_explicit",
)


def _hash_body(body: dict[str, Any]) -> str:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_parent_precommit(parent: dict[str, Any]) -> dict[str, Any]:
    stored = str(parent.get("PRECOMMIT_SHA256") or "")
    body = {k: v for k, v in parent.items() if k != "PRECOMMIT_SHA256"}
    recomputed = _hash_body(body)
    return {
        "stored": stored,
        "recomputed": recomputed,
        "matches_calendar_corrected": stored == CALENDAR_CORRECTED_PRECOMMIT_SHA256,
        "recomputed_matches_stored": recomputed == stored,
        "ok": stored == CALENDAR_CORRECTED_PRECOMMIT_SHA256 and recomputed == stored,
    }


def eligibility_unchanged(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    p = dict(parent.get("data_eligibility") or {})
    c = dict(child.get("data_eligibility") or {})
    diffs = []
    for k in ELIGIBILITY_KEYS:
        if p.get(k) != c.get(k):
            diffs.append({"key": k, "parent": p.get(k), "child": c.get(k)})
    stop_p = dict(parent.get("observation_period_stopping_rule") or {})
    stop_c = dict(child.get("observation_period_stopping_rule") or {})
    for k in (
        "close_when",
        "min_candidate_days_WHY_THIS_STOCK",
        "max_sessions",
        "max_sessions_are_actual_eligible_jp_cash_sessions",
        "non_business_days_do_not_count",
        "partial_incomplete_session_ineligible",
        "stopping_rule_thresholds_unchanged",
    ):
        if stop_p.get(k) != stop_c.get(k):
            diffs.append({"key": f"stop.{k}", "parent": stop_p.get(k), "child": stop_c.get(k)})
    return {
        "ok": not diffs,
        "diffs": diffs,
        "FIRST_ELIGIBLE_JP_CASH_SESSION": c.get("first_eligible_session"),
        "20260921_COUNTS_AS_SESSION": c.get("20260921_COUNTS_AS_SESSION"),
        "20260922_COUNTS_AS_SESSION": c.get("20260922_COUNTS_AS_SESSION"),
        "20260923_COUNTS_AS_SESSION": c.get("20260923_COUNTS_AS_SESSION"),
    }


def apply_reason_erratum(parent: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy({k: v for k, v in parent.items() if k != "PRECOMMIT_SHA256"})
    cal = dict(body.get("calendar_correction") or {})
    cal["reason"] = CALENDAR_REASON
    cal["holiday_labels"] = {
        "20260921": "Respect for the Aged Day",
        "20260922": "Citizen's Holiday",
        "20260923": "Autumnal Equinox Day",
    }
    cal["kind"] = "CALENDAR_REASON_ERRATUM_METADATA_ONLY"
    cal["eligibility_unchanged"] = True
    cal["old_first_eligible_session"] = "20260921"
    cal["new_first_eligible_session"] = FIRST_ELIGIBLE_JP_CASH_SESSION
    body["calendar_correction"] = cal
    body["OLD_PRECOMMIT_SHA256"] = str(body.get("OLD_PRECOMMIT_SHA256") or FREEZE_PRECOMMIT_SHA256)
    body["PARENT_PRECOMMIT_SHA256"] = CALENDAR_CORRECTED_PRECOMMIT_SHA256
    body["CALENDAR_CORRECTED_PRECOMMIT_SHA256"] = CALENDAR_CORRECTED_PRECOMMIT_SHA256
    body["PROSPECTIVE_DATA_OPENED"] = False
    new_sha = _hash_body(body)
    if new_sha in {CALENDAR_CORRECTED_PRECOMMIT_SHA256, FREEZE_PRECOMMIT_SHA256}:
        raise RuntimeError("NEW_PRECOMMIT_SHA_COLLIDES_WITH_PARENT")
    body["PRECOMMIT_SHA256"] = new_sha
    elig = dict(body.get("data_eligibility") or {})
    if str(elig.get("first_eligible_session") or "") != FIRST_ELIGIBLE_JP_CASH_SESSION:
        raise RuntimeError("ELIGIBILITY_SESSION_MUTATED")
    if list(elig.get("non_cash_dates_explicit") or []) != list(NON_CASH_DATES):
        raise RuntimeError("NON_CASH_DATES_MUTATED")
    return body
