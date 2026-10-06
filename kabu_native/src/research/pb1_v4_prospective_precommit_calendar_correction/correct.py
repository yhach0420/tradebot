"""Rebuild calendar-corrected precommit. Hash over body without PRECOMMIT_SHA256."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from research.pb1_v4_prospective_precommit_calendar_correction import (
    FIRST_ELIGIBLE_JP_CASH_SESSION,
    NON_CASH_DATES,
    OLD_PRECOMMIT_SHA256,
)

ELIGIBLE_SESSION_RULE = (
    "eligible session = first actual TSE cash trading session strictly after freeze_at "
    "that passes the frozen market-data completeness gates. "
    "A calendar date +1 is not a session. Derivative holiday sessions are not JP cash sessions."
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
        "matches_expected_old": stored == OLD_PRECOMMIT_SHA256,
        "recomputed_matches_stored": recomputed == stored,
        "ok": stored == OLD_PRECOMMIT_SHA256 and recomputed == stored,
    }


def correct_precommit(parent: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy({k: v for k, v in parent.items() if k != "PRECOMMIT_SHA256"})
    elig = dict(body.get("data_eligibility") or {})
    elig["rule"] = ELIGIBLE_SESSION_RULE
    elig["first_eligible_session"] = FIRST_ELIGIBLE_JP_CASH_SESSION
    elig["calendar_plus_one_is_not_a_session"] = True
    elig["tse_cash_only"] = True
    elig["derivative_holiday_sessions_not_eligible"] = True
    elig["20260921_COUNTS_AS_SESSION"] = False
    elig["20260922_COUNTS_AS_SESSION"] = False
    elig["20260923_COUNTS_AS_SESSION"] = False
    elig["non_cash_dates_explicit"] = list(NON_CASH_DATES)
    body["data_eligibility"] = elig
    stop = dict(body.get("observation_period_stopping_rule") or {})
    stop["close_when"] = (
        "WHY_THIS_STOCK candidate-days >= 21 OR 40 actual eligible JP cash sessions, whichever first"
    )
    stop["max_sessions_are_actual_eligible_jp_cash_sessions"] = True
    stop["non_business_days_do_not_count"] = True
    stop["partial_incomplete_session_ineligible"] = True
    stop["stopping_rule_thresholds_unchanged"] = True
    body["observation_period_stopping_rule"] = stop
    body["OLD_PRECOMMIT_SHA256"] = OLD_PRECOMMIT_SHA256
    body["calendar_correction"] = {
        "kind": "CALENDAR_PRECOMMIT_METADATA_ONLY",
        "V4_CHANGED": False,
        "SPEC_CHANGED": False,
        "contamination_ledger_changed": False,
        "old_first_eligible_session": "20260921",
        "new_first_eligible_session": FIRST_ELIGIBLE_JP_CASH_SESSION,
        "reason": "20260921 Respect-for-the-Aged-Day holiday; 20260922-23 weekend; not TSE cash sessions",
    }
    body["PROSPECTIVE_DATA_OPENED"] = False
    new_sha = _hash_body(body)
    if new_sha == OLD_PRECOMMIT_SHA256:
        raise RuntimeError("NEW_PRECOMMIT_SHA_COLLIDES_WITH_OLD")
    body["PRECOMMIT_SHA256"] = new_sha
    return body
