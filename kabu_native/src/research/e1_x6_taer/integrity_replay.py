"""Economic-integrity-fixed TAER joint replay (SESSION_END / MAX_HOLD / same-session).

Does not change ENTRY profile, EXIT thresholds, or P2 SHA contents.
Does not overwrite TAER_V1_JOINT_INVALID_ECONOMIC_INTEGRITY run.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x6_fcrr.features import FeatureBuffer
from research.e1_x6_fcrr.replay import (
    _universe_from_manifest,
    load_day_events,
    load_source_manifest,
)
from research.e1_x6_provisional.cost_contract import COST_RATE, LOT, net_pnl_yen, yen_roundtrip_cost
from research.e1_x6_provisional.util import sha256_file, sha256_obj

from .config import DAYS
from .exit_joint_audit import PRIOR_STORE, load_entry_observations, setup_path_summary, decompose_s7
from .exit_sm import EXIT_THRESHOLDS, ExitPos, step_exit, _tick

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
MAX_HOLD_SEC = float(EXIT_THRESHOLDS["max_hold_sec"])  # 300
EVAL_TOL_SEC = 5.0  # evaluation_tolerance_sec
GAP_TOL_SEC = 60.0  # if first event after deadline is later than this → MAX_HOLD_GAP_EXIT

PAIRS = [
    ("TAER_P3", "PULLBACK_RECLAIM", "R10", "X_STRUCTURAL"),
    ("TAER_P3", "PULLBACK_RECLAIM", "R10", "X_CONTINUATION"),
    ("TAER_P3", "PULLBACK_RECLAIM", "R10", "X_HYBRID"),
    ("TAER_P3", "RANGE_BREAKOUT", "R10", "X_STRUCTURAL"),
    ("TAER_P3", "RANGE_BREAKOUT", "R10", "X_CONTINUATION"),
    ("TAER_P3", "RANGE_BREAKOUT", "R10", "X_HYBRID"),
]

LOCKED_P1 = "f7ef02ee7f8a2580765658cae1fe5e2fcabaf3d74cbf147142fa67cb83aa7db9"
LOCKED_P2 = "9a484c78b74c66d32be002b3bc9db9a0068b95ea62bbe49e4accf810c575894a"


def _session_of(ts) -> str:
    return "AM" if ts.hour < 12 else "PM"


def _pf(pnls: list[float]) -> tuple[Optional[float], str]:
    gains = sum(x for x in pnls if x > 0)
    losses = sum(-x for x in pnls if x < 0)
    if losses <= 1e-12 and gains > 0:
        return None, "NO_LOSS"
    if losses <= 1e-12:
        return None, "EMPTY"
    return gains / losses, "OK"


def _max_dd(day_pnls: dict[str, float]) -> float:
    eq = peak = 0.0
    dd = 0.0
    for d in sorted(day_pnls):
        eq += day_pnls[d]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd


@dataclass
class PathTracker:
    times: list[float] = field(default_factory=list)
    symbols: set[str] = field(default_factory=set)
    days: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)
    mfe_bid: float = 0.0
    mae_bid: float = 0.0
    last_valid: Optional[dict[str, Any]] = None
    event_count: int = 0

    def observe(self, *, t: float, sym: str, day: str, session: str,
                bid: float, ask: float, mid: float, entry_ask: float, event_id: str) -> None:
        self.times.append(t)
        self.symbols.add(sym)
        self.days.add(day)
        self.sessions.add(session)
        self.event_count += 1
        d = bid - entry_ask
        self.mfe_bid = max(self.mfe_bid, d)
        self.mae_bid = min(self.mae_bid, d)
        self.last_valid = {
            "t": t, "bid": bid, "ask": ask, "mid": mid,
            "event_id": event_id, "day": day, "session": session, "symbol": sym,
        }


def _integrity_check(tr: dict[str, Any]) -> tuple[str, list[str]]:
    fails = []
    if tr["entry_event_symbol"] != tr["exit_event_symbol"]:
        fails.append("FAIL_CROSS_SYMBOL")
    if tr["path_symbol_unique_count"] != 1:
        fails.append("FAIL_CROSS_SYMBOL")
    if tr["entry_event_day"] != tr["exit_event_day"]:
        fails.append("FAIL_CROSS_DAY")
    if tr["path_day_unique_count"] != 1:
        fails.append("FAIL_CROSS_DAY")
    if tr["entry_event_session"] != tr["exit_event_session"]:
        fails.append("FAIL_CROSS_SESSION")
    if tr["path_session_unique_count"] != 1:
        fails.append("FAIL_CROSS_SESSION")
    if tr["exit_time"] < tr["entry_time"] - 1e-9:
        fails.append("FAIL_TIME_ORDER")
    hold = float(tr["hold_sec"])
    reason = tr["exit_reason"]
    # MAX_HOLD exits at first event >= deadline; event may arrive slightly after 300s
    # due to evaluate cadence. Long overruns must be tagged MAX_HOLD_GAP_EXIT.
    if reason == "MAX_HOLD":
        if hold > MAX_HOLD_SEC + GAP_TOL_SEC + 1e-9:
            fails.append("FAIL_MAX_HOLD_SHOULD_BE_GAP")
    elif reason == "MAX_HOLD_GAP_EXIT":
        pass  # long gap holds are expected and separated
    elif reason == "NOT_EVALUABLE_SESSION_END_EXIT_PRICE":
        pass
    else:
        if hold > MAX_HOLD_SEC + EVAL_TOL_SEC + 1e-9:
            fails.append("FAIL_MAX_HOLD_VIOLATION")
    # MFE/MAE envelope on price deltas
    if tr.get("integrity_status") != "NOT_EVALUABLE":
        realized = float(tr["realized_price_delta"])
        mfe = float(tr["mfe_price_delta"])
        mae = float(tr["mae_price_delta"])
        tick = _tick(float(tr["entry_price_used"]))
        tol = tick
        if realized < mae - tol - 1e-12 or realized > mfe + tol + 1e-12:
            fails.append("FAIL_REALIZED_OUTSIDE_MFE_MAE")
        # PnL identity
        gross = (float(tr["exit_price_used"]) - float(tr["entry_price_used"])) * LOT
        if abs(gross - float(tr["gross_pnl_yen"])) > 1e-6:
            fails.append("FAIL_GROSS_PNL_IDENTITY")
        cost = yen_roundtrip_cost(float(tr["entry_price_used"]))
        if abs(cost - float(tr["cost_yen"])) > 1e-6:
            fails.append("FAIL_COST_IDENTITY")
        if abs(float(tr["net_pnl_yen"]) - (gross - cost)) > 1e-4:
            fails.append("FAIL_NET_PNL_IDENTITY")
    status = "PASS" if not fails else "FAIL"
    if tr.get("price_source") == "NOT_EVALUABLE":
        status = "NOT_EVALUABLE"
    return status, fails


def _close_trade(
    *,
    pos: ExitPos,
    meta: dict,
    tracker: PathTracker,
    exit_quote: Optional[dict[str, Any]],
    exit_reason: str,
    exit_state: str,
    session_boundary_time: Optional[float],
    pair_id: str,
    setup_type: str,
    exit_candidate: str,
    day: str,
) -> dict[str, Any]:
    entry_ask = float(pos.entry_ask)
    if exit_quote is None:
        tr = {
            "pair_id": pair_id,
            "episode_id": meta["episode_id"],
            "setup_type": setup_type,
            "entry_profile": "TAER_P3",
            "exit_candidate": exit_candidate,
            "day": day,
            "session": meta["entry_session"],
            "symbol": pos.symbol,
            "entry_time": pos.entry_t,
            "exit_time": pos.entry_t,
            "hold_sec": 0.0,
            "entry_event_id": meta["entry_event_id"],
            "exit_event_id": None,
            "entry_event_day": day,
            "exit_event_day": day,
            "entry_event_session": meta["entry_session"],
            "exit_event_session": meta["entry_session"],
            "entry_event_symbol": pos.symbol,
            "exit_event_symbol": pos.symbol,
            "entry_best_bid": meta["entry_bid"],
            "entry_best_ask": entry_ask,
            "entry_mid": pos.entry_mid,
            "entry_price_used": entry_ask,
            "exit_best_bid": None,
            "exit_best_ask": None,
            "exit_mid": None,
            "exit_price_used": None,
            "path_first_time": tracker.times[0] if tracker.times else pos.entry_t,
            "path_last_time": tracker.times[-1] if tracker.times else pos.entry_t,
            "path_event_count": tracker.event_count,
            "path_symbol_unique_count": len(tracker.symbols),
            "path_day_unique_count": len(tracker.days),
            "path_session_unique_count": len(tracker.sessions),
            "mfe_price_delta": tracker.mfe_bid,
            "mae_price_delta": tracker.mae_bid,
            "realized_price_delta": None,
            "lot": LOT,
            "gross_pnl_yen": None,
            "cost_bps": 5.0,
            "cost_yen": None,
            "net_pnl_yen": None,
            "exit_reason": "NOT_EVALUABLE_SESSION_END_EXIT_PRICE",
            "exit_state": exit_state,
            "price_source": "NOT_EVALUABLE",
            "session_boundary_time": session_boundary_time,
            "selected_exit_event_time": None,
            "selected_exit_event_id": None,
            "selected_exit_bid": None,
            "seconds_from_selected_event_to_boundary": None,
            "integrity_status": "NOT_EVALUABLE",
            "integrity_failure_reasons": ["NOT_EVALUABLE_SESSION_END_EXIT_PRICE"],
        }
        return tr

    exit_bid = float(exit_quote["bid"])
    exit_t = float(exit_quote["t"])
    hold = exit_t - pos.entry_t
    realized = exit_bid - entry_ask
    gross = realized * LOT
    cost = yen_roundtrip_cost(entry_ask)
    net = gross - cost
    # also via contract
    econ = net_pnl_yen(entry_ask, exit_bid)
    # prefer contract net (must match)
    net = float(econ["net_pnl_yen_100"])
    cost = float(econ["cost_yen_100"])
    gross = float(econ["gross_pnl_yen_100"])

    boundary = session_boundary_time
    sec_to_boundary = None if boundary is None else (boundary - exit_t)

    tr = {
        "pair_id": pair_id,
        "episode_id": meta["episode_id"],
        "setup_type": setup_type,
        "entry_profile": "TAER_P3",
        "exit_candidate": exit_candidate,
        "day": day,
        "session": meta["entry_session"],
        "symbol": pos.symbol,
        "entry_time": pos.entry_t,
        "exit_time": exit_t,
        "hold_sec": hold,
        "entry_event_id": meta["entry_event_id"],
        "exit_event_id": exit_quote["event_id"],
        "entry_event_day": day,
        "exit_event_day": exit_quote["day"],
        "entry_event_session": meta["entry_session"],
        "exit_event_session": exit_quote["session"],
        "entry_event_symbol": pos.symbol,
        "exit_event_symbol": exit_quote["symbol"],
        "entry_best_bid": meta["entry_bid"],
        "entry_best_ask": entry_ask,
        "entry_mid": pos.entry_mid,
        "entry_price_used": entry_ask,
        "exit_best_bid": exit_bid,
        "exit_best_ask": float(exit_quote["ask"]),
        "exit_mid": float(exit_quote["mid"]),
        "exit_price_used": exit_bid,
        "path_first_time": tracker.times[0] if tracker.times else pos.entry_t,
        "path_last_time": tracker.times[-1] if tracker.times else exit_t,
        "path_event_count": tracker.event_count,
        "path_symbol_unique_count": len(tracker.symbols),
        "path_day_unique_count": len(tracker.days),
        "path_session_unique_count": len(tracker.sessions),
        "mfe_price_delta": tracker.mfe_bid,
        "mae_price_delta": tracker.mae_bid,
        "realized_price_delta": realized,
        "lot": LOT,
        "gross_pnl_yen": gross,
        "cost_bps": 5.0,
        "cost_yen": cost,
        "net_pnl_yen": net,
        "exit_reason": exit_reason,
        "exit_state": exit_state,
        "price_source": "CANONICAL_BEST_BID_SAME_SESSION",
        "session_boundary_time": boundary,
        "selected_exit_event_time": exit_t,
        "selected_exit_event_id": exit_quote["event_id"],
        "selected_exit_bid": exit_bid,
        "seconds_from_selected_event_to_boundary": sec_to_boundary,
        "integrity_status": "",
        "integrity_failure_reasons": [],
        "scenario_id_prior": meta.get("scenario_id_prior"),
    }
    st, fails = _integrity_check(tr)
    tr["integrity_status"] = st
    tr["integrity_failure_reasons"] = fails
    return tr


def replay_pair_integrity(
    entries: list[dict],
    *,
    setup_type: str,
    exit_candidate: str,
    day_events: dict[str, list],
) -> dict[str, Any]:
    subset = [e for e in entries if e["setup_type"] == setup_type]
    pair_id = f"TAER_P3|{setup_type}|R10|{exit_candidate}"
    trades: list[dict] = []
    reason_c: Counter = Counter()
    integrity_c: Counter = Counter()

    for day in sorted({e["day"] for e in subset}):
        events = day_events[day]
        day_entries = sorted([e for e in subset if e["day"] == day], key=lambda x: x["entry_t"])
        queue: dict[str, list] = defaultdict(list)
        for e in day_entries:
            queue[e["symbol"]].append(e)
        for s in queue:
            queue[s].sort(key=lambda x: x["entry_t"])

        # positions: sym -> (pos, meta, tracker)
        positions: dict[str, tuple[ExitPos, dict, PathTracker]] = {}
        entered: set[str] = set()
        bufs: dict[str, FeatureBuffer] = {}
        last_feat_bucket: dict[str, int] = {}
        # session end boundaries approx: last event time per session in day stream
        session_last_t = {"AM": None, "PM": None}
        for t, _, row in events:
            sess = _session_of(row["ts"])
            session_last_t[sess] = t if session_last_t[sess] is None else max(session_last_t[sess], t)

        event_i = 0
        for t, sym, row in events:
            event_i += 1
            event_id = f"{day}|{sym}|{event_i}|{t}"
            bid, ask = float(row["bid"]), float(row["ask"])
            mid = 0.5 * (bid + ask)
            sess = _session_of(row["ts"])
            vwap = float(row["vwap"]) if row.get("vwap") is not None else None
            spread = (ask - bid) / mid * 10000.0 if mid > 0 else None

            # close on session change for open position of this symbol
            if sym in positions:
                pos, meta, tracker = positions[sym]
                if sess != meta["entry_session"]:
                    # SESSION_END using last valid same-session quote
                    boundary = session_last_t.get(meta["entry_session"])
                    tr = _close_trade(
                        pos=pos, meta=meta, tracker=tracker,
                        exit_quote=tracker.last_valid,
                        exit_reason="SESSION_END",
                        exit_state=pos.state,
                        session_boundary_time=boundary,
                        pair_id=pair_id, setup_type=setup_type,
                        exit_candidate=exit_candidate, day=day,
                    )
                    trades.append(tr)
                    reason_c[tr["exit_reason"]] += 1
                    integrity_c[tr["integrity_status"]] += 1
                    del positions[sym]
                    # do not process this event as continuation of old pos

            if sym in positions:
                pos, meta, tracker = positions[sym]
                deadline = pos.entry_t + MAX_HOLD_SEC
                # MAX_HOLD / GAP
                if t + 1e-12 >= deadline:
                    gap = t - deadline
                    # use current quote if same session; else last_valid
                    if sess == meta["entry_session"]:
                        tracker.observe(
                            t=t, sym=sym, day=day, session=sess,
                            bid=bid, ask=ask, mid=mid, entry_ask=pos.entry_ask, event_id=event_id,
                        )
                        q = tracker.last_valid
                    else:
                        q = tracker.last_valid
                    reason = "MAX_HOLD_GAP_EXIT" if gap > GAP_TOL_SEC else "MAX_HOLD"
                    tr = _close_trade(
                        pos=pos, meta=meta, tracker=tracker,
                        exit_quote=q, exit_reason=reason, exit_state="EXIT",
                        session_boundary_time=None,
                        pair_id=pair_id, setup_type=setup_type,
                        exit_candidate=exit_candidate, day=day,
                    )
                    # annotate gap
                    tr["max_hold_deadline"] = deadline
                    tr["max_hold_gap_sec"] = gap
                    trades.append(tr)
                    reason_c[tr["exit_reason"]] += 1
                    integrity_c[tr["integrity_status"]] += 1
                    del positions[sym]
                else:
                    # normal path observe + SM
                    if sess == meta["entry_session"]:
                        tracker.observe(
                            t=t, sym=sym, day=day, session=sess,
                            bid=bid, ask=ask, mid=mid, entry_ask=pos.entry_ask, event_id=event_id,
                        )
                        if sym not in bufs:
                            bufs[sym] = FeatureBuffer()
                        bufs[sym].push(t, bid, ask, row["vwap"], row["vol"])
                        feats = {}
                        bucket = int(t // 5.0)
                        if last_feat_bucket.get(sym) != bucket:
                            last_feat_bucket[sym] = bucket
                            snap = bufs[sym].snapshot(t)
                            if snap.get("complete"):
                                feats = snap
                        hit = step_exit(
                            pos, t=t, bid=bid, ask=ask, mid=mid, vwap=vwap, spread_bps=spread,
                            volume_30s=feats.get("volume_30s"),
                            price_update_count_10s=feats.get("price_update_count_10s"),
                        )
                        # Override mid-based mfe in pack — we use bid tracker at close
                        if hit:
                            tr = _close_trade(
                                pos=pos, meta=meta, tracker=tracker,
                                exit_quote=tracker.last_valid,
                                exit_reason=hit["exit_reason"],
                                exit_state=hit["exit_state"],
                                session_boundary_time=None,
                                pair_id=pair_id, setup_type=setup_type,
                                exit_candidate=exit_candidate, day=day,
                            )
                            trades.append(tr)
                            reason_c[tr["exit_reason"]] += 1
                            integrity_c[tr["integrity_status"]] += 1
                            del positions[sym]

            # entries
            q = queue.get(sym) or []
            while q and q[0]["entry_t"] <= t + 1e-9:
                e = q.pop(0)
                if e["episode_id"] in entered:
                    continue
                if sym in positions:
                    continue
                if len(positions) >= 5:
                    entered.add(e["episode_id"])
                    continue
                if abs(e["entry_t"] - t) > 2.0 and t < e["entry_t"]:
                    # not yet
                    q.insert(0, e)
                    break
                # open only on matching session of entry event
                entry_sess = sess if abs(e["entry_t"] - t) < 2.0 else _session_of(
                    datetime.fromtimestamp(e["entry_t"], tz=JST)
                )
                # Prefer current quote if this is the entry event
                if abs(e["entry_t"] - t) > 5.0:
                    # skip until we see near entry time — put back
                    # Actually entries are keyed by path_head time; walk until t>=entry_t
                    pass
                pb = e.get("pullback_low")
                pb_f = float(pb) if pb is not None else None
                pos = ExitPos(
                    symbol=sym,
                    setup_type=setup_type,
                    exit_candidate=exit_candidate,
                    entry_t=float(e["entry_t"]),
                    entry_ask=float(e["entry_ask"]),
                    entry_mid=float(e["entry_mid"]),
                    reclaim_level=float(e["reclaim_level"]),
                    pullback_low=pb_f,
                    range_high=float(e["reclaim_level"]),
                    range_low=pb_f,
                    vwap_at_entry=None,
                    atr=None,
                    last_progress_t=float(e["entry_t"]),
                    peak_mid=float(e["entry_mid"]),
                )
                if sym not in bufs:
                    bufs[sym] = FeatureBuffer()
                bufs[sym].push(t, bid, ask, row["vwap"], row["vol"])
                snap = bufs[sym].snapshot(t)
                if snap.get("complete"):
                    pos.atr = snap.get("atr_180s")
                    pos.vol30_at_entry = snap.get("volume_30s")
                    pos.vwap_at_entry = snap.get("vwap")
                tracker = PathTracker()
                meta = {
                    **e,
                    "entry_session": entry_sess if abs(e["entry_t"] - t) < 5 else sess,
                    "entry_event_id": event_id,
                    "entry_bid": bid,
                }
                # ensure session from entry timestamp
                meta["entry_session"] = _session_of(datetime.fromtimestamp(e["entry_t"], tz=JST))
                if sess != meta["entry_session"]:
                    # entry event not in this session tick — wait
                    q.insert(0, e)
                    break
                tracker.observe(
                    t=t, sym=sym, day=day, session=sess,
                    bid=bid, ask=ask, mid=mid, entry_ask=pos.entry_ask, event_id=event_id,
                )
                positions[sym] = (pos, meta, tracker)
                entered.add(e["episode_id"])
                step_exit(
                    pos, t=t, bid=bid, ask=ask, mid=mid, vwap=vwap, spread_bps=spread,
                    volume_30s=pos.vol30_at_entry, price_update_count_10s=None,
                )

        # end of day: SESSION_END remaining with last valid
        for sym, (pos, meta, tracker) in list(positions.items()):
            boundary = session_last_t.get(meta["entry_session"])
            tr = _close_trade(
                pos=pos, meta=meta, tracker=tracker,
                exit_quote=tracker.last_valid,
                exit_reason="SESSION_END",
                exit_state=pos.state,
                session_boundary_time=boundary,
                pair_id=pair_id, setup_type=setup_type,
                exit_candidate=exit_candidate, day=day,
            )
            trades.append(tr)
            reason_c[tr["exit_reason"]] += 1
            integrity_c[tr["integrity_status"]] += 1
        positions.clear()

    # metrics only on PASS trades for gates; keep all for audit
    pass_trades = [t for t in trades if t["integrity_status"] == "PASS"]
    pnls = [t["net_pnl_yen"] for t in pass_trades if t["net_pnl_yen"] is not None]
    day_pnl: dict[str, float] = defaultdict(float)
    for t in pass_trades:
        if t["net_pnl_yen"] is not None:
            day_pnl[t["day"]] += t["net_pnl_yen"]
    pf, pf_st = _pf(pnls)
    wins = sum(1 for x in pnls if x > 0)
    losses = sum(1 for x in pnls if x < 0)
    draws = len(pnls) - wins - losses
    ex722 = [t for t in pass_trades if t["day"] != "20260722" and t["net_pnl_yen"] is not None]
    ex722_pnls = [t["net_pnl_yen"] for t in ex722]
    ex722_pf, _ = _pf(ex722_pnls)
    by_sym: dict[str, float] = defaultdict(float)
    for t in pass_trades:
        if t["net_pnl_yen"] is not None:
            by_sym[t["symbol"]] += t["net_pnl_yen"]
    top_sym = max(by_sym.items(), key=lambda x: abs(x[1])) if by_sym else (None, 0.0)
    top_trade = max(pass_trades, key=lambda x: abs(x.get("net_pnl_yen") or 0)) if pass_trades else None
    top_day = max(day_pnl.items(), key=lambda x: abs(x[1])) if day_pnl else (None, 0.0)

    confirm_days = ["20260727", "20260728", "20260729", "20260730", "20260731"]
    slices = [{"fold": f"F{i}", "confirm": cd, "confirm_pnl": day_pnl.get(cd, 0.0)}
              for i, cd in enumerate(confirm_days, 1)]
    day_del = [{"held_out_day": d, "remaining_pnl": sum(v for k, v in day_pnl.items() if k != d)}
               for d in sorted(day_pnl)]

    ledger_sha = sha256_obj([
        {
            "episode_id": t["episode_id"], "entry_time": t["entry_time"], "exit_time": t["exit_time"],
            "exit_reason": t["exit_reason"], "exit_price_used": t["exit_price_used"],
            "net_pnl_yen": t["net_pnl_yen"], "integrity_status": t["integrity_status"],
        }
        for t in sorted(trades, key=lambda x: (x["entry_time"], x["symbol"], x["episode_id"]))
    ])

    return {
        "pair_id": pair_id,
        "setup_type": setup_type,
        "exit_candidate": exit_candidate,
        "n_all": len(trades),
        "n_pass": len(pass_trades),
        "n_fail": sum(1 for t in trades if t["integrity_status"] == "FAIL"),
        "n_not_evaluable": sum(1 for t in trades if t["integrity_status"] == "NOT_EVALUABLE"),
        "pnl": sum(pnls) if pnls else 0.0,
        "pf": pf,
        "pf_status": pf_st,
        "wld": {"w": wins, "l": losses, "d": draws},
        "avg_pnl": (sum(pnls) / len(pnls)) if pnls else None,
        "max_dd": _max_dd(dict(day_pnl)),
        "exit_reason_counts": dict(reason_c),
        "integrity_counts": dict(integrity_c),
        "day_pnl": dict(day_pnl),
        "ex722_pnl": sum(ex722_pnls) if ex722_pnls else 0.0,
        "ex722_pf": ex722_pf,
        "ex722_n": len(ex722),
        "top1_day": {"day": top_day[0], "pnl": top_day[1]},
        "top1_symbol": {"symbol": top_sym[0], "pnl": top_sym[1]},
        "top1_trade_pnl": None if top_trade is None else top_trade.get("net_pnl_yen"),
        "day_deletion": day_del,
        "retrospective_confirm_day_slice": slices,
        "ledger_sha256": ledger_sha,
        "trades": trades,
    }


def noncore_gates(p: dict[str, Any]) -> dict[str, Any]:
    """Non-CORE diagnostic gates (not adoption)."""
    if p["n_fail"] > 0 or p["n_pass"] == 0:
        return {"all_pass": False, "failed": ["integrity_or_empty"], "checks": {}}
    slices = p.get("retrospective_confirm_day_slice") or []
    pos_slices = sum(1 for s in slices if float(s["confirm_pnl"]) > 0)
    med = sorted(float(s["confirm_pnl"]) for s in slices)[len(slices) // 2] if slices else 0.0
    day_del_ok = all(float(r["remaining_pnl"]) >= 0 for r in (p.get("day_deletion") or []))
    # top1 exclusions
    pnl = float(p["pnl"])
    top_trade = float(p["top1_trade_pnl"] or 0)
    top_sym = float((p.get("top1_symbol") or {}).get("pnl") or 0)
    top_day = float((p.get("top1_day") or {}).get("pnl") or 0)
    checks = {
        "pnl_gt_0": pnl > 0,
        "pf_ge_1_10": (p["pf"] is not None and float(p["pf"]) >= 1.10) or p["pf_status"] == "NO_LOSS",
        "positive_confirm_slices_ge_3": pos_slices >= 3,
        "confirm_median_gt_0": med > 0,
        "day_deletion_remaining_ge_0": day_del_ok,
        "ex_top1_trade_pnl_gt_0": (pnl - top_trade) > 0 if top_trade > 0 else (pnl - top_trade) > 0 or pnl > 0,
        "ex_top1_symbol_pnl_gt_0": (pnl - top_sym) > 0,
        "ex_top1_day_pnl_ge_0": (pnl - top_day) >= 0,
    }
    # fix ex_top1_trade: exclude the top trade pnl from total
    checks["ex_top1_trade_pnl_gt_0"] = (pnl - top_trade) > 0
    failed = [k for k, v in checks.items() if not v]
    return {"all_pass": len(failed) == 0, "failed": failed, "checks": checks}
