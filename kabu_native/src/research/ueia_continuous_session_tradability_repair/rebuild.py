"""Rebuild continuous-session samples with session-scoped features and labels."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.ueia_continuous_session_tradability_repair.session import (
    classify_session,
    continuous_session_id,
    crosses_boundary,
    market_tradable,
    seconds_since_session_open,
    seconds_to_session_end,
    session_end_time,
)
from research.upward_edge_identification_audit.constants import (
    BARRIERS,
    MAX_REGULAR_PER_STREAM,
    MAX_STATE_PER_STREAM,
    REGULAR_SAMPLE_SEC,
    STATE_SAMPLE_MIN_GAP_SEC,
    WARMUP_SEC,
)
from research.upward_edge_identification_audit.features import FeatureEngine
from research.upward_edge_identification_audit.labels import LabelRow, _bps
from research.upward_edge_identification_audit.loader import Tick, exec_entry_ok
from research.upward_edge_identification_audit.constants import COST_BPS
from research.upward_edge_identification_audit.samples import Sample, _state_changed, build_day_context, _ctx_at


def label_first_passage_session(
    ticks: Sequence[Tick],
    i: int,
    sample_id: str,
    barrier_id: str,
    entry_ask: float,
    entry_bid: float,
    spread_bps: Optional[float],
) -> tuple[LabelRow, bool]:
    """First-passage limited to same continuous session. Returns (label, crossed_boundary_flag)."""
    spec = BARRIERS[barrier_id]
    up_bps, down_bps, horizon = spec["up_bps"], spec["down_bps"], spec["horizon_sec"]
    up_px = entry_ask * (1.0 + up_bps / 10000.0)
    down_px = entry_ask * (1.0 - down_bps / 10000.0)
    t0 = ticks[i].ts
    sess0 = continuous_session_id(t0)
    max_bid = min_bid = None
    last_bid = entry_bid
    events = 0
    result = "NEITHER"
    hit_time = None
    hit_sec = None
    data_complete = True
    crossed = False

    if sess0 is None:
        # should not sample here
        lab = LabelRow(
            sample_id=sample_id, barrier=barrier_id, entry_ask=entry_ask, entry_bid=entry_bid,
            entry_spread=spread_bps, up_barrier=up_px, down_barrier=down_px, horizon_sec=horizon,
            first_result="DATA_END", first_hit_time=None, first_hit_sec=None,
            max_future_bid=None, min_future_bid=None, MFE_bps=None, MAE_bps=None,
            terminal_return_bps=None, cost_adjusted_return_bps=None,
            events_observed=0, data_complete=False,
        )
        return lab, True

    for j in range(i + 1, len(ticks)):
        t = ticks[j]
        dt = (t.ts - t0).total_seconds()
        if dt > horizon:
            break
        if continuous_session_id(t.ts) != sess0:
            result = "DATA_END"
            data_complete = False
            crossed = True
            break
        bid = t.board.canonical_best_bid
        if bid is None or bid <= 0:
            continue
        events += 1
        last_bid = float(bid)
        max_bid = last_bid if max_bid is None else max(max_bid, last_bid)
        min_bid = last_bid if min_bid is None else min(min_bid, last_bid)
        up_hit = last_bid >= up_px
        down_hit = last_bid <= down_px
        if up_hit and down_hit:
            result = "BOTH_SAME_EVENT"
            hit_time, hit_sec = t.ts, dt
            break
        if up_hit:
            result = "UP_FIRST"
            hit_time, hit_sec = t.ts, dt
            break
        if down_hit:
            result = "DOWN_FIRST"
            hit_time, hit_sec = t.ts, dt
            break
    else:
        if (ticks[-1].ts - t0).total_seconds() < horizon:
            # may be end of data or session ended without more ticks
            end = session_end_time(t0)
            if end is not None and ticks[-1].ts >= end - __import__("datetime").timedelta(seconds=1):
                result = "DATA_END"
                data_complete = False
            elif continuous_session_id(ticks[-1].ts) != sess0:
                result = "DATA_END"
                data_complete = False
                crossed = True

    mfe = _bps(entry_ask, max_bid) if max_bid is not None else None
    mae = _bps(entry_ask, min_bid) if min_bid is not None else None
    term = _bps(entry_ask, last_bid) if last_bid else None
    cadj = (term - COST_BPS) if term is not None else None
    # Rename DATA_END at boundary for audit
    if result == "DATA_END" and crossed:
        # keep DATA_END for compatibility; flag separately
        pass
    lab = LabelRow(
        sample_id=sample_id, barrier=barrier_id, entry_ask=entry_ask, entry_bid=entry_bid,
        entry_spread=spread_bps, up_barrier=up_px, down_barrier=down_px, horizon_sec=horizon,
        first_result=result, first_hit_time=hit_time, first_hit_sec=hit_sec,
        max_future_bid=max_bid, min_future_bid=min_bid, MFE_bps=mfe, MAE_bps=mae,
        terminal_return_bps=term, cost_adjusted_return_bps=cadj,
        events_observed=events, data_complete=data_complete,
    )
    return lab, crossed


@dataclass
class SessionSample(Sample):
    session_state: str = ""
    market_tradable: bool = False
    crosses_session_boundary: dict[str, bool] = field(default_factory=dict)
    seconds_since_open: Optional[float] = None
    seconds_to_end: Optional[float] = None
    feature_ready: bool = False


def build_continuous_stream_samples(
    stream_key: str,
    ticks: list[Tick],
    day_ctx: dict,
    *,
    warmup_extra_sec: float = 0.0,
) -> list[SessionSample]:
    if len(ticks) < 50:
        return []
    day, symbol = stream_key.split("|", 1)
    eng: Optional[FeatureEngine] = None
    cur_sess: Optional[str] = None
    out: list[SessionSample] = []
    n_reg = n_state = 0
    last_reg_ts: Optional[datetime] = None
    last_state_ts: Optional[datetime] = None
    prev: Optional[Tick] = None
    sess_start_ts: Optional[datetime] = None

    for i, t in enumerate(ticks):
        st = classify_session(t.ts)
        cid = continuous_session_id(t.ts)

        # Only update features inside continuous sessions; reset on AM/PM entry
        if cid is None:
            prev = t
            continue
        if cid != cur_sess:
            eng = FeatureEngine()
            cur_sess = cid
            sess_start_ts = t.ts
            n_reg = n_state = 0
            last_reg_ts = last_state_ts = None
            prev = None

        assert eng is not None
        c = _ctx_at(day_ctx, t.ts, symbol)
        for k, v in c.items():
            setattr(eng, k, v)
        eng.update(t)

        # warmup: session-local WARMUP + optional extra
        if sess_start_ts is None:
            prev = t
            continue
        age = (t.ts - sess_start_ts).total_seconds()
        ready = age >= (WARMUP_SEC + warmup_extra_sec) and eng.warmed(t)
        if not ready or not exec_entry_ok(t) or not market_tradable(t.ts):
            prev = t
            continue

        ask = float(t.board.canonical_best_ask)
        bid = float(t.board.canonical_best_bid)
        if ask <= 0 or bid <= 0 or bid > ask:
            prev = t
            continue
        spr = t.board.canonical_spread_bps

        take_reg = False
        take_state = False
        if last_reg_ts is None or (t.ts - last_reg_ts).total_seconds() >= REGULAR_SAMPLE_SEC:
            if n_reg < MAX_REGULAR_PER_STREAM:
                take_reg = True
        if prev is not None and _state_changed(prev, t):
            if last_state_ts is None or (t.ts - last_state_ts).total_seconds() >= STATE_SAMPLE_MIN_GAP_SEC:
                if n_state < MAX_STATE_PER_STREAM:
                    take_state = True
        if not take_reg and not take_state:
            prev = t
            continue

        feats = eng.snapshot(t)
        types = []
        if take_reg:
            types.append("REGULAR")
            n_reg += 1
            last_reg_ts = t.ts
        if take_state:
            types.append("STATE_CHANGE")
            n_state += 1
            last_state_ts = t.ts

        for sty in types:
            sid = f"{day}|{symbol}|{t.event_seq}|{i}|{sty}|S1"
            sm = SessionSample(
                sample_id=sid, day=day, symbol=symbol, event_sequence=t.event_seq,
                event_time=t.ts, sample_type=sty, idx=i, entry_ask=ask, entry_bid=bid,
                spread_bps=spr, features=feats, stream_key=stream_key,
                session_state=st, market_tradable=True,
                seconds_since_open=seconds_since_session_open(t.ts),
                seconds_to_end=seconds_to_session_end(t.ts),
                feature_ready=True,
            )
            for bid_id in ("B2", "B4"):
                lab, crossed = label_first_passage_session(ticks, i, sid, bid_id, ask, bid, spr)
                sm.labels[bid_id] = lab
                sm.crosses_session_boundary[bid_id] = crossed
            out.append(sm)
        prev = t
    return out


def rebuild_all_continuous(
    streams: dict[str, list[Tick]],
    *,
    warmup_extra_sec: float = 0.0,
) -> list[SessionSample]:
    by_day: dict[str, dict[str, list[Tick]]] = defaultdict(dict)
    for key, ticks in streams.items():
        day, _ = key.split("|", 1)
        by_day[day][key] = ticks
    out: list[SessionSample] = []
    for day, smap in sorted(by_day.items()):
        print(f"[cs] rebuild day={day} warmup_extra={warmup_extra_sec}", flush=True)
        ctx = build_day_context(smap, day)
        for key, ticks in smap.items():
            out.extend(build_continuous_stream_samples(key, ticks, ctx, warmup_extra_sec=warmup_extra_sec))
    return out


def annotate_original_samples(samples: Sequence[Sample]) -> list[dict[str, Any]]:
    """Attach session metadata to original (S0) samples without mutating labels."""
    rows = []
    for s in samples:
        st = classify_session(s.event_time)
        # check if original B4 path would cross (using stored label times roughly via horizon)
        crossed = {}
        for b, lab in s.labels.items():
            # heuristic: if first_hit after session end or DATA_END near boundary
            crossed[b] = False
            if lab.first_result == "DATA_END":
                crossed[b] = True
            elif lab.first_hit_time is not None and crosses_boundary(s.event_time, lab.first_hit_time):
                crossed[b] = True
            else:
                # scan not available — flag preopen/lunch as invalid execution
                if st not in ("CONTINUOUS_AM", "CONTINUOUS_PM"):
                    crossed[b] = True
        rows.append({
            "sample": s,
            "session_state": st,
            "market_tradable": market_tradable(s.event_time),
            "crosses": crossed,
        })
    return rows
