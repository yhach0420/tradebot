"""Prospective Valid-Day gate — contamination days must not be counted.

SoT for 20260812:
  count_as_valid_prospective_day = False
  reasons: PRIMARY_CONTAMINATION + DUPLICATE_RUNTIME_CONTAMINATION
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[2]

# Hard fail-closed: these trading dates are never Valid Prospective Days.
INVALID_PROSPECTIVE_DAYS: dict[str, dict[str, Any]] = {
    "20260812": {
        "count_as_valid_prospective_day": False,
        "prospective_status": "INVALID_CONTAMINATION",
        "reasons": [
            "V1R_PBV2_PRIMARY_CONTAMINATION",
            "V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION",
        ],
        "market_data_use": "RETROSPECTIVE_OPERATIONAL_EVIDENCE_ONLY",
        "next_prospective_day1_candidate": "next fully unseen trading day after 20260812",
    },
}


def is_valid_prospective_day(trading_date: str) -> bool:
    day = str(trading_date or "").strip()
    if not day:
        return False
    if day in INVALID_PROSPECTIVE_DAYS:
        return False
    status = load_prospective_day_status(day)
    if status is None:
        return False  # fail-closed: no status → not counted yet
    if status.get("count_as_valid_prospective_day") is False:
        return False
    if status.get("count_as_valid_day") is False:
        return False
    return bool(status.get("count_as_valid_prospective_day"))


def load_prospective_day_status(trading_date: str) -> Optional[dict[str, Any]]:
    day = str(trading_date)
    hard = INVALID_PROSPECTIVE_DAYS.get(day)
    path = NATIVE / "results" / "small_paper" / day / "PROSPECTIVE_DAY_STATUS.json"
    file_status: Optional[dict[str, Any]] = None
    if path.is_file():
        try:
            file_status = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            file_status = None
    if hard is None and file_status is None:
        return None
    out: dict[str, Any] = {}
    if file_status:
        out.update(file_status)
    if hard:
        # Hard table wins on validity bits; merge reason lists.
        reasons = list(hard.get("reasons") or [])
        for r in out.get("invalidating_verdicts") or []:
            v = r.get("verdict") if isinstance(r, dict) else None
            if v and v not in reasons:
                reasons.append(v)
        out.update(hard)
        out["reasons"] = reasons
        out["count_as_valid_prospective_day"] = False
        out["count_as_valid_day"] = False
        out["prospective_day_number"] = None
    return out


def assert_not_counted_as_valid(trading_date: str) -> dict[str, Any]:
    ok = not is_valid_prospective_day(trading_date)
    return {
        "trading_date": str(trading_date),
        "is_valid_prospective_day": False if ok else True,
        "assert_not_counted": ok,
        "status": load_prospective_day_status(trading_date),
    }
