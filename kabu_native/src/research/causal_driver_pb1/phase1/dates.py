"""Allowed USDJPY ingest dates. Frozen Validation and Prospective are hard-deny even for FX."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from research.causal_driver_pb1 import C1_LAST, DEV_FIRST, FV_FIRST, FV_LAST, PROSPECTIVE_FROM
from research.causal_driver_pb1.contracts.enums import DatasetRole
from research.causal_driver_pb1.datasets.roles import role_for_session_date
from research.causal_driver_pb1.phase1.errors import IngestDateDenied


def yyyymmdd(d: date | str) -> str:
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d)


def parse_yyyymmdd(text: str) -> date:
    return datetime.strptime(str(text), "%Y%m%d").date()


def day_iter(start: str, end: str) -> list[date]:
    a = parse_yyyymmdd(start)
    b = parse_yyyymmdd(end)
    out: list[date] = []
    cur = a
    while cur <= b:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def ingest_date_allowed(session_yyyymmdd: str) -> bool:
    d = yyyymmdd(session_yyyymmdd)
    if d < DEV_FIRST:
        return False
    if d > C1_LAST:
        return False
    if FV_FIRST <= d <= FV_LAST:
        return False
    if d >= PROSPECTIVE_FROM:
        return False
    return True


def assert_ingest_date_allowed(session_yyyymmdd: str) -> DatasetRole:
    d = yyyymmdd(session_yyyymmdd)
    if not ingest_date_allowed(d):
        if FV_FIRST <= d <= FV_LAST:
            raise IngestDateDenied("FROZEN_VALIDATION_USDJPY_DENIED")
        if d >= PROSPECTIVE_FROM:
            raise IngestDateDenied("PROSPECTIVE_USDJPY_DENIED")
        if d > C1_LAST:
            raise IngestDateDenied("AFTER_ALLOWED_USDJPY_WINDOW")
        raise IngestDateDenied("BEFORE_DEVELOPMENT_USDJPY_DENIED")
    return role_for_session_date(d)


def assert_utc_source_file_date_allowed(file_day: date) -> None:
    """UTC Jetta file dates. 20260422+ files are never opened."""
    key = yyyymmdd(file_day)
    if key >= FV_FIRST:
        raise IngestDateDenied("FROZEN_VALIDATION_USDJPY_FILE_DENIED")
    if key >= PROSPECTIVE_FROM:
        raise IngestDateDenied("PROSPECTIVE_USDJPY_FILE_DENIED")
    if key < "20240916":
        raise IngestDateDenied("BEFORE_SOURCE_FILE_WINDOW")
