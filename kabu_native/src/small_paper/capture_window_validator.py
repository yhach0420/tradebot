"""Per-trade/candidate capture window validation for research use of partial days."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

VALID_COMPLETE_WINDOW = "VALID_COMPLETE_WINDOW"
INVALID_LOOKBACK = "INVALID_LOOKBACK"
INVALID_ENTRY_PRICE = "INVALID_ENTRY_PRICE"
INVALID_EXIT_PRICE = "INVALID_EXIT_PRICE"
CROSSES_CAPTURE_GAP = "CROSSES_CAPTURE_GAP"
CROSSES_SESSION_DISCONTINUITY = "CROSSES_SESSION_DISCONTINUITY"
DATA_END_INCOMPLETE = "DATA_END_INCOMPLETE"
CORRUPTED_ORDER = "CORRUPTED_ORDER"
OTHER_DQ = "OTHER_DQ"


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        s = str(v).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


@dataclass
class WindowValidation:
    window_valid: bool
    invalid_reason: str
    classification: str
    lookback_start: str
    entry_time: str
    exit_time: str
    crosses_gap: bool
    crosses_session: bool
    raw_event_count: int
    max_internal_gap_sec: float
    entry_ask_valid: bool
    exit_bid_valid: bool
    feature_history_valid: bool
    outcome_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_trade_window(
    *,
    lookback_start: Any,
    entry_time: Any,
    exit_time: Any,
    event_times: Sequence[Any],
    entry_ask: Any = None,
    exit_bid: Any = None,
    gap_intervals: Sequence[tuple[Any, Any]] | None = None,
    session_boundaries: Sequence[Any] | None = None,
    exit_reason: str = "",
    max_internal_gap_sec: float = 120.0,
    require_feature_history: bool = True,
) -> WindowValidation:
    lb = _parse_ts(lookback_start)
    et = _parse_ts(entry_time)
    xt = _parse_ts(exit_time)
    times = sorted(t for t in (_parse_ts(x) for x in event_times) if t is not None)

    def _fail(cls: str, reason: str, **kw: Any) -> WindowValidation:
        base = dict(
            window_valid=False,
            invalid_reason=reason,
            classification=cls,
            lookback_start=lb.isoformat() if lb else "",
            entry_time=et.isoformat() if et else "",
            exit_time=xt.isoformat() if xt else "",
            crosses_gap=False,
            crosses_session=False,
            raw_event_count=len(times),
            max_internal_gap_sec=0.0,
            entry_ask_valid=False,
            exit_bid_valid=False,
            feature_history_valid=False,
            outcome_valid=False,
        )
        base.update(kw)
        return WindowValidation(**base)

    if et is None:
        return _fail(INVALID_ENTRY_PRICE, "missing_entry_time")
    if xt is None:
        return _fail(INVALID_EXIT_PRICE, "missing_exit_time")
    if xt < et:
        return _fail(CORRUPTED_ORDER, "exit_before_entry", entry_ask_valid=True)
    if str(exit_reason or "").upper() in ("DATA_END", "DATAEND"):
        return _fail(DATA_END_INCOMPLETE, "data_end_exit", outcome_valid=False)

    try:
        entry_ask_f = float(entry_ask) if entry_ask not in (None, "") else None
    except Exception:
        entry_ask_f = None
    try:
        exit_bid_f = float(exit_bid) if exit_bid not in (None, "") else None
    except Exception:
        exit_bid_f = None
    if entry_ask_f is None or entry_ask_f <= 0:
        return _fail(INVALID_ENTRY_PRICE, "invalid_entry_ask")
    if exit_bid_f is None or exit_bid_f <= 0:
        return _fail(INVALID_EXIT_PRICE, "invalid_exit_bid")

    if lb is None:
        lb = et - timedelta(seconds=60)
    in_window = [t for t in times if lb <= t <= xt]
    if require_feature_history and not any(t <= et for t in in_window):
        return _fail(INVALID_LOOKBACK, "no_events_before_entry", entry_ask_valid=True)

    # continuity inside window
    max_gap = 0.0
    for a, b in zip(in_window, in_window[1:]):
        gap = (b - a).total_seconds()
        if gap > max_gap:
            max_gap = gap
    if max_gap > float(max_internal_gap_sec):
        return _fail(
            CROSSES_CAPTURE_GAP,
            f"internal_gap_{max_gap:.1f}s",
            crosses_gap=True,
            max_internal_gap_sec=max_gap,
            entry_ask_valid=True,
            exit_bid_valid=True,
            feature_history_valid=True,
        )

    crosses_gap = False
    for gs, ge in gap_intervals or []:
        a, b = _parse_ts(gs), _parse_ts(ge)
        if a is None or b is None:
            continue
        if a <= et <= b or a <= xt <= b or (et <= a and xt >= b):
            crosses_gap = True
            break
    if crosses_gap:
        return _fail(
            CROSSES_CAPTURE_GAP,
            "crosses_known_gap",
            crosses_gap=True,
            max_internal_gap_sec=max_gap,
            entry_ask_valid=True,
            exit_bid_valid=True,
            feature_history_valid=True,
        )

    crosses_session = False
    for sb in session_boundaries or []:
        b = _parse_ts(sb)
        if b is None:
            continue
        if et < b < xt:
            crosses_session = True
            break
    if crosses_session:
        # Allow only if continuous events across boundary
        near = [t for t in in_window if abs((t - (_parse_ts(session_boundaries[0]) or et)).total_seconds()) < 5]
        if len(near) < 2:
            return _fail(
                CROSSES_SESSION_DISCONTINUITY,
                "session_boundary_discontinuity",
                crosses_session=True,
                max_internal_gap_sec=max_gap,
                entry_ask_valid=True,
                exit_bid_valid=True,
                feature_history_valid=True,
            )

    return WindowValidation(
        window_valid=True,
        invalid_reason="",
        classification=VALID_COMPLETE_WINDOW,
        lookback_start=lb.isoformat(),
        entry_time=et.isoformat(),
        exit_time=xt.isoformat(),
        crosses_gap=False,
        crosses_session=crosses_session,
        raw_event_count=len(in_window),
        max_internal_gap_sec=max_gap,
        entry_ask_valid=True,
        exit_bid_valid=True,
        feature_history_valid=True,
        outcome_valid=True,
    )
