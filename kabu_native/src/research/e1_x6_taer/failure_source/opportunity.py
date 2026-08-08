"""Executable opportunity envelope (oracle; not runtime EXIT)."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x6_fcrr.features import FeatureBuffer
from research.e1_x6_fcrr.replay import load_day_events
from research.e1_x6_provisional.cost_contract import post_cost_label_bps

from .precommit import FRESHNESS_MAX_SEC, HORIZONS, MAX_HOLD_SEC

JST = ZoneInfo("Asia/Tokyo")


def _session_of(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


@dataclass
class OppState:
    episode_id: str
    setup_type: str
    day: str
    session: str
    symbol: str
    entry_t: float
    entry_ask: Optional[float] = None
    entry_event_id: Optional[str] = None
    started: bool = False
    # per-horizon best/worst net bps
    best: dict[float, float] = field(default_factory=dict)
    worst: dict[float, float] = field(default_factory=dict)
    best_exit_time: Optional[float] = None
    best_exit_bid: Optional[float] = None
    best_net_300: Optional[float] = None
    adverse_before_best: float = 0.0
    _min_so_far: float = 0.0
    time_to_pos: Optional[float] = None
    time_to_p5: Optional[float] = None
    time_to_p10: Optional[float] = None
    first_touch_5_m10: Optional[str] = "NONE"
    first_touch_10_m15: Optional[str] = "NONE"
    path_event_count: int = 0
    path_complete: bool = False
    last_bid_t: Optional[float] = None

    def ensure_horizons(self) -> None:
        for h in HORIZONS:
            if h not in self.best:
                self.best[h] = float("-inf")
                self.worst[h] = float("inf")


def compute_opportunity_and_features(
    episodes: list[dict[str, Any]],
    events_by_day: dict[str, list],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Single pass per day: opportunity envelope + ENTRY-time feature snapshot."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for e in episodes:
        by_day[e["day"]].append(e)

    opp_rows: list[dict[str, Any]] = []
    feat_rows: list[dict[str, Any]] = []
    meta = {"days": {}, "entry_ask_source": "canonical_best_ask_at_or_after_entry_t"}

    for day in sorted(by_day):
        events = events_by_day[day]
        eps = sorted(by_day[day], key=lambda x: (float(x["entry_t"]), x["episode_id"]))
        # pending starts keyed by symbol
        pending: dict[str, list[OppState]] = defaultdict(list)
        active: dict[str, list[OppState]] = defaultdict(list)
        for e in eps:
            st = OppState(
                episode_id=e["episode_id"],
                setup_type=e["setup_type"],
                day=e["day"],
                session=e["session"],
                symbol=e["symbol"],
                entry_t=float(e["entry_t"]),
            )
            st.ensure_horizons()
            pending[e["symbol"]].append(st)

        bufs: dict[str, FeatureBuffer] = {}
        last_spread: dict[str, float] = {}
        feat_captured: dict[str, dict] = {}
        finished: dict[str, list[OppState]] = defaultdict(list)
        ep_by_id = {e["episode_id"]: e for e in eps}

        for idx, (t, sym, row) in enumerate(events):
            ts: datetime = row["ts"]
            sess = _session_of(ts)
            bid = float(row["bid"])
            ask = float(row["ask"])
            vwap = float(row["vwap"])
            vol = float(row["vol"])

            buf = bufs.get(sym)
            if buf is None:
                buf = FeatureBuffer()
                bufs[sym] = buf
            buf.push(t, bid, ask, vwap, vol)
            age = buf.age(t)
            fresh = age <= FRESHNESS_MAX_SEC + 1e-9

            # start pending entries at first same-symbol event with t >= entry_t and fresh ask
            still = []
            for st in pending[sym]:
                if t + 1e-12 < st.entry_t:
                    still.append(st)
                    continue
                if sess != st.session:
                    # crossed session before entry fill → not evaluable start later
                    still.append(st)
                    continue
                if not fresh:
                    still.append(st)
                    continue
                st.entry_ask = ask
                st.entry_event_id = f"{day}|{sym}|{idx}"
                st.started = True
                # capture features at entry decision time (asof <= t)
                snap = buf.snapshot(t)
                feat_captured[st.episode_id] = _build_entry_features(
                    ep_by_id[st.episode_id], snap, buf, t, last_spread.get(sym)
                )
                active[sym].append(st)
            pending[sym] = still

            if not fresh:
                last_spread[sym] = (ask - bid) / ((ask + bid) / 2.0) * 10000.0 if ask + bid > 0 else None
                continue

            # update active same-session paths
            remain = []
            finished_sym = finished.setdefault(sym, [])
            for st in active[sym]:
                if sess != st.session or day != st.day:
                    st.path_complete = True
                    finished_sym.append(st)
                    continue
                if st.entry_ask is None or st.entry_ask <= 0:
                    remain.append(st)
                    continue
                dt = t - st.entry_t
                if dt < -1e-12:
                    remain.append(st)
                    continue
                if dt > MAX_HOLD_SEC + 1e-9:
                    st.path_complete = True
                    finished_sym.append(st)
                    continue
                net = post_cost_label_bps(st.entry_ask, bid)
                st.path_event_count += 1
                st.last_bid_t = t
                st._min_so_far = min(st._min_so_far, net)
                for h in HORIZONS:
                    if dt <= h + 1e-12:
                        if net > st.best[h]:
                            st.best[h] = net
                            if h == 300.0:
                                st.best_exit_time = t
                                st.best_exit_bid = bid
                                st.best_net_300 = net
                                st.adverse_before_best = st._min_so_far
                        if net < st.worst[h]:
                            st.worst[h] = net
                if st.time_to_pos is None and net > 0:
                    st.time_to_pos = dt
                if st.time_to_p5 is None and net >= 5.0 - 1e-12:
                    st.time_to_p5 = dt
                if st.time_to_p10 is None and net >= 10.0 - 1e-12:
                    st.time_to_p10 = dt
                if st.first_touch_5_m10 == "NONE":
                    if net >= 5.0 - 1e-12:
                        st.first_touch_5_m10 = "PLUS_5"
                    elif net <= -10.0 + 1e-12:
                        st.first_touch_5_m10 = "MINUS_10"
                if st.first_touch_10_m15 == "NONE":
                    if net >= 10.0 - 1e-12:
                        st.first_touch_10_m15 = "PLUS_10"
                    elif net <= -15.0 + 1e-12:
                        st.first_touch_10_m15 = "MINUS_15"
                remain.append(st)
            active[sym] = remain

            sp = (ask - bid) / ((ask + bid) / 2.0) * 10000.0 if ask + bid > 0 else None
            if sp is not None:
                last_spread[sym] = sp

        # finalize day
        all_states: list[OppState] = []
        for sym in set(list(pending) + list(active) + list(finished)):
            all_states.extend(pending[sym])
            all_states.extend(active[sym])
            all_states.extend(finished[sym])
        day_n_started = 0
        for st in all_states:
            if st.started and st.entry_ask is not None:
                day_n_started += 1
                # if never got path events but started, still emit
                if st.path_event_count > 0 and (st.last_bid_t is not None):
                    if (st.last_bid_t - st.entry_t) >= MAX_HOLD_SEC - 1.0 or st.path_complete:
                        st.path_complete = True
                row = _opp_row(st)
                opp_rows.append(row)
                fr = feat_captured.get(st.episode_id)
                if fr is not None:
                    fr = {
                        **fr,
                        "episode_id": st.episode_id,
                        "overlap_cluster_id": ep_by_id[st.episode_id].get("overlap_cluster_id"),
                        "is_cluster_representative": ep_by_id[st.episode_id].get("is_cluster_representative"),
                        "cluster_size": ep_by_id[st.episode_id].get("cluster_size"),
                        "cluster_weight": ep_by_id[st.episode_id].get("cluster_weight"),
                        "setup_type": st.setup_type,
                        "day": st.day,
                        "session": st.session,
                        "symbol": st.symbol,
                        "scenario_id_prior": ep_by_id[st.episode_id].get("scenario_id_prior"),
                    }
                    feat_rows.append(fr)
            else:
                # never got a valid entry ask
                opp_rows.append({
                    "episode_id": st.episode_id,
                    "setup_type": st.setup_type,
                    "day": st.day,
                    "session": st.session,
                    "symbol": st.symbol,
                    "entry_time": st.entry_t,
                    "entry_price": None,
                    "path_complete": False,
                    "evaluable": False,
                    "reason": "NO_ENTRY_ASK",
                    **{f"best_net_pnl_bps_{int(h)}s": None for h in HORIZONS},
                    **{f"worst_net_pnl_bps_{int(h)}s": None for h in HORIZONS},
                })
        meta["days"][day] = {"n_episodes": len(eps), "n_started": day_n_started, "n_events": len(events)}

    # attach cluster fields to opp from episodes
    ep_idx = {e["episode_id"]: e for e in episodes}
    for r in opp_rows:
        e = ep_idx.get(r["episode_id"]) or {}
        r["overlap_cluster_id"] = e.get("overlap_cluster_id")
        r["is_cluster_representative"] = e.get("is_cluster_representative")
        r["cluster_size"] = e.get("cluster_size")
        r["cluster_weight"] = e.get("cluster_weight")
        r["scenario_id_prior"] = e.get("scenario_id_prior")
    return opp_rows, feat_rows, meta


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(float(x))


def _build_entry_features(ep: dict, snap: dict, buf: FeatureBuffer, t: float, prior_spread: Optional[float]) -> dict:
    setup_f = (ep.get("setup_detail") or {}).get("features") or {}
    anchor = ep.get("anchor") or {}
    atr = snap.get("atr_180s")
    mid = snap.get("mid")
    ask = snap.get("ask")
    ref = anchor.get("reference_high")
    cross_mag = None
    if _finite(ask) and _finite(ref) and float(ref) > 0:
        cross_mag = (float(ask) - float(ref)) / float(ref) * 10000.0

    # extra returns/slopes from buffer window helpers via snapshot fields + derived
    # FeatureBuffer snapshot already has ret_15/30/180; compute 10/60 via internal if complete
    pre10 = pre60 = slope30 = slope60 = None
    vol60 = pu30 = None
    bid_updates = None
    if snap.get("complete") and buf.ticks:
        # reuse snapshot sub-logic lightly
        pre10 = _ret(buf, t, 10.0)
        pre60 = _ret(buf, t, 60.0)
        slope30 = _slope(buf, t, 30.0)
        slope60 = _slope(buf, t, 60.0)
        vol60 = _vol(buf, t, 60.0)
        pu30 = _pu(buf, t, 30.0)
        bid_updates = _bid_updates(buf, t, 30.0)

    vol10 = snap.get("volume_10s")
    vol30 = snap.get("volume_30s")
    med10 = snap.get("median_active_volume_10s_120s")
    med30 = snap.get("median_active_volume_30s_300s")
    impulse = (float(vol10) / float(med10)) if _finite(vol10) and _finite(med10) and float(med10) > 0 else None
    persist = (float(vol30) / float(med30)) if _finite(vol30) and _finite(med30) and float(med30) > 0 else None
    d15 = snap.get("down_tick_volume_ratio_15s")
    d60 = snap.get("down_tick_volume_ratio_60s")
    downtick_dec = (float(d15) / float(d60)) if _finite(d15) and _finite(d60) and float(d60) > 0 else None
    pie = None
    if _finite(snap.get("ret_30s")) and _finite(vol30) and float(vol30) > 0:
        pie = float(snap["ret_30s"]) / float(vol30)

    spread = snap.get("spread_bps")
    spread_change = None
    if _finite(spread) and _finite(prior_spread):
        spread_change = float(spread) - float(prior_spread)

    # bid_support numeric: 1 if bid near ref
    bid_support = None
    if _finite(snap.get("bid")) and _finite(ref) and _finite(anchor.get("tick")):
        bid_support = 1.0 if float(snap["bid"]) >= float(ref) - float(anchor["tick"]) - 1e-12 else 0.0

    pu10 = snap.get("price_update_count_10s")
    pu_med = snap.get("median_price_update_count_10s_120s")
    upd_acc = (float(pu10) / float(pu_med)) if _finite(pu10) and _finite(pu_med) and float(pu_med) > 0 else None

    setup_code = 0 if ep["setup_type"] == "PULLBACK_RECLAIM" else 1
    ak = str(anchor.get("anchor_kind") or "")
    anchor_code = 0 if ak == "MICRO_HIGH" else 1

    age = buf.age(t) if buf.ticks else None

    feats = {
        "setup_type_code": setup_code,
        "anchor_type_code": anchor_code,
        "range_width_atr": setup_f.get("range_width_atr"),
        "range_duration_sec": setup_f.get("range_duration_sec"),
        "pullback_depth_atr": setup_f.get("pullback_depth_atr"),
        "pullback_duration_sec": setup_f.get("pullback_duration_sec"),
        "high_test_count": setup_f.get("high_test_count"),
        "cross_magnitude_bps": cross_mag,
        "distance_from_vwap_atr": snap.get("distance_above_vwap"),
        "distance_from_session_high_atr": snap.get("distance_from_session_high"),
        "pre_cross_return_10s": pre10,
        "pre_cross_return_30s": snap.get("ret_30s"),
        "pre_cross_return_60s": pre60,
        "pre_cross_slope_30s": slope30,
        "pre_cross_slope_60s": slope60,
        "pre_cross_acceleration": (float(slope30) - float(slope60)) if _finite(slope30) and _finite(slope60) else None,
        "volume_10s": vol10,
        "volume_30s": vol30,
        "volume_60s": vol60,
        "volume_impulse_ratio": impulse,
        "volume_persistence": persist,
        "uptick_volume_ratio_10s": snap.get("uptick_volume_ratio_10s"),
        "uptick_volume_ratio_30s": snap.get("uptick_volume_ratio_30s"),
        "downtick_deceleration": downtick_dec,
        "price_impact_efficiency": pie,
        "spread_bps": spread,
        "spread_change": spread_change,
        "bid_support": bid_support,
        "best_bid_update_count": bid_updates,
        "ask_replenishment": None,  # depth not in feed
        "imbalance": None,  # depth not in feed
        "price_update_count_10s": pu10,
        "price_update_count_30s": pu30,
        "update_acceleration": upd_acc,
        "event_freshness": age,
        "board_freshness": age,
        "trade_side_quality_code": 1 if snap.get("trade_side_quality") == "TICK_RULE_INFERRED" else 0,
        "snapshot_complete": bool(snap.get("complete")),
        "snapshot_reason": snap.get("reason"),
    }
    # missing count over FEATURE_SCHEMA numeric fields except codes always present
    from .precommit import FEATURE_SCHEMA
    miss = 0
    for k in FEATURE_SCHEMA:
        if k in ("setup_type_code", "anchor_type_code", "trade_side_quality_code", "missing_feature_count"):
            continue
        if feats.get(k) is None:
            miss += 1
    feats["missing_feature_count"] = miss
    return feats


def _window(buf: FeatureBuffer, now: float, sec: float):
    if not buf.ticks:
        return []
    lo = now - sec
    return [x for x in buf.ticks if x.t >= lo - 1e-12 and x.t <= now + 1e-12]


def _ret(buf, now, sec):
    w = _window(buf, now, sec)
    if len(w) < 2 or w[0].mid <= 0:
        return None
    return (w[-1].mid / w[0].mid - 1.0) * 10000.0


def _slope(buf, now, sec):
    w = _window(buf, now, sec)
    if len(w) < 4:
        return None
    t0 = w[0].t
    xs = [x.t - t0 for x in w]
    ys = [x.mid for x in w]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return num / den


def _vol(buf, now, sec):
    w = _window(buf, now, sec)
    if len(w) < 2:
        return None
    return max(0.0, w[-1].cum_vol - w[0].cum_vol)


def _pu(buf, now, sec):
    w = _window(buf, now, sec)
    if len(w) < 2:
        return None
    c = 0
    prev = w[0].mid
    for x in w[1:]:
        if abs(x.mid - prev) > 1e-12:
            c += 1
            prev = x.mid
    return c


def _bid_updates(buf, now, sec):
    w = _window(buf, now, sec)
    if len(w) < 2:
        return None
    c = 0
    prev = w[0].bid
    for x in w[1:]:
        if abs(x.bid - prev) > 1e-12:
            c += 1
            prev = x.bid
    return c


def _opp_row(st: OppState) -> dict[str, Any]:
    def hz(d, h):
        v = d.get(h)
        if v is None or (isinstance(v, float) and (math.isinf(v) or math.isnan(v))):
            return None
        return float(v)

    return {
        "episode_id": st.episode_id,
        "setup_type": st.setup_type,
        "day": st.day,
        "session": st.session,
        "symbol": st.symbol,
        "entry_time": st.entry_t,
        "entry_price": st.entry_ask,
        "entry_event_id": st.entry_event_id,
        "evaluable": st.entry_ask is not None and st.path_event_count > 0,
        "best_net_pnl_bps_30s": hz(st.best, 30.0),
        "best_net_pnl_bps_60s": hz(st.best, 60.0),
        "best_net_pnl_bps_120s": hz(st.best, 120.0),
        "best_net_pnl_bps_300s": hz(st.best, 300.0) if st.best_net_300 is None else st.best_net_300,
        "worst_net_pnl_bps_30s": hz(st.worst, 30.0),
        "worst_net_pnl_bps_60s": hz(st.worst, 60.0),
        "worst_net_pnl_bps_120s": hz(st.worst, 120.0),
        "worst_net_pnl_bps_300s": hz(st.worst, 300.0),
        "time_to_net_positive_sec": st.time_to_pos,
        "time_to_net_plus_5bps_sec": st.time_to_p5,
        "time_to_net_plus_10bps_sec": st.time_to_p10,
        "adverse_before_best_bps": st.adverse_before_best if st.best_exit_time is not None else st._min_so_far,
        "first_touch_plus_5_or_minus_10": st.first_touch_5_m10,
        "first_touch_plus_10_or_minus_15": st.first_touch_10_m15,
        "best_exit_time": st.best_exit_time,
        "best_exit_bid": st.best_exit_bid,
        "path_complete": st.path_complete or (
            st.last_bid_t is not None and (st.last_bid_t - st.entry_t) >= MAX_HOLD_SEC - 1.0
        ),
        "path_event_count": st.path_event_count,
    }
