"""Phase 1 self-checks: timestamp, UTC/JST, Bid/Ask, missing side, duplicates, gaps, restricted dates."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from research.causal_driver_pb1.contracts.enums import QualityStatus
from research.causal_driver_pb1.contracts.errors import CausalTimeError
from research.causal_driver_pb1.contracts.source import SourceIdentity
from research.causal_driver_pb1.contracts.time import UTC, bar_start_available_at, decide, jst, to_jst
from research.causal_driver_pb1.phase1.bars import pair_raw_rows, synthetic_side
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase1.gaps import classify_gaps, classify_missing_minute
from research.causal_driver_pb1.phase1.map_obs import map_bar
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed


def _src() -> SourceIdentity:
    return SourceIdentity(
        source_id="DUKASCOPY_JETTA_1M",
        provider="DUKASCOPY_JETTA",
        instrument="USDJPY",
        resolution="1m",
        timezone="UTC",
        timestamp_semantics="BAR_START",
        source_path_or_endpoint_identity="synthetic://phase1_checks",
        inventory_sha256="0" * 64,
    )


def _pair_one(*, ts_utc_ms: int, bid: tuple[float, float, float, float], ask: tuple[float, float, float, float] | None) -> dict[str, Any]:
    bid_rows = [synthetic_side(ts_utc_ms=ts_utc_ms, open_=bid[0], high=bid[1], low=bid[2], close=bid[3], side="BID")]
    ask_rows = []
    if ask is not None:
        ask_rows = [synthetic_side(ts_utc_ms=ts_utc_ms, open_=ask[0], high=ask[1], low=ask[2], close=ask[3], side="ASK")]
    return pair_raw_rows(bid_rows=bid_rows, ask_rows=ask_rows, source_id="DUKASCOPY_JETTA_1M")


def timestamp_checks() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    bar = jst(2024, 9, 17, 9, 30, 0)
    available = bar_start_available_at(bar)
    u1 = available == jst(2024, 9, 17, 9, 31, 0)
    rows.append({"name": "U1", "pass": u1, "bar_start": "09:30", "available_at": "09:31"})
    packed = _pair_one(
        ts_utc_ms=int(bar.astimezone(UTC).timestamp() * 1000),
        bid=(150.0, 150.0, 150.0, 150.0),
        ask=(150.01, 150.01, 150.01, 150.01),
    )
    obs = map_bar(packed["bars"][0], _src())
    u1b = obs.event_time == bar and obs.available_at == available
    rows.append({"name": "U1_mapped", "pass": u1b, "event_time": obs.event_time.isoformat(), "available_at": obs.available_at.isoformat()})
    reject_ok = False
    try:
        decide(available_at=obs.available_at, decision_time=jst(2024, 9, 17, 9, 30, 59))
    except CausalTimeError:
        reject_ok = True
    usable_reject = obs.usable_at(jst(2024, 9, 17, 9, 30, 59)) is False
    rows.append({"name": "U2", "pass": reject_ok and usable_reject, "decision": "09:30:59", "result": "REJECT"})
    accept_ok = decide(available_at=obs.available_at, decision_time=jst(2024, 9, 17, 9, 31, 0)) == "ACCEPT"
    usable_accept = obs.usable_at(jst(2024, 9, 17, 9, 31, 0)) is True
    rows.append({"name": "U3", "pass": accept_ok and usable_accept, "decision": "09:31:00", "result": "ACCEPT"})
    utc_bar = datetime(2024, 9, 16, 15, 0, 0, tzinfo=UTC)
    jst_bar = to_jst(utc_bar, field="event_time")
    avail = bar_start_available_at(jst_bar)
    wrap = jst_bar == jst(2024, 9, 17, 0, 0, 0) and avail == jst(2024, 9, 17, 0, 1, 0)
    rows.append(
        {
            "name": "UTC_JST_DATE_WRAP",
            "pass": wrap,
            "utc": utc_bar.isoformat(),
            "jst": jst_bar.isoformat(),
            "available_at": avail.isoformat(),
        }
    )
    utc_bar2 = datetime(2025, 3, 9, 15, 0, 0, tzinfo=UTC)
    jst_bar2 = to_jst(utc_bar2, field="event_time")
    dst_note = jst_bar2.utcoffset().total_seconds() == 9 * 3600
    rows.append({"name": "JST_FIXED_PLUS9", "pass": dst_note, "offset_hours": 9})
    return {"pass": all(r["pass"] for r in rows), "rows": rows}


def bidask_checks() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    ts = int(jst(2024, 9, 17, 9, 30, 0).astimezone(UTC).timestamp() * 1000)
    ok = _pair_one(ts_utc_ms=ts, bid=(150.0, 150.0, 150.0, 150.0), ask=(150.01, 150.01, 150.01, 150.01))
    bar_ok = ok["bars"][0]
    rows.append(
        {
            "name": "BID_ASK_SANE",
            "pass": bar_ok.quality_status == QualityStatus.VALID and bar_ok.spread_close is not None and abs(bar_ok.spread_close - 0.01) < 1e-8,
            "quality": bar_ok.quality_status.value,
            "spread": bar_ok.spread_close,
        }
    )
    bad = _pair_one(ts_utc_ms=ts, bid=(150.02, 150.02, 150.02, 150.02), ask=(150.01, 150.01, 150.01, 150.01))
    bar_bad = bad["bars"][0]
    rows.append(
        {
            "name": "ASK_LT_BID",
            "pass": bar_bad.quality_status == QualityStatus.REJECTED,
            "quality": bar_bad.quality_status.value,
            "reason": bar_bad.quality_reason,
        }
    )
    missing = _pair_one(ts_utc_ms=ts, bid=(150.0, 150.0, 150.0, 150.0), ask=None)
    bar_m = missing["bars"][0]
    src = _src()
    obs_m = map_bar(bar_m, src)
    rows.append(
        {
            "name": "MISSING_ASK_NOT_VALID",
            "pass": bar_m.quality_status == QualityStatus.DEGRADED and obs_m.usable_at(bar_m.available_at) is False,
            "quality": bar_m.quality_status.value,
        }
    )
    bid_a = synthetic_side(ts_utc_ms=ts, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID")
    bid_b = synthetic_side(ts_utc_ms=ts, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID")
    ask = synthetic_side(ts_utc_ms=ts, open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK")
    ident = pair_raw_rows(bid_rows=[bid_a, bid_b], ask_rows=[ask], source_id="DUKASCOPY_JETTA_1M")
    rows.append(
        {
            "name": "IDENTICAL_DUPLICATE_AUDITED",
            "pass": ident["identical_duplicate_n"] >= 1 and ident["conflicting_duplicate_n"] == 0,
            "identical_duplicate_n": ident["identical_duplicate_n"],
        }
    )
    bid_c = synthetic_side(ts_utc_ms=ts, open_=151.0, high=151.0, low=151.0, close=151.0, side="BID")
    conf = pair_raw_rows(bid_rows=[bid_a, bid_c], ask_rows=[ask], source_id="DUKASCOPY_JETTA_1M")
    rows.append(
        {
            "name": "CONFLICTING_DUPLICATE_BLOCK",
            "pass": conf["conflicting_duplicate_n"] >= 1,
            "conflicting_duplicate_n": conf["conflicting_duplicate_n"],
        }
    )
    return {"pass": all(r["pass"] for r in rows), "rows": rows}


def gap_checks() -> dict[str, Any]:
    # Friday 2024-09-20 16:59 NY = 20:59 UTC (EDT UTC-4). Sunday 17:01 NY = 21:01 UTC.
    fri = datetime(2024, 9, 20, 20, 59, 0, tzinfo=UTC)
    sun = datetime(2024, 9, 22, 21, 0, 0, tzinfo=UTC)
    fri_ms = int(fri.timestamp() * 1000)
    sun_ms = int(sun.timestamp() * 1000)
    mid = fri_ms + 60_000
    weekend_label = classify_missing_minute(mid)
    bid_rows = [
        synthetic_side(ts_utc_ms=fri_ms, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID"),
        synthetic_side(ts_utc_ms=sun_ms, open_=150.0, high=150.0, low=150.0, close=150.0, side="BID"),
    ]
    ask_rows = [
        synthetic_side(ts_utc_ms=fri_ms, open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK"),
        synthetic_side(ts_utc_ms=sun_ms, open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK"),
    ]
    packed = pair_raw_rows(bid_rows=bid_rows, ask_rows=ask_rows, source_id="DUKASCOPY_JETTA_1M")
    gaps = classify_gaps(packed["bars"])
    weekday_a = datetime(2024, 9, 17, 1, 0, 0, tzinfo=UTC)
    weekday_b = datetime(2024, 9, 17, 1, 5, 0, tzinfo=UTC)
    miss = classify_missing_minute(int(weekday_a.timestamp() * 1000) + 60_000)
    bid2 = [
        synthetic_side(ts_utc_ms=int(weekday_a.timestamp() * 1000), open_=150.0, high=150.0, low=150.0, close=150.0, side="BID"),
        synthetic_side(ts_utc_ms=int(weekday_b.timestamp() * 1000), open_=150.0, high=150.0, low=150.0, close=150.0, side="BID"),
    ]
    ask2 = [
        synthetic_side(ts_utc_ms=int(weekday_a.timestamp() * 1000), open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK"),
        synthetic_side(ts_utc_ms=int(weekday_b.timestamp() * 1000), open_=150.01, high=150.01, low=150.01, close=150.01, side="ASK"),
    ]
    packed2 = pair_raw_rows(bid_rows=bid2, ask_rows=ask2, source_id="DUKASCOPY_JETTA_1M")
    gaps2 = classify_gaps(packed2["bars"])
    rows = [
        {"name": "WEEKEND_NOT_UNEXPECTED", "pass": weekend_label == "EXPECTED_CLOSED" and gaps["unexpected_gap_n"] == 0, "class": weekend_label},
        {"name": "WEEKDAY_HOLE_UNEXPECTED", "pass": miss == "UNEXPECTED_MISSING" and gaps2["unexpected_gap_n"] >= 1, "class": miss},
    ]
    return {"pass": all(r["pass"] for r in rows), "rows": rows}


def restricted_date_checks() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for day, tag in (("20260422", "FROZEN_VALIDATION"), ("20260924", "PROSPECTIVE")):
        denied = False
        reason = ""
        try:
            assert_ingest_date_allowed(day)
        except IngestDateDenied as exc:
            denied = True
            reason = str(exc)
        rows.append({"name": f"DENY_{tag}", "pass": denied, "date": day, "reason": reason})
    return {"pass": all(r["pass"] for r in rows), "rows": rows}
