"""FCRR window replay → CAP5 + frozen E1_X5 EXIT (streaming)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from research.e1_x6_provisional.analysis_mask import build_mask_index, row_in_analysis_mask
from research.e1_x6_provisional.cost_contract import LOT, net_pnl_yen
from research.e1_x6_provisional.portfolio_replay import CAP, _Pos, _exit_reason
from research.e1_x6_provisional.util import parse_ts

from .config import CANDIDATE_IDS, DAYS
from .decision import push_and_decide
from .features import FeatureBuffer
from .state_machine import Machine

NATIVE = Path(__file__).resolve().parents[3]


def _day_dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _raw_day_dir(day: str) -> Path:
    return NATIVE / "data" / "push_jsonl" / _day_dash(day)


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _norm_sym(sym: str) -> str:
    s = str(sym)
    return s[:-2] if s.endswith(".T") else s


def load_source_manifest() -> dict[str, Any]:
    fp = NATIVE / "results" / "research" / "e1_x6_redesign_20260721_20260731" / "report1.json"
    r = json.loads(fp.read_text(encoding="utf-8"))
    return r["source_manifest"]


def _universe_from_manifest(sm: dict[str, Any], day: str) -> list[str]:
    for w in sm.get("windows") or []:
        if str(w.get("day")) != day:
            continue
        u = w.get("universe_symbols") or []
        if u:
            return sorted({_norm_sym(x) for x in u})
    rd = _raw_day_dir(day)
    if not rd.is_dir():
        return []
    return sorted({_norm_sym(fp.stem) for fp in rd.glob("*.jsonl")})[:50]


def compute_volume_abs_floor(samples: list[float]) -> float:
    xs = sorted(v for v in samples if v is not None and v > 0)
    if not xs:
        return 0.0
    return float(xs[len(xs) // 2])


_EVENT_CACHE: dict[tuple[str, tuple[str, ...]], list[tuple[float, str, dict]]] = {}


def load_day_events(day: str, universe: list[str]) -> list[tuple[float, str, dict]]:
    key = (day, tuple(sorted(universe)))
    hit = _EVENT_CACHE.get(key)
    if hit is not None:
        return hit
    uset = set(universe)
    rows: list[tuple[float, str, dict]] = []
    rd = _raw_day_dir(day)
    for fp in sorted(rd.glob("*.jsonl")):
        sym = _norm_sym(fp.stem)
        if sym not in uset:
            continue
        with fp.open("rb") as f:
            for lineb in f:
                try:
                    d = json.loads(lineb)
                except Exception:
                    continue
                ts = parse_ts(d.get("recorded_at"))
                if ts is None:
                    continue
                p = d.get("payload") or {}
                bid = _f((p.get("Buy1") or {}).get("Price"))
                ask = _f((p.get("Sell1") or {}).get("Price"))
                vwap = _f(p.get("VWAP"))
                vol = _f(p.get("TradingVolume"))
                if None in (bid, ask, vwap, vol):
                    continue
                rows.append((ts.timestamp(), sym, {
                    "ts": ts, "bid": bid, "ask": ask, "vwap": vwap, "vol": vol,
                }))
    rows.sort(key=lambda x: (x[0], x[1]))
    _EVENT_CACHE[key] = rows
    return rows


def _iter_day_events(day: str, universe: list[str]):
    yield from load_day_events(day, universe)


def replay_day_candidate(
    day: str,
    candidate_id: str,
    *,
    mask_index: dict,
    universe: list[str],
    volume_abs_floor: float,
    events: Optional[list[tuple[float, str, dict]]] = None,
) -> dict[str, Any]:
    bufs = {s: FeatureBuffer() for s in universe}
    machines = {
        s: Machine(symbol=s, candidate_id=candidate_id, volume_abs_floor=volume_abs_floor)
        for s in universe
    }
    positions: dict[str, _Pos] = {}
    completed: list[dict] = []
    decision_ledger: list[dict] = []
    entry_by_episode: set[tuple[str, int]] = set()
    cap_blocked = 0
    episode_reentry = 0
    signals = 0
    funnel = {
        "obs": 0, "CONTEXT_READY": 0, "PULLBACK_ACTIVE": 0, "SELLING_EXHAUSTED": 0,
        "RECLAIM_CROSSED": 0, "RETENTION_CONFIRMED": 0, "ENTRY_EMITTED": 0,
    }
    last_eval_t: dict[str, float] = {}
    vol30_samples: list[float] = []

    for t, sym, row in (events if events is not None else load_day_events(day, universe)):
        # EXIT monitor for open positions on every quote of that symbol
        if sym in positions:
            pos = positions[sym]
            reason = _exit_reason(pos, row["bid"], row["ts"])
            if reason:
                econ = net_pnl_yen(pos.entry_ask, row["bid"])
                completed.append({
                    "day": day,
                    "am_pm": "AM" if row["ts"].hour < 12 else "PM",
                    "symbol": sym,
                    "entry_time": pos.entry_time.isoformat(),
                    "exit_time": row["ts"].isoformat(),
                    "entry_ask": pos.entry_ask,
                    "exit_bid": row["bid"],
                    "exit_reason": reason,
                    "holding_sec": (row["ts"] - pos.entry_time).total_seconds(),
                    "lot": LOT,
                    "candidate_id": candidate_id,
                    **econ,
                })
                del positions[sym]

        mask = row_in_analysis_mask(day, row["ts"], mask_index)
        in_mask = bool(mask.get("in_analysis_mask"))
        bucket = int(t // 5.0)
        prev_b = int(last_eval_t[sym] // 5.0) if sym in last_eval_t else None
        evaluate = in_mask and (prev_b is None or bucket != prev_b)
        if evaluate:
            last_eval_t[sym] = t
            funnel["obs"] += 1

        n_tr = 0  # unused; funnel uses last_step_tos
        sig, feats = push_and_decide(
            bufs[sym], machines[sym],
            t=t, bid=row["bid"], ask=row["ask"], vwap=row["vwap"], cum_vol=row["vol"],
            evaluate=evaluate,
        )
        if evaluate and feats.get("volume_30s"):
            vol30_samples.append(float(feats["volume_30s"]))
        # Only count transitions on evaluate observations. last_step_tos otherwise
        # persists across non-evaluate ticks and would inflate funnel counters.
        if evaluate:
            for to in machines[sym].last_step_tos:
                if to in funnel:
                    funnel[to] = funnel.get(to, 0) + 1
        else:
            machines[sym].last_step_tos.clear()

        if sig is None:
            continue
        signals += 1
        ep_id = int(sig.get("episode_id") or -1)
        key = (sym, ep_id)
        if key in entry_by_episode:
            episode_reentry += 1
            continue
        if sym in positions:
            continue
        if len(positions) >= CAP:
            cap_blocked += 1
            machines[sym].notify_cap_blocked(t)
            entry_by_episode.add(key)
            decision_ledger.append({
                "ts": row["ts"].isoformat(), "symbol": sym,
                "decision": "REJECT", "reason": "CAP5_BLOCKED",
                "episode_id": ep_id, "candidate_id": candidate_id,
            })
            continue
        entry_by_episode.add(key)
        positions[sym] = _Pos(symbol=sym, entry_time=row["ts"], entry_ask=float(sig["entry_ask"]))
        decision_ledger.append({
            "ts": row["ts"].isoformat(), "symbol": sym,
            "decision": "ENTRY", "reason": "E1_X6_FCRR",
            "episode_id": ep_id, "candidate_id": candidate_id,
            "entry_ask": float(sig["entry_ask"]),
        })

    transitions = []
    # Do not retain per-tick state transitions (funnel + decisions suffice for audit).

    return {
        "day": day,
        "candidate_id": candidate_id,
        "completed_trades": completed,
        "cap_blocked": cap_blocked,
        "signals": signals,
        "funnel": funnel,
        "episode_reentry": episode_reentry,
        "decision_ledger": decision_ledger,
        "state_transitions": transitions,
        "vol30_samples": vol30_samples,
        "open_at_end": len(positions),
    }


def replay_all_candidates(
    *,
    volume_abs_floor: float,
    days: tuple[str, ...] = DAYS,
) -> dict[str, Any]:
    sm = load_source_manifest()
    mask_index = build_mask_index(sm)
    # Preload events once per day (shared across R10/R20/R30 and A/B via _EVENT_CACHE).
    day_uni: dict[str, list[str]] = {}
    for day in days:
        uni = _universe_from_manifest(sm, day)
        if uni:
            day_uni[day] = uni
            print(f"  preload {day} universe={len(uni)}", flush=True)
            load_day_events(day, uni)

    per_cand: dict[str, Any] = {}
    for cid in CANDIDATE_IDS:
        all_trades: list = []
        funnels = []
        caps = 0
        reentries = 0
        transitions = []
        decisions = []
        for day, uni in day_uni.items():
            print(f"  FCRR {cid} {day} universe={len(uni)}", flush=True)
            res = replay_day_candidate(
                day, cid, mask_index=mask_index, universe=uni,
                volume_abs_floor=volume_abs_floor,
                events=load_day_events(day, uni),
            )
            all_trades.extend(res["completed_trades"])
            funnels.append({"day": day, **res["funnel"]})
            caps += res["cap_blocked"]
            reentries += res["episode_reentry"]
            # keep transitions compact: counts only in publish; store sha input slim
            transitions.extend(res["state_transitions"][:0])  # drop bulky; use funnel
            decisions.extend(res["decision_ledger"])
            print(
                f"    signals={res['signals']} trades={len(res['completed_trades'])} "
                f"cap_blocked={res['cap_blocked']}",
                flush=True,
            )
        per_cand[cid] = {
            "trades": all_trades,
            "funnels": funnels,
            "cap_blocked": caps,
            "episode_reentry": reentries,
            "transitions": transitions,
            "decisions": decisions,
        }
    return {"candidates": per_cand, "mask_index_n": len(mask_index)}


def estimate_volume_floor(days: tuple[str, ...] = ("20260721", "20260722", "20260723", "20260724")) -> float:
    sm = load_source_manifest()
    mask_index = build_mask_index(sm)
    samples: list[float] = []
    for day in days:
        uni = _universe_from_manifest(sm, day)
        bufs = {s: FeatureBuffer() for s in uni}
        last_eval: dict[str, float] = {}
        n = 0
        for t, sym, row in _iter_day_events(day, uni):
            bufs[sym].push(t, row["bid"], row["ask"], row["vwap"], row["vol"])
            mask = row_in_analysis_mask(day, row["ts"], mask_index)
            if not mask.get("in_analysis_mask"):
                continue
            bucket = int(t // 5.0)
            pb = int(last_eval[sym] // 5.0) if sym in last_eval else None
            if pb is not None and bucket == pb:
                continue
            last_eval[sym] = t
            n += 1
            if n % 3 != 0:  # subsample for fit speed
                continue
            snap = bufs[sym].snapshot(t)
            if snap.get("complete") and snap.get("volume_30s"):
                samples.append(float(snap["volume_30s"]))
        print(f"  floor_fit {day} samples={len(samples)}", flush=True)
    return compute_volume_abs_floor(samples)
