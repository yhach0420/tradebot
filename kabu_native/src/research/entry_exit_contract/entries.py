"""EC1 / EC2 / EC3 ENTRY detection with frozen contracts (no future leakage)."""
from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from research.entry_exit_contract.constants import CONTRACT_VERSION, DEFAULT_THRESHOLDS, NATIVE, PUSH_CACHE
from research.entry_exit_contract.contract import EntryContract
from research.volume_confirmed_impulse_entry.features import _Prefix, _features_fast, aggregate_to_seconds
from research.volume_confirmed_impulse_entry.push_loader import PushTick

JST = ZoneInfo("Asia/Tokyo")


def load_push_day(day: str, native: Path = NATIVE) -> dict[str, list[PushTick]]:
    path = (native / "results" / "research" / "volume_confirmed_impulse_entry" / "_push_cache" / f"{day}_push.pkl")
    if not path.is_file():
        path = PUSH_CACHE / f"{day}_push.pkl"
    with path.open("rb") as fh:
        by, _st = pickle.load(fh)
    return by


def _session(t: datetime) -> str:
    return "AM" if t.hour < 12 else "PM"


def _qualities(bar: PushTick, feat_ok: bool) -> tuple[str, str, str, str]:
    vol_q = "OK" if bar.volume_delta is not None and not bar.dq_volume_reset else "NOT_EVALUABLE"
    quote_q = "OK" if bar.bid is not None and bar.ask is not None else "NOT_EVALUABLE"
    side_q = str(bar.trade_side_quality or "NOT_EVALUABLE")
    src = "PUSH_CACHE" if feat_ok else "NOT_EVALUABLE"
    return src, quote_q, vol_q, side_q


def _hold_above(bars: Sequence[PushTick], i: int, level: float, hold_sec: float) -> tuple[bool, int]:
    t0 = bars[i].event_time
    for j in range(i, min(len(bars), i + 40)):
        if bars[j].current_price <= level:
            return False, j
        if (bars[j].event_time - t0).total_seconds() >= hold_sec:
            return True, j
    return False, i


def detect_ec1(bars: Sequence[PushTick], *, day: str, thr: dict[str, float], step: int = 2) -> list[EntryContract]:
    if len(bars) < 50:
        return []
    p = _Prefix(list(bars))
    out: list[EntryContract] = []
    last_ep: Optional[datetime] = None
    for i in range(40, len(bars) - 1, step):
        cur = bars[i]
        prev = bars[i - 1]
        feat = _features_fast(p, i)
        if not feat.ok:
            continue
        v = feat.values
        bl = v.get("micro_high_60s") or v.get("range_high_120s")
        if bl is None:
            continue
        # true cross: previous <= level < current (level excludes current by construction)
        if not (prev.current_price <= bl < cur.current_price):
            continue
        vi10 = v.get("volume_impulse_10s")
        vi30 = v.get("volume_impulse_30s")
        if vi10 is None and vi30 is None:
            continue
        if not ((vi10 is not None and vi10 >= thr["vol_impulse_10s"]) or (vi30 is not None and vi30 >= thr["vol_impulse_30s"])):
            continue
        ur = v.get("uptick_volume_ratio_10s") or v.get("uptick_volume_ratio_30s")
        if ur is not None and ur < thr["uptick_min"]:
            continue
        if cur.trade_side_quality in ("", None) and ur is None:
            continue
        sc = v.get("spread_change_30s")
        if sc is not None and sc > thr["max_spread_change_bps"]:
            continue
        if (v.get("chase_overheat") or 0) >= 1.0:
            continue
        ta = v.get("tick_acceleration_10s")
        if ta is not None and ta < 1.0:
            continue
        ok_hold, entry_i = _hold_above(bars, i, float(bl), thr["hold_sec"])
        if not ok_hold:
            continue
        # impulse age: require recent impulse (approx: volume impulse present at cross)
        entry_bar = bars[entry_i]
        if last_ep and (entry_bar.event_time - last_ep).total_seconds() < 60:
            continue
        eid = uuid4().hex[:10]
        src, qq, vq, sq = _qualities(entry_bar, True)
        snap = {k: (float(x) if isinstance(x, (int, float)) else None) for k, x in v.items()}
        snap["breakout_level"] = float(bl)
        c = EntryContract(
            strategy_id="EC1",
            contract_version=CONTRACT_VERSION,
            symbol=entry_bar.symbol,
            day=day,
            session=_session(entry_bar.event_time),
            entry_signal_time=cur.event_time,
            entry_time=entry_bar.event_time,
            entry_price=float(entry_bar.current_price),
            entry_reason="volume_breakout_true_cross",
            entry_feature_snapshot=snap,
            expected_market_path="new_high_within_horizon_above_breakout",
            expected_horizon_sec=90.0,
            invalidation_level=float(bl),
            invalidation_reason_definition="price_below_breakout_level_no_recover_5s",
            hold_condition_definition="above_breakout_and_impulse_alive",
            profit_exit_definition="EC1-X2_impulse_decay|EC1-X3_volume_exhaustion|EC1-X4_flow_trailing",
            emergency_exit_definition="hard_stop|session_close|data_stale",
            setup_id=eid,
            episode_id=f"EC1:{entry_bar.symbol}:{float(bl):.4f}:{entry_bar.event_time.strftime('%H%M%S')}",
            source_quality=src,
            quote_quality=qq,
            volume_quality=vq,
            trade_side_quality=sq,
            levels={"breakout_level": float(bl), "entry_price": float(entry_bar.current_price)},
        )
        out.append(c)
        last_ep = entry_bar.event_time
    return out


def detect_ec2(bars: Sequence[PushTick], *, day: str, thr: dict[str, float], step: int = 2) -> list[EntryContract]:
    if len(bars) < 80:
        return []
    p = _Prefix(list(bars))
    out: list[EntryContract] = []
    last_ep: Optional[datetime] = None
    for i in range(80, len(bars) - 1, step):
        cur = bars[i]
        prev = bars[i - 1]
        feat = _features_fast(p, i)
        if not feat.ok:
            continue
        v = feat.values
        # trend: 5–10 min rise via 300s / 180s slope proxy using 120s + prior
        rise120 = v.get("price_slope_120s")
        if rise120 is None or rise120 < thr["trend_rise_min_pct"]:
            continue
        # pullback_low = micro_low_60s (excludes current)
        pl = v.get("micro_low_60s")
        rh = v.get("micro_high_60s") or v.get("range_high_120s")
        if pl is None or rh is None or pl <= 0:
            continue
        pb_depth = (rh - pl) / rh * 100.0
        if pb_depth < thr["pullback_min_pct"] or pb_depth > thr["pullback_max_pct"]:
            continue
        # not free-fall
        if (v.get("accel_down") or 0) >= 1.0:
            continue
        reclaim = float(v.get("micro_high_30s") or rh)
        if not (prev.current_price <= reclaim < cur.current_price):
            continue
        ur = v.get("uptick_volume_ratio_30s")
        if ur is not None and ur < thr["uptick_min"]:
            continue
        sc = v.get("spread_change_30s")
        if sc is not None and sc > 20:
            continue
        ok_hold, entry_i = _hold_above(bars, i, reclaim, thr["hold_sec"])
        if not ok_hold:
            continue
        entry_bar = bars[entry_i]
        if last_ep and (entry_bar.event_time - last_ep).total_seconds() < 90:
            continue
        # exclude momentum-low chase: price still near lows without reclaim volume
        if cur.current_price <= pl * 1.001:
            continue
        eid = uuid4().hex[:10]
        src, qq, vq, sq = _qualities(entry_bar, True)
        snap = {k: (float(x) if isinstance(x, (int, float)) else None) for k, x in v.items()}
        # approximate VWAP as mid of range
        vwap = (float(rh) + float(pl)) / 2.0
        c = EntryContract(
            strategy_id="EC2",
            contract_version=CONTRACT_VERSION,
            symbol=entry_bar.symbol,
            day=day,
            session=_session(entry_bar.event_time),
            entry_signal_time=cur.event_time,
            entry_time=entry_bar.event_time,
            entry_price=float(entry_bar.current_price),
            entry_reason="pullback_reclaim_true_cross",
            entry_feature_snapshot=snap,
            expected_market_path="recover_toward_pre_pullback_high",
            expected_horizon_sec=float(thr["rebound_horizon_sec"]),
            invalidation_level=float(pl),
            invalidation_reason_definition="pullback_low_break_or_reclaim_fail",
            hold_condition_definition="above_pullback_low_and_reclaim_held",
            profit_exit_definition="EC2-X2_rebound_failure|EC2-X3_retest_failure|EC2-X4_rebound_trailing",
            emergency_exit_definition="hard_stop|session_close|data_stale",
            setup_id=eid,
            episode_id=f"EC2:{entry_bar.symbol}:{float(pl):.4f}:{entry_bar.event_time.strftime('%H%M%S')}",
            source_quality=src,
            quote_quality=qq,
            volume_quality=vq,
            trade_side_quality=sq,
            levels={
                "pullback_low": float(pl),
                "reclaim_level": float(reclaim),
                "pre_pullback_high": float(rh),
                "trend_reference": float(rh),
                "vwap": float(vwap),
                "expected_retest_level": float(rh),
            },
        )
        out.append(c)
        last_ep = entry_bar.event_time
    return out


def detect_ec3(bars: Sequence[PushTick], *, day: str, thr: dict[str, float], step: int = 2) -> list[EntryContract]:
    if len(bars) < 80:
        return []
    p = _Prefix(list(bars))
    out: list[EntryContract] = []
    last_ep: Optional[datetime] = None
    for i in range(80, len(bars) - 1, step):
        cur = bars[i]
        prev = bars[i - 1]
        feat = _features_fast(p, i)
        if not feat.ok:
            continue
        v = feat.values
        rh = v.get("range_high_120s")
        rl = v.get("range_low_120s")
        if rh is None or rl is None or rh <= rl:
            continue
        width = rh - rl
        # prior wider range: use 180s range if available
        rh180 = v.get("range_high_180s")
        rl180 = v.get("range_low_180s")
        if rh180 is None or rl180 is None:
            continue
        prior_w = rh180 - rl180
        if prior_w <= 0 or width / prior_w > thr["compress_ratio_max"]:
            continue
        # compression: volume not exploding inside range
        vi10 = v.get("volume_impulse_10s")
        if vi10 is not None and vi10 > 3.0 and prev.current_price < rh:
            # allow only at breakout bar
            pass
        if not (prev.current_price <= rh < cur.current_price):
            continue
        if vi10 is None or vi10 < thr["vol_impulse_10s"]:
            continue
        ur = v.get("uptick_volume_ratio_10s")
        if ur is not None and ur < thr["uptick_min"]:
            continue
        sc = v.get("spread_change_30s")
        if sc is not None and sc > 20:
            continue
        ok_hold, entry_i = _hold_above(bars, i, float(rh), thr["hold_sec"])
        if not ok_hold:
            continue
        # immediate reentry check: still above after hold
        if bars[entry_i].current_price <= rh:
            continue
        entry_bar = bars[entry_i]
        if last_ep and (entry_bar.event_time - last_ep).total_seconds() < 90:
            continue
        eid = uuid4().hex[:10]
        src, qq, vq, sq = _qualities(entry_bar, True)
        snap = {k: (float(x) if isinstance(x, (int, float)) else None) for k, x in v.items()}
        mid = (float(rh) + float(rl)) / 2.0
        c = EntryContract(
            strategy_id="EC3",
            contract_version=CONTRACT_VERSION,
            symbol=entry_bar.symbol,
            day=day,
            session=_session(entry_bar.event_time),
            entry_signal_time=cur.event_time,
            entry_time=entry_bar.event_time,
            entry_price=float(entry_bar.current_price),
            entry_reason="compression_breakout_true_cross",
            entry_feature_snapshot=snap,
            expected_market_path="range_expansion_above_range_high",
            expected_horizon_sec=120.0,
            invalidation_level=float(rh),
            invalidation_reason_definition="range_high_reentry_no_recover",
            hold_condition_definition="above_range_high_expansion_alive",
            profit_exit_definition="EC3-X3_expansion_decay|EC3-X4_range_expansion_trailing",
            emergency_exit_definition="hard_stop|session_close|data_stale",
            setup_id=eid,
            episode_id=f"EC3:{entry_bar.symbol}:{float(rh):.4f}:{entry_bar.event_time.strftime('%H%M%S')}",
            source_quality=src,
            quote_quality=qq,
            volume_quality=vq,
            trade_side_quality=sq,
            levels={
                "range_high": float(rh),
                "range_low": float(rl),
                "range_mid": float(mid),
                "range_width": float(width),
            },
        )
        out.append(c)
        last_ep = entry_bar.event_time
    return out


def build_ec_entries(
    days: Sequence[str],
    push_by_day: dict[str, dict[str, list[PushTick]]],
    thresholds: dict[str, dict[str, float]],
) -> dict[str, list[EntryContract]]:
    out: dict[str, list[EntryContract]] = {"EC1": [], "EC2": [], "EC3": []}
    for day in days:
        by = push_by_day.get(day) or {}
        print(f"[eec] detect entries day={day} symbols={len(by)}", flush=True)
        for sym, ticks in by.items():
            if len(ticks) < 40:
                continue
            bars = aggregate_to_seconds(ticks)
            if len(bars) < 50:
                continue
            out["EC1"].extend(detect_ec1(bars, day=day, thr=thresholds["EC1"]))
            out["EC2"].extend(detect_ec2(bars, day=day, thr=thresholds["EC2"]))
            out["EC3"].extend(detect_ec3(bars, day=day, thr=thresholds["EC3"]))
    for k in out:
        out[k].sort(key=lambda c: (c.day, c.entry_time, c.strategy_id, c.setup_id))
        print(f"[eec] {k} n={len(out[k])}", flush=True)
    return out
