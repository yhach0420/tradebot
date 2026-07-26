"""Daily Market Capture Completeness Gate — quality labels (not hard research ban).

Day labels:
  COMPLETE_CAPTURE
  PARTIAL_CAPTURE
  CORRUPTED_CAPTURE
  NO_DATA
  DQ_BLOCKED

Legacy aliases retained for callers:
  CAPTURE_COMPLETE / CAPTURE_PARTIAL / CAPTURE_TRUNCATED / CAPTURE_DQ_BLOCKED

PARTIAL_CAPTURE: seal_pass=false for day completeness, but VALID_COMPLETE_WINDOW
trades remain research-usable via capture_window_validator.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

COMPLETE_CAPTURE = "COMPLETE_CAPTURE"
PARTIAL_CAPTURE = "PARTIAL_CAPTURE"
CORRUPTED_CAPTURE = "CORRUPTED_CAPTURE"
NO_DATA = "NO_DATA"
DQ_BLOCKED = "DQ_BLOCKED"

# Legacy aliases
CAPTURE_COMPLETE = COMPLETE_CAPTURE
CAPTURE_PARTIAL = PARTIAL_CAPTURE
CAPTURE_TRUNCATED = PARTIAL_CAPTURE
CAPTURE_DQ_BLOCKED = DQ_BLOCKED

PM_END = time(15, 20)
AM_START = time(9, 0)
AM_END = time(11, 30)
PM_START = time(12, 30)
FINALIZE = time(15, 35)
EARLY_END = time(15, 0)
LUNCH_START = time(11, 30)
LUNCH_END = time(12, 30)


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _day_dt(trading_date: str, hhmm: time) -> datetime:
    y, m, d = int(trading_date[:4]), int(trading_date[4:6]), int(trading_date[6:8])
    return datetime(y, m, d, hhmm.hour, hhmm.minute, tzinfo=JST)


def evaluate_capture_completeness(
    *,
    trading_date: str,
    first_event_at: Any,
    last_event_at: Any,
    dropped_event_count: int = 0,
    disconnect_count: int = 0,
    reconnect_count: int = 0,
    recovery_success_count: int = 0,
    registration_symbol_count: int = 0,
    expected_registration_symbols: int = 50,
    largest_gap_sec: float = 0.0,
    am_largest_gap_sec: float = 0.0,
    pm_largest_gap_sec: float = 0.0,
    lunch_gap_sec: float = 0.0,
    heartbeat_at: Any = None,
    raw_row_count: Optional[int] = None,
    seal_row_count: Optional[int] = None,
    stale_or_silence: bool = False,
    session_mixing: bool = False,
    duplicate_key_count: int = 0,
    timestamp_regression_count: int = 0,
) -> dict[str, Any]:
    first = _parse_ts(first_event_at)
    last = _parse_ts(last_event_at)
    hb = _parse_ts(heartbeat_at)

    expected_start = _day_dt(trading_date, time(8, 50))
    expected_end = _day_dt(trading_date, PM_END)
    finalize_at = _day_dt(trading_date, FINALIZE)

    coverage_am = bool(
        first is not None
        and last is not None
        and first <= _day_dt(trading_date, AM_START) + timedelta(minutes=10)
        and last >= _day_dt(trading_date, time(11, 0))
    )
    coverage_pm = bool(
        first is not None
        and last is not None
        and first <= _day_dt(trading_date, PM_START) + timedelta(minutes=15)
        and last >= expected_end - timedelta(minutes=5)
    )

    heartbeat_until_finalize = bool(hb is not None and hb >= finalize_at - timedelta(seconds=30))
    raw_vs_seal = True
    if raw_row_count is not None and seal_row_count is not None:
        raw_vs_seal = int(raw_row_count) == int(seal_row_count)

    reg_ok = int(registration_symbol_count) >= int(expected_registration_symbols)
    dropped = int(dropped_event_count or 0)
    # Lunch is not an anomaly — ignore lunch_gap_sec for unexplained gap.
    unexplained = max(float(am_largest_gap_sec or 0), float(pm_largest_gap_sec or 0))
    if unexplained <= 0:
        unexplained = float(largest_gap_sec or 0)

    reasons: list[str] = []
    if dropped > 0:
        reasons.append(f"dropped_event_count={dropped}")
    if int(raw_row_count or 0) == 0 and first is None:
        reasons.append("no_data")
    if not coverage_am:
        reasons.append("coverage_am=false")
    if not coverage_pm:
        reasons.append("coverage_pm=false")
    if last is not None and last.time() < EARLY_END:
        reasons.append(f"early_last_event={last.isoformat(timespec='seconds')}")
    if unexplained > 600:
        reasons.append(f"unexplained_gap_sec={unexplained}")
    if not reg_ok and int(registration_symbol_count or 0) > 0:
        reasons.append(f"registration_coverage={registration_symbol_count}")
    if not raw_vs_seal:
        reasons.append("raw_vs_seal_row_mismatch")
    if stale_or_silence:
        reasons.append("stale_or_push_silence")
    if session_mixing:
        reasons.append("session_mixing")
    if int(duplicate_key_count or 0) > 0:
        reasons.append(f"duplicate_key_count={duplicate_key_count}")
    if int(timestamp_regression_count or 0) > 0:
        reasons.append(f"timestamp_regression_count={timestamp_regression_count}")

    if dropped > 0:
        status = DQ_BLOCKED
    elif int(raw_row_count or 0) == 0 and first is None:
        status = NO_DATA
    elif session_mixing or int(duplicate_key_count or 0) > 1000:
        status = CORRUPTED_CAPTURE
    elif coverage_am and coverage_pm and not reasons:
        status = COMPLETE_CAPTURE
    else:
        status = PARTIAL_CAPTURE

    # Day seal_pass only for COMPLETE; partial windows still research-usable.
    seal_pass = status == COMPLETE_CAPTURE and dropped == 0 and raw_vs_seal
    research_day_complete = seal_pass
    research_windows_allowed = status in (COMPLETE_CAPTURE, PARTIAL_CAPTURE, CORRUPTED_CAPTURE)

    return {
        "expected_start_at": expected_start.isoformat(timespec="seconds"),
        "actual_first_event_at": first.isoformat(timespec="milliseconds") if first else None,
        "expected_end_at": expected_end.isoformat(timespec="seconds"),
        "actual_last_event_at": last.isoformat(timespec="milliseconds") if last else None,
        "coverage_am": coverage_am,
        "coverage_pm": coverage_pm,
        "am_largest_gap_sec": float(am_largest_gap_sec or 0.0),
        "pm_largest_gap_sec": float(pm_largest_gap_sec or 0.0),
        "lunch_gap_sec": float(lunch_gap_sec or 0.0),
        "largest_gap_sec": unexplained,
        "heartbeat_until_finalize": heartbeat_until_finalize,
        "registration_coverage": int(registration_symbol_count or 0),
        "upstream_disconnect_count": int(disconnect_count or 0),
        "upstream_reconnect_count": int(reconnect_count or 0),
        "upstream_recovery_success_count": int(recovery_success_count or 0),
        "disconnect_count": int(disconnect_count or 0),
        "reconnect_success": int(reconnect_count or 0),
        "dropped_event_count": dropped,
        "raw_vs_seal_row_match": raw_vs_seal,
        "session_mixing": bool(session_mixing),
        "duplicate_key_count": int(duplicate_key_count or 0),
        "timestamp_regression_count": int(timestamp_regression_count or 0),
        "stale_or_silence": bool(stale_or_silence),
        "reasons": reasons,
        "status": status,
        "label": status,
        "research_adoptable": research_day_complete,
        "research_windows_allowed": research_windows_allowed,
        "seal_pass": seal_pass,
    }
