"""PFQ design-period entry selection, path diagnosis, joint replay."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x6_fcrr.features import FeatureBuffer
from research.e1_x6_provisional.cost_contract import net_pnl_yen
from research.e1_x7_pfq.config import REACHABILITY, STRUCTURAL
from research.e1_x7_pfq.exit_sm import PfqPos, step_pfq_exit
from research.e1_x7_pfq.feature_contract import FRESHNESS_MAX_SEC

JST = ZoneInfo("Asia/Tokyo")


def _session_of(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def evaluate_reachability(entries: list[dict[str, Any]], *, candidate_id: str) -> dict[str, Any]:
    n = len(entries)
    days = Counter(e["day"] for e in entries)
    syms = Counter(e["symbol"] for e in entries)
    clusters = {e.get("cluster_id") or e["episode_id"] for e in entries}
    valid_flow = sum(1 for e in entries if e.get("ratio_valid"))
    path_ok = sum(1 for e in entries if e.get("path_complete"))
    max_day = (max(days.values()) / n) if n else 1.0
    max_sym = (max(syms.values()) / n) if n else 1.0
    gates = {
        "unique_overlap_clusters": len(clusters),
        "entry_observation_episodes": n,
        "entry_days": len(days),
        "max_day_share": max_day,
        "max_symbol_share": max_sym,
        "flow_ratio_valid_rate": valid_flow / n if n else 0.0,
        "path_complete_rate": path_ok / n if n else 0.0,
    }
    ok = (
        gates["unique_overlap_clusters"] >= REACHABILITY["unique_overlap_clusters_min"]
        and gates["entry_observation_episodes"] >= REACHABILITY["entry_observation_episodes_min"]
        and gates["entry_days"] >= REACHABILITY["entry_days_min"]
        and gates["max_day_share"] <= REACHABILITY["max_day_share_max"]
        and gates["max_symbol_share"] <= REACHABILITY["max_symbol_share_max"]
        and gates["flow_ratio_valid_rate"] >= REACHABILITY["flow_ratio_valid_rate_min"]
        and gates["path_complete_rate"] >= REACHABILITY["path_complete_rate_min"]
    )
    return {
        "candidate_id": candidate_id,
        "reachable": ok,
        "status": "OK" if ok else "PFQ_ENTRY_UNREACHABLE",
        "gates": gates,
        "by_day": dict(days),
        "top_symbols": dict(syms.most_common(10)),
    }


def build_path_points(
    events: list,
    *,
    sym: str,
    entry_t: float,
    entry_ask: float,
    session: str,
    reclaim_level: float,
    pullback_low: Optional[float],
) -> tuple[list[dict], bool, Optional[str]]:
    buf = FeatureBuffer()
    points: list[dict] = []
    mfe = mae = 0.0
    complete = False
    censor = None
    end_t = entry_t + STRUCTURAL["max_hold_sec"]
    vol0 = None
    last_prog_t = entry_t

    for t, s, row in events:
        if s != sym:
            continue
        sess = _session_of(row["ts"])
        if t + 1e-12 < entry_t:
            buf.push(t, float(row["bid"]), float(row["ask"]), float(row["vwap"]), float(row["vol"]))
            continue
        if sess != session:
            complete = True
            censor = "SESSION_END"
            break
        if t > end_t + 1e-9:
            complete = True
            censor = "HORIZON_END"
            break
        bid, ask = float(row["bid"]), float(row["ask"])
        mid = 0.5 * (bid + ask)
        buf.push(t, bid, ask, float(row["vwap"]), float(row["vol"]))
        if buf.age(t) > FRESHNESS_MAX_SEC:
            continue
        if vol0 is None:
            vol0 = float(row["vol"])
        net = (bid / entry_ask - 1.0) * 10000.0 - 5.0
        d = bid - entry_ask
        if d > mfe:
            mfe = d
            last_prog_t = t
        mae = min(mae, d)
        elapsed = t - entry_t
        # snapshot at ~5s cadence to keep path build tractable
        snap = {}
        if (not points) or (elapsed - float(points[-1]["elapsed_sec"]) >= 4.5) or (t >= end_t - 1e-9):
            snap = buf.snapshot(t)
        else:
            snap = {
                "price_update_count_10s": points[-1].get("price_update_count_since_entry"),
                "uptick_volume_ratio_30s": points[-1].get("uptick_volume_ratio_since_entry"),
            }
        points.append({
            "elapsed_sec": elapsed,
            "t": t,
            "best_bid": bid,
            "best_ask": ask,
            "mid": mid,
            "spread": (ask - bid) / mid * 10000.0 if mid > 0 else None,
            "net_pnl_bps": net,
            "MFE_so_far": mfe,
            "MAE_so_far": mae,
            "time_since_progress": t - last_prog_t,
            "price_update_count_since_entry": snap.get("price_update_count_10s"),
            "volume_since_entry": None if vol0 is None else max(0.0, float(row["vol"]) - vol0),
            "uptick_volume_ratio_since_entry": snap.get("uptick_volume_ratio_30s"),
            "reclaim_level_state": "ABOVE" if mid >= reclaim_level else "BELOW",
            "pullback_low_state": (
                None if pullback_low is None else ("ABOVE" if mid >= float(pullback_low) else "BELOW")
            ),
            "freshness": True,
            "censor_reason": None,
        })
    if points and censor:
        points[-1]["censor_reason"] = censor
    if not complete and points:
        complete = float(points[-1]["elapsed_sec"]) >= STRUCTURAL["max_hold_sec"] - 1.0
        if not complete:
            censor = censor or "STREAM_END"
    return points, bool(complete or (points and censor in ("SESSION_END", "HORIZON_END"))), censor


def path_diagnosis(points: list[dict]) -> dict[str, Any]:
    if not points:
        return {"empty": True}
    t_pos = t5 = None
    min_so_far = 0.0
    adverse_before_5 = 0.0
    for p in points:
        net = float(p["net_pnl_bps"])
        min_so_far = min(min_so_far, net)
        if t_pos is None and net > 0:
            t_pos = p["elapsed_sec"]
        if t5 is None and net >= 5.0 - 1e-12:
            t5 = p["elapsed_sec"]
            adverse_before_5 = min_so_far
    final = points[-1]
    return {
        "time_to_net_positive_sec": t_pos,
        "time_to_plus_5bps_sec": t5,
        "adverse_before_plus_5bps": adverse_before_5 if t5 is not None else min_so_far,
        "false_reclaim": final.get("reclaim_level_state") == "BELOW",
        "no_progress": float(final.get("MFE_so_far") or 0) <= 0,
        "late_continuation": t5 is not None and float(t5) >= 120,
        "structure_failure": final.get("pullback_low_state") == "BELOW",
        "path_n": len(points),
        "final_net_bps": final.get("net_pnl_bps"),
    }


def replay_pair(
    entries: list[dict[str, Any]],
    *,
    candidate_id: str,
    exit_candidate: str,
    events_by_day: dict[str, list],
) -> dict[str, Any]:
    by_day: dict[str, list] = defaultdict(list)
    for e in entries:
        by_day[e["day"]].append(e)

    trades: list[dict] = []
    for day in sorted(by_day):
        events = events_by_day[day]
        day_entries = sorted(by_day[day], key=lambda x: (float(x["entry_time"]), x["episode_id"]))
        qi = 0
        deferred: dict[str, list] = defaultdict(list)
        positions: dict[str, tuple[PfqPos, dict, Optional[dict]]] = {}
        bufs: dict[str, FeatureBuffer] = {}
        opened: set[str] = set()
        symbol_busy: set[str] = set()

        def try_open(e: dict, t: float, sym: str, sess: str, ask: float, mid: float, quote: dict, fresh: bool):
            if e["episode_id"] in opened:
                return
            if e["symbol"] != sym or e.get("session") != sess:
                deferred[e["symbol"]].append(e)
                return
            if not fresh:
                deferred[sym].append(e)
                return
            if len(positions) >= STRUCTURAL["cap"]:
                deferred[sym].append(e)
                return
            if e["symbol"] in symbol_busy:
                deferred[sym].append(e)
                return
            pos = PfqPos(
                symbol=e["symbol"],
                exit_candidate=exit_candidate,
                entry_t=float(e["entry_time"]),
                entry_ask=ask,
                entry_mid=mid,
                reclaim_level=float(e.get("reclaim_level") or ask),
                pullback_low=e.get("pullback_low"),
                entry_pu10=e.get("price_update_count_10s"),
            )
            positions[e["episode_id"]] = (pos, e, quote)
            opened.add(e["episode_id"])
            symbol_busy.add(e["symbol"])

        def close_pos(eid: str, quote: Optional[dict], reason: str, integrity: str = "PASS"):
            pos, meta, last_q = positions.pop(eid)
            symbol_busy.discard(pos.symbol)
            q = quote or last_q
            if q is None:
                trades.append({
                    "candidate_id": candidate_id,
                    "exit_candidate": exit_candidate,
                    "pair_id": f"{candidate_id}|{exit_candidate}",
                    "episode_id": meta["episode_id"],
                    "cluster_id": meta.get("cluster_id"),
                    "day": day,
                    "session": meta.get("session"),
                    "symbol": pos.symbol,
                    "entry_time": pos.entry_t,
                    "exit_reason": reason,
                    "integrity_status": "NOT_EVALUABLE",
                    "net_pnl_yen": None,
                })
                return
            bid = float(q["bid"])
            econ = net_pnl_yen(pos.entry_ask, bid)
            trades.append({
                "candidate_id": candidate_id,
                "exit_candidate": exit_candidate,
                "pair_id": f"{candidate_id}|{exit_candidate}",
                "episode_id": meta["episode_id"],
                "cluster_id": meta.get("cluster_id"),
                "day": day,
                "session": meta.get("session"),
                "symbol": pos.symbol,
                "entry_time": pos.entry_t,
                "exit_time": float(q["t"]),
                "hold_sec": float(q["t"]) - pos.entry_t,
                "entry_ask": pos.entry_ask,
                "exit_bid": bid,
                "exit_reason": reason,
                "gross_pnl_yen": econ["gross_pnl_yen_100"],
                "cost_yen": econ["cost_yen_100"],
                "net_pnl_yen": econ["net_pnl_yen_100"],
                "net_bps": econ["net_bps"],
                "integrity_status": "PASS",
                "profit_floor_armed": bool(pos.profit_floor_armed),
                "profit_floor_armed_at": pos.profit_floor_armed_at,
                "profit_floor_armed_bid": pos.profit_floor_armed_bid,
                "profit_floor_armed_net_bps": pos.profit_floor_armed_net_bps,
                "max_executable_net_bps": (
                    None if pos.max_executable_net_bps == float("-inf") else pos.max_executable_net_bps
                ),
            })

        for t, sym, row in events:
            sess = _session_of(row["ts"])
            bid, ask = float(row["bid"]), float(row["ask"])
            mid = 0.5 * (bid + ask)
            buf = bufs.setdefault(sym, FeatureBuffer())
            buf.push(t, bid, ask, float(row["vwap"]), float(row["vol"]))
            fresh = buf.age(t) <= FRESHNESS_MAX_SEC + 1e-9
            # snapshot only when needed for open positions on this symbol
            snap = {}
            if fresh and (any(positions[eid][0].symbol == sym for eid in positions) or deferred.get(sym)):
                snap = buf.snapshot(t)
            quote = {"t": t, "bid": bid, "ask": ask, "mid": mid, "session": sess}

            while qi < len(day_entries) and float(day_entries[qi]["entry_time"]) <= t + 1e-12:
                try_open(day_entries[qi], t, sym, sess, ask, mid, quote, fresh)
                qi += 1

            if deferred.get(sym):
                waiting = deferred[sym]
                deferred[sym] = []
                for e in waiting:
                    if e["episode_id"] not in opened:
                        try_open(e, t, sym, sess, ask, mid, quote, fresh)

            for eid in list(positions.keys()):
                pos, meta, last_q = positions[eid]
                if pos.symbol != sym:
                    continue
                if sess != meta.get("session"):
                    close_pos(eid, last_q, "SESSION_END")
                    continue
                positions[eid] = (pos, meta, quote)
                if not fresh:
                    continue
                hold = t - pos.entry_t
                # cheap hard checks every tick without FeatureBuffer.snapshot
                net = (bid / pos.entry_ask - 1.0) * 10000.0 - 5.0
                # Research BE5: update arm/max before hard exits so ledger retains state
                if exit_candidate == "PFQ_X_PROGRESS_BE5_FLOOR0":
                    if net > pos.max_executable_net_bps:
                        pos.max_executable_net_bps = net
                    if (not pos.profit_floor_armed) and net >= 5.0 - 1e-9:
                        pos.profit_floor_armed = True
                        pos.profit_floor_armed_at = float(t)
                        pos.profit_floor_armed_bid = float(bid)
                        pos.profit_floor_armed_net_bps = float(net)
                from research.e1_x7_pfq.config import EXIT_THRESHOLDS as ET
                if hold >= float(ET["max_hold_sec"]) - 1e-12:
                    close_pos(eid, quote, "MAX_HOLD")
                    continue
                if net <= float(ET["hard_stop_bps"]) + 1e-12:
                    close_pos(eid, quote, "HARD_STOP")
                    continue
                mid_px = mid
                tick = 1.0
                if pos.pullback_low is not None and mid_px < float(pos.pullback_low) - 1e-12:
                    close_pos(eid, quote, "PULLBACK_LOW_BREAK")
                    continue
                # snapshot only when strategy exits may fire (cadence ~5s or armed protect)
                need_flow = (
                    hold >= float(ET["progress_deadline_sec"]) - 1e-12
                    or pos.state in ("PROFIT_PROTECTION", "COST_COVERED")
                    or net >= float(ET["protect_min_net_bps_for_arm"]) - 1e-12
                    or exit_candidate == "PFQ_X_PROGRESS_BE5_FLOOR0"
                )
                pu = None
                if need_flow:
                    if not snap:
                        snap = buf.snapshot(t)
                    pu = snap.get("price_update_count_10s")
                res = step_pfq_exit(
                    pos, t=t, bid=bid, ask=ask, mid=mid,
                    price_update_count_10s=pu,
                )
                if res:
                    close_pos(eid, quote, res["exit_reason"])

        for eid in list(positions.keys()):
            close_pos(eid, positions[eid][2], "STREAM_END")

    pass_tr = [t for t in trades if t.get("integrity_status") == "PASS" and t.get("net_pnl_yen") is not None]
    pnls = [float(t["net_pnl_yen"]) for t in pass_tr]
    day_pnl: dict[str, float] = defaultdict(float)
    for t in pass_tr:
        day_pnl[t["day"]] += float(t["net_pnl_yen"])
    gains = sum(x for x in pnls if x > 0)
    losses = sum(-x for x in pnls if x < 0)
    return {
        "candidate_id": candidate_id,
        "exit_candidate": exit_candidate,
        "pair_id": f"{candidate_id}|{exit_candidate}",
        "n_trades": len(trades),
        "n_pass": len(pass_tr),
        "pnl": sum(pnls) if pnls else 0.0,
        "pf": (gains / losses) if losses > 1e-12 else None,
        "exit_reason_counts": dict(Counter(t.get("exit_reason") for t in pass_tr)),
        "day_pnl": dict(day_pnl),
        "trades": trades,
        "period_status": "EXPLORATORY_DESIGN_DIAGNOSTIC_ONLY",
    }
