"""JP cash session eligibility resolver. Calendar date +1 is not a session.

Does not open 20260924 market data. Completeness is injected by the caller.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from research.pb1_v4_prospective_semantic_validation_preflight import FIRST_ELIGIBLE_JP_CASH_SESSION, NON_CASH_DATES

# Frozen JP cash closed dates for the prospective window. Not loaded from 20260924 bars.
TSE_CASH_CLOSED = {
    "20260921": "Respect for the Aged Day",
    "20260922": "Citizen's Holiday",
    "20260923": "Autumnal Equinox Day",
    "20261012": "Health and Sports Day",
    "20261103": "Culture Day",
    "20261123": "Labor Thanksgiving Day",
}
FREEZE_SESSION_DATE = "20260920"


def parse_session(session: str) -> datetime:
    raw = str(session).replace("-", "")[:8]
    return datetime.strptime(raw, "%Y%m%d")


def is_weekend(session: str) -> bool:
    return parse_session(session).weekday() >= 5


def tse_cash_calendar(session: str) -> dict[str, Any]:
    day = str(session).replace("-", "")[:8]
    if is_weekend(day):
        return {"is_tse_cash_session": False, "counts_as_session": False, "label": "weekend"}
    label = TSE_CASH_CLOSED.get(day)
    if label:
        return {"is_tse_cash_session": False, "counts_as_session": False, "label": label}
    return {"is_tse_cash_session": True, "counts_as_session": True, "label": "tse_cash_session"}


def resolve_jp_cash_session(
    session: str,
    *,
    freeze_date: str = FREEZE_SESSION_DATE,
    first_eligible: str = FIRST_ELIGIBLE_JP_CASH_SESSION,
    ledger_keys: set[tuple[str, str]] | None = None,
    symbol: str | None = None,
    completeness: dict[str, Any] | None = None,
    allow_open_prospective: bool = False,
) -> dict[str, Any]:
    day = str(session).replace("-", "")[:8]
    cal = tse_cash_calendar(day)
    strictly_after_freeze = day > str(freeze_date).replace("-", "")[:8]
    calendar_plus_one = str(int(str(freeze_date).replace("-", "")[:8]) + 1) if str(freeze_date).replace("-", "")[:8].isdigit() else ""
    in_ledger = bool(symbol and ledger_keys and (str(symbol), day) in ledger_keys)
    complete = bool((completeness or {}).get("ok"))
    prospective_window = day >= str(first_eligible)
    opened = bool(allow_open_prospective)
    reasons: list[str] = []
    if not strictly_after_freeze:
        reasons.append("not_strictly_after_freeze_at")
    if not cal["is_tse_cash_session"]:
        reasons.append(f"not_tse_cash:{cal['label']}")
    if day == calendar_plus_one and not cal["is_tse_cash_session"]:
        reasons.append("calendar_plus_one_is_not_a_session")
    if in_ledger:
        reasons.append("contamination_ledger")
    if prospective_window and not opened:
        reasons.append("PROSPECTIVE_DATA_NOT_OPENED_THIS_TASK")
    if completeness is not None and not complete:
        reasons.append("incomplete_or_failed_quality_gate")
    if completeness is None and opened:
        reasons.append("completeness_required")
    eligible = (
        strictly_after_freeze
        and bool(cal["is_tse_cash_session"])
        and not in_ledger
        and opened
        and complete
        and day >= str(first_eligible)
    )
    counts = bool(cal["counts_as_session"]) and strictly_after_freeze and opened and complete and not in_ledger
    return {
        "session": day,
        "is_tse_cash_session": bool(cal["is_tse_cash_session"]),
        "calendar_label": cal["label"],
        "strictly_after_freeze": strictly_after_freeze,
        "first_eligible_session": first_eligible,
        "calendar_plus_one_rejected": day in set(NON_CASH_DATES) or day == calendar_plus_one,
        "in_contamination_ledger": in_ledger,
        "completeness_ok": complete if completeness is not None else None,
        "allow_open_prospective": opened,
        "eligible": eligible,
        "counts_as_session": counts,
        "20260921_COUNTS_AS_SESSION": False,
        "20260922_COUNTS_AS_SESSION": False,
        "20260923_COUNTS_AS_SESSION": False,
        "reasons": reasons,
    }
