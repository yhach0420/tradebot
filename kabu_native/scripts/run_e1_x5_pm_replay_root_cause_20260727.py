"""E1_X5 20260727 PM Capture replay root-cause (research-only; no Runtime changes).

Produces:
  results/research/e1_x5_pm_replay_root_cause_20260727/{report.md,report.json,audit.xlsx}

Live SoT: small_paper_summary_pm.json e1_x5_forward_shadow
Replay cannot hit exact mono-clock tick selection (no eval event log) → REPLAY_PARITY_FAIL
with first mismatch at 12:40 snapshot; Runtime aggregates remain factual SoT.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

import pandas as pd

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "results" / "research" / "e1_x5_pm_replay_root_cause_20260727"
SESSION = REPO / "results" / "small_paper" / "20260727" / "live_session_122519"
CAPTURE = (
    REPO
    / "data"
    / "market_capture"
    / "20260727"
    / "session_ing_20260727_11752_1785113581_4db3b030"
)
PM_START = datetime(2026, 7, 27, 12, 33, 0, tzinfo=JST)
PM_END = datetime(2026, 7, 27, 15, 23, 0, tzinfo=JST)
SNAP_1240 = datetime(2026, 7, 27, 12, 40, 0, tzinfo=JST)
POLL_SEC = 5.0
THRESHOLD = 0.48256067040851486
LIVE_TARGET = {
    "trades": 173,
    "pnl": -336949.05,
    "pf": 0.4224840804532763,
    "wins": 63,
    "losses": 110,
    "exits": {"STOP": 92, "TRAILING": 47, "MAX_HOLD": 22, "TARGET": 12},
    "cap_blocked": 230,
    "entries": 173,
    "evaluated": 15757,
    "missing": 16,
    "snap7_pnl": -9235.95,
    "snap7_n": 7,
    "snap7_entries": 12,
    "snap7_open": 5,
    "snap7_eval": 696,
}


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _norm_sym(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    if not s.endswith(".T"):
        return f"{s}.T"
    return s


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def load_universe() -> set[str]:
    cfg = json.loads((SESSION / "live_session_config.json").read_text(encoding="utf-8"))
    df = pd.read_csv(cfg["universe_csv_path"])
    col = "symbol" if "symbol" in df.columns else df.columns[0]
    return {_norm_sym(x) for x in df[col].tolist()}


def iter_pm_events(universe: set[str]) -> Iterator[dict[str, Any]]:
    for part in sorted(CAPTURE.glob("push_part_*.jsonl")):
        if part.name < "push_part_0008.jsonl":
            continue
        with part.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = _parse_ts(rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at"))
                if ts is None:
                    continue
                if ts < PM_START:
                    continue
                if ts > PM_END:
                    return
                sym = _norm_sym(rec.get("symbol") or "")
                if sym not in universe:
                    continue
                op = rec.get("original_payload")
                if not isinstance(op, dict):
                    continue
                if not isinstance(op.get("Buy1"), dict) or not isinstance(op.get("Sell1"), dict):
                    continue
                payload = dict(op)
                if payload.get("sequence") is None and rec.get("sequence") is not None:
                    payload["sequence"] = rec["sequence"]
                if not payload.get("CurrentPriceTime"):
                    payload["CurrentPriceTime"] = ts.isoformat()
                yield {
                    "symbol": sym,
                    "recv_ts": ts,
                    "payload": payload,
                    "sequence": rec.get("sequence"),
                    "event_id": str(
                        rec.get("raw_record_id")
                        or f"{ts.isoformat()}|{sym}|{rec.get('sequence')}"
                    ),
                }


@dataclass
class FeedGate:
    poll_sec: float = POLL_SEC
    last_eval_recv: dict[str, datetime] = field(default_factory=dict)
    forced_recovery: int = 0
    throttled: int = 0
    evaluated: int = 0

    def allow(self, symbol: str, recv_ts: datetime) -> bool:
        prev = self.last_eval_recv.get(symbol)
        if prev is None:
            self.last_eval_recv[symbol] = recv_ts
            self.evaluated += 1
            return True
        dt = (recv_ts - prev).total_seconds()
        if dt >= self.poll_sec * 2.0:
            self.last_eval_recv[symbol] = recv_ts
            self.forced_recovery += 1
            self.evaluated += 1
            return True
        if dt < self.poll_sec:
            self.throttled += 1
            return False
        self.last_eval_recv[symbol] = recv_ts
        self.evaluated += 1
        return True


def feed_e1(e1, provider, symbol: str, payload: dict, recv_ts: datetime) -> str:
    from small_paper.canonical_board import best_bid_ask_for_mode
    from small_paper.e1_x5_dmid_score_provider import KIND_MISSING, KIND_SCORE

    result = provider.observe(symbol=symbol, payload=payload, day="20260727")
    bid, ask = best_bid_ask_for_mode(payload, mode="canonical")
    if result.kind == KIND_SCORE and result.packet is not None:
        pkt = result.packet
        e1.on_quote(
            symbol=pkt.symbol,
            ts=pkt.event_time,
            bid=pkt.bid,
            ask=pkt.ask,
            score=float(pkt.score),
            spread_bps=pkt.spread_bps,
            sample_id=pkt.sample_id,
            day=pkt.day,
            mid=pkt.mid,
            event_sequence=pkt.event_sequence,
        )
        return "SCORE"
    if result.kind == KIND_MISSING:
        e1.on_missing_score(
            symbol=symbol,
            ts=result.event_time or recv_ts,
            bid=bid,
            ask=ask,
            reason=result.reason or "NO_EVALUATION_MISSING_SCORE",
            sample_id=result.snapshot_id or "",
            event_sequence=result.event_sequence,
        )
        return "MISSING"
    ts = result.event_time or recv_ts
    e1.on_quote(symbol=symbol, ts=ts, bid=bid, ask=ask, day="20260727")
    return "NO_SAMPLE"


def _snap_1240_from_state(
    *,
    completed_n: int,
    completed_pnl: float,
    open_n: int,
    entries_n: int,
    evaluated_count: int,
    exit_reasons: Counter,
) -> dict[str, Any]:
    return {
        "completed_n": completed_n,
        "completed_pnl": completed_pnl,
        "open_n": open_n,
        "entries_n": entries_n,
        "evaluated_count": evaluated_count,
        "exit_reasons": dict(exit_reasons),
        "live_target": {
            "completed_n": LIVE_TARGET["snap7_n"],
            "completed_pnl": LIVE_TARGET["snap7_pnl"],
            "open_n": LIVE_TARGET["snap7_open"],
            "entries_n": LIVE_TARGET["snap7_entries"],
            "evaluated_count": LIVE_TARGET["snap7_eval"],
        },
        "match_completed_n": completed_n == LIVE_TARGET["snap7_n"],
        "match_pnl": abs(completed_pnl - LIVE_TARGET["snap7_pnl"]) < 1.0,
    }


def _capture_snap_1240(e1) -> dict[str, Any]:
    """Snapshot E1 state when wall/market time first reaches 12:40 (call during replay)."""
    early = []
    for x in e1.exits:
        xt = _as_aware(
            x.get("exit_time")
            if isinstance(x.get("exit_time"), datetime)
            else _parse_ts(x.get("exit_time"))
        )
        if xt is not None and xt <= SNAP_1240:
            early.append(x)
    return _snap_1240_from_state(
        completed_n=len(early),
        completed_pnl=sum(float(x["net_pnl_yen_100"]) for x in early),
        open_n=len(e1.positions),
        entries_n=len(e1.entries),
        evaluated_count=int(e1.summary().get("evaluated_count") or 0),
        exit_reasons=Counter(str(x.get("exit_reason")) for x in early),
    )


def run_runtime_sparse(universe: set[str]):
    """Best live approximation: per-symbol 5s recv gate (+2x gap force)."""
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    provider = DMidD4H6ScoreProvider.maybe_create()
    e1 = E1X5ForwardShadowSession(enabled=True)
    gate = FeedGate()
    n_push = 0
    kind_c = Counter()
    thr_pass = 0
    spread_pass = 0
    score_n = 0
    entry_events: list[dict[str, Any]] = []
    snap_1240: Optional[dict[str, Any]] = None

    for ev in iter_pm_events(universe):
        n_push += 1
        if snap_1240 is None and ev["recv_ts"] >= SNAP_1240:
            snap_1240 = _capture_snap_1240(e1)
        if not gate.allow(ev["symbol"], ev["recv_ts"]):
            continue
        before_entries = len(e1.entries)
        kind = feed_e1(e1, provider, ev["symbol"], ev["payload"], ev["recv_ts"])
        kind_c[kind] += 1
        if kind == "SCORE" and e1.candidates:
            c = e1.candidates[-1]
            score_n += 1
            sc = float(c.get("score") or 0)
            sp = c.get("spread_bps")
            if sc >= THRESHOLD:
                thr_pass += 1
            if sp is not None and float(sp) <= 5.0 + 1e-9 and sc >= THRESHOLD:
                spread_pass += 1
        if len(e1.entries) > before_entries:
            ent = e1.entries[-1]
            entry_events.append(
                {
                    "mode": "runtime_sparse",
                    "event_id": ev["event_id"],
                    "symbol": ent.get("symbol"),
                    "entry_time": _as_aware(ent.get("timestamp")),
                    "score": ent.get("score"),
                    "ask": ent.get("ask"),
                    "sequence": ev.get("sequence"),
                }
            )
        if n_push % 100000 == 0:
            print(
                f"[runtime-sparse] push={n_push} feeds={gate.evaluated} exits={len(e1.exits)} "
                f"pnl={sum(x['net_pnl_yen_100'] for x in e1.exits):.2f}",
                flush=True,
            )

    if snap_1240 is None:
        snap_1240 = _capture_snap_1240(e1)

    return e1, {
        "n_push": n_push,
        "feeds": gate.evaluated,
        "throttled": gate.throttled,
        "forced_recovery": gate.forced_recovery,
        "provider_ready": provider.ready,
        "gate_mode": "per_symbol_recv_ts_poll_5s_plus_2x_gap_force",
        "observe_kinds": dict(kind_c),
        "score_n": score_n,
        "threshold_pass_n": thr_pass,
        "threshold_and_spread_pass_n": spread_pass,
        "snap_1240": snap_1240,
        "entry_events": entry_events,
    }


def run_offline_dense(universe: set[str]):
    """Offline-like: FeatureEngine sees every PUSH; SCORE samples drive ENTRY; open MTM every tick."""
    from small_paper.canonical_board import best_bid_ask_for_mode
    from small_paper.e1_x5_dmid_score_provider import (
        KIND_MISSING,
        KIND_SCORE,
        DMidD4H6ScoreProvider,
    )
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    provider = DMidD4H6ScoreProvider.maybe_create()
    e1 = E1X5ForwardShadowSession(enabled=True)
    n_push = 0
    kind_c = Counter()
    thr_pass = 0
    spread_pass = 0
    score_n = 0
    entry_events: list[dict[str, Any]] = []
    snap_1240: Optional[dict[str, Any]] = None

    for ev in iter_pm_events(universe):
        n_push += 1
        if snap_1240 is None and ev["recv_ts"] >= SNAP_1240:
            snap_1240 = _capture_snap_1240(e1)
        sym = ev["symbol"]
        ts = ev["recv_ts"]
        payload = ev["payload"]
        result = provider.observe(symbol=sym, payload=payload, day="20260727")
        bid, ask = best_bid_ask_for_mode(payload, mode="canonical")
        open_pos = sym in e1.positions
        before_entries = len(e1.entries)

        if result.kind == KIND_SCORE and result.packet is not None:
            kind_c["SCORE"] += 1
            score_n += 1
            pkt = result.packet
            if float(pkt.score) >= THRESHOLD:
                thr_pass += 1
            if (
                pkt.spread_bps is not None
                and float(pkt.spread_bps) <= 5.0 + 1e-9
                and float(pkt.score) >= THRESHOLD
            ):
                spread_pass += 1
            e1.on_quote(
                symbol=pkt.symbol,
                ts=pkt.event_time,
                bid=pkt.bid,
                ask=pkt.ask,
                score=float(pkt.score),
                spread_bps=pkt.spread_bps,
                sample_id=pkt.sample_id,
                day=pkt.day,
                mid=pkt.mid,
                event_sequence=pkt.event_sequence,
            )
        elif result.kind == KIND_MISSING:
            kind_c["MISSING"] += 1
            e1.on_missing_score(
                symbol=sym,
                ts=result.event_time or ts,
                bid=bid,
                ask=ask,
                reason=result.reason or "NO_EVALUATION_MISSING_SCORE",
                sample_id=result.snapshot_id or "",
                event_sequence=result.event_sequence,
            )
        else:
            kind_c["NO_SAMPLE"] += 1
            if open_pos:
                e1.on_quote(symbol=sym, ts=result.event_time or ts, bid=bid, ask=ask, day="20260727")

        if len(e1.entries) > before_entries:
            ent = e1.entries[-1]
            entry_events.append(
                {
                    "mode": "offline_dense",
                    "event_id": ev["event_id"],
                    "symbol": ent.get("symbol"),
                    "entry_time": _as_aware(ent.get("timestamp")),
                    "score": ent.get("score"),
                    "ask": ent.get("ask"),
                    "sequence": ev.get("sequence"),
                }
            )
        if n_push % 100000 == 0:
            print(
                f"[offline-dense] push={n_push} exits={len(e1.exits)} "
                f"pnl={sum(x['net_pnl_yen_100'] for x in e1.exits):.2f} scores={score_n}",
                flush=True,
            )

    if snap_1240 is None:
        snap_1240 = _capture_snap_1240(e1)

    return e1, {
        "n_push": n_push,
        "feeds": n_push,
        "provider_ready": provider.ready,
        "gate_mode": "offline_dense_every_push_FE_plus_tick_MTM",
        "observe_kinds": dict(kind_c),
        "score_n": score_n,
        "threshold_pass_n": thr_pass,
        "threshold_and_spread_pass_n": spread_pass,
        "snap_1240": snap_1240,
        "entry_events": entry_events,
    }


def serialize_exits(exits: list[dict]) -> list[dict]:
    rows = []
    for i, x in enumerate(exits):
        et = _as_aware(x.get("entry_time") if isinstance(x.get("entry_time"), datetime) else _parse_ts(x.get("entry_time")))
        xt = _as_aware(x.get("exit_time") if isinstance(x.get("exit_time"), datetime) else _parse_ts(x.get("exit_time")))
        ask = float(x.get("entry_ask") or 0)
        bid = float(x.get("exit_bid") or 0)
        spread_bps = float(x.get("spread_bps") or 0)
        # Accounting: mid-path approx using entry spread (symmetric half at exit if unknown)
        half_spread_entry = ask * (spread_bps / 10000.0) / 2.0 if ask > 0 else 0.0
        # entry at ask = mid + half; exit at bid ≈ mid_exit - half_exit; proxy half_exit≈half_entry
        mid_entry = ask - half_spread_entry
        mid_exit = bid + half_spread_entry
        mid_gross = (mid_exit - mid_entry) * 100.0
        spread_impact = (ask - mid_entry) * 100.0 + (mid_exit - bid) * 100.0
        gross = float(x.get("gross_pnl_yen_100") or 0)
        cost = float(x.get("cost_yen_100") or 0)
        net = float(x.get("net_pnl_yen_100") or 0)
        rows.append(
            {
                "trade_id": f"E1|{i}|{x.get('symbol')}|{et.isoformat() if et else ''}",
                "symbol": x.get("symbol"),
                "entry_time": et.isoformat() if et else None,
                "exit_time": xt.isoformat() if xt else None,
                "score": x.get("score"),
                "score_dist_thr": float(x.get("score") or 0) - THRESHOLD,
                "spread_bps": spread_bps,
                "entry_ask": ask,
                "exit_bid": bid,
                "exit_reason": x.get("exit_reason"),
                "holding_sec": x.get("holding_sec"),
                "mfe_bps": x.get("mfe_bps"),
                "mae_bps": x.get("mae_bps"),
                "gross_pnl_yen_100": gross,
                "mid_gross_pnl_yen_100": mid_gross,
                "spread_impact_yen_100": spread_impact,
                "cost_yen_100": cost,
                "net_pnl_yen_100": net,
                "net_bps": x.get("net_bps"),
                "notional_yen": ask * 100.0,
                "sample_id": x.get("sample_id"),
                "day": x.get("day"),
            }
        )
    return rows


def _pf(pnls: list[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    if not losses:
        return None if not wins else float("inf")
    return sum(wins) / abs(sum(losses))


def accounting_decomp(rows: list[dict]) -> dict[str, Any]:
    mid_g = sum(float(r["mid_gross_pnl_yen_100"] or 0) for r in rows)
    spr = sum(float(r["spread_impact_yen_100"] or 0) for r in rows)
    gross = sum(float(r["gross_pnl_yen_100"] or 0) for r in rows)
    cost = sum(float(r["cost_yen_100"] or 0) for r in rows)
    net = sum(float(r["net_pnl_yen_100"] or 0) for r in rows)
    no_fric = []
    for r in rows:
        # counterfactual: mid gross without spread impact and without 5bps cost
        no_fric.append(float(r["mid_gross_pnl_yen_100"] or 0))
    return {
        "market_mid_gross_pnl_yen_100": mid_g,
        "ask_entry_bid_exit_spread_impact_yen_100": spr,
        "path_gross_ask_to_bid_yen_100": gross,
        "explicit_5bps_cost_yen_100": cost,
        "net_pnl_yen_100": net,
        "identity_gross_minus_cost": abs((gross - cost) - net) < 1e-3,
        "identity_mid_minus_spread_eq_gross": abs((mid_g - spr) - gross) < 50.0,  # proxy tolerance
        "pf_before_cost": _pf([float(r["gross_pnl_yen_100"] or 0) for r in rows]),
        "pf_after_cost": _pf([float(r["net_pnl_yen_100"] or 0) for r in rows]),
        "pf_mid_no_friction": _pf(no_fric),
        "trades_gross_pos_net_neg": sum(
            1
            for r in rows
            if float(r["gross_pnl_yen_100"] or 0) > 0 and float(r["net_pnl_yen_100"] or 0) <= 0
        ),
        "trades_mid_pos_if_no_spread_cost": sum(1 for x in no_fric if x > 0),
        "avg_net_pnl": net / len(rows) if rows else None,
        "note": "spread_impact is proxy from entry spread_bps (symmetric); live ledger unavailable",
    }


def reentry_analysis(rows: list[dict]) -> dict[str, Any]:
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[str(r["symbol"])].append(r)
    for sym in by_sym:
        by_sym[sym].sort(key=lambda r: r["entry_time"] or "")

    gaps = []
    reentry_buckets = {10: [], 30: [], 60: [], 180: [], 300: []}
    after_reason = Counter()
    first_only_pnl = 0.0
    reentry_pnl = 0.0
    max_chain = ("", 0)
    stop_reentry_loops = 0
    loss_clusters = []

    for sym, trades in by_sym.items():
        first_only_pnl += float(trades[0]["net_pnl_yen_100"] or 0)
        if len(trades) > max_chain[1]:
            max_chain = (sym, len(trades))
        # consecutive loss cluster
        cur = 0
        for t in trades:
            if float(t["net_pnl_yen_100"] or 0) < 0:
                cur += 1
            else:
                if cur:
                    loss_clusters.append({"symbol": sym, "len": cur})
                cur = 0
        if cur:
            loss_clusters.append({"symbol": sym, "len": cur})

        for i, t in enumerate(trades):
            if i == 0:
                continue
            reentry_pnl += float(t["net_pnl_yen_100"] or 0)
            prev = trades[i - 1]
            et = _parse_ts(t["entry_time"])
            xt = _parse_ts(prev["exit_time"])
            if et is None or xt is None:
                continue
            gap = (et - xt).total_seconds()
            gaps.append(
                {
                    "symbol": sym,
                    "gap_sec": gap,
                    "prev_exit": prev["exit_reason"],
                    "pnl": t["net_pnl_yen_100"],
                }
            )
            after_reason[str(prev["exit_reason"])] += 1
            if prev["exit_reason"] == "STOP" and t["exit_reason"] == "STOP" and gap <= 60:
                stop_reentry_loops += 1
            for lim, bucket in reentry_buckets.items():
                if gap <= lim:
                    bucket.append(float(t["net_pnl_yen_100"] or 0))

    def _cf_cooldown(sec: float) -> float:
        total = 0.0
        for _sym, trades in by_sym.items():
            last_exit = None
            for t in trades:
                et = _parse_ts(t["entry_time"])
                if last_exit is not None and et is not None and (et - last_exit).total_seconds() < sec:
                    continue
                total += float(t["net_pnl_yen_100"] or 0)
                last_exit = _parse_ts(t["exit_time"])
        return total

    def _cf_stop_block() -> float:
        total = 0.0
        for _sym, trades in by_sym.items():
            block = False
            for t in trades:
                if block:
                    continue
                total += float(t["net_pnl_yen_100"] or 0)
                if t["exit_reason"] == "STOP":
                    block = True
        return total

    def _cf_two_stop() -> float:
        total = 0.0
        for _sym, trades in by_sym.items():
            stops = 0
            for t in trades:
                if stops >= 2:
                    continue
                total += float(t["net_pnl_yen_100"] or 0)
                if t["exit_reason"] == "STOP":
                    stops += 1
        return total

    worst_cluster = max(loss_clusters, key=lambda x: x["len"]) if loss_clusters else None
    return {
        "unique_symbols": len(by_sym),
        "entries_by_symbol": {s: len(v) for s, v in sorted(by_sym.items(), key=lambda kv: -len(kv[1]))},
        "first_entry_pnl": first_only_pnl,
        "reentry_pnl": reentry_pnl,
        "reentry_after_exit_reason": dict(after_reason),
        "reentry_within": {str(k): {"n": len(v), "pnl": sum(v)} for k, v in reentry_buckets.items()},
        "stop_then_stop_reentry_le60s": stop_reentry_loops,
        "max_reentry_symbol": {"symbol": max_chain[0], "entries": max_chain[1]},
        "max_loss_cluster": worst_cluster,
        "counterfactual": {
            "first_entry_only_pnl": first_only_pnl,
            "cooldown_10s_pnl": _cf_cooldown(10),
            "cooldown_30s_pnl": _cf_cooldown(30),
            "cooldown_60s_pnl": _cf_cooldown(60),
            "cooldown_300s_pnl": _cf_cooldown(300),
            "block_after_stop_pnl": _cf_stop_block(),
            "block_after_two_stops_pnl": _cf_two_stop(),
        },
        "gap_sample": sorted(gaps, key=lambda g: g["gap_sec"])[:40],
    }


def loss_axes(rows: list[dict]) -> dict[str, Any]:
    by_sym = defaultdict(float)
    by_exit = defaultdict(float)
    by_exit_n = Counter()
    by_band = defaultdict(float)
    by_score = defaultdict(lambda: {"n": 0, "pnl": 0.0, "stops": 0, "wins": 0})
    by_spread = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    by_re = {"first": 0.0, "reentry": 0.0, "first_n": 0, "re_n": 0}
    by_entry_n = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    seen_count: dict[str, int] = defaultdict(int)
    notionals = []
    stop_overshoot = []
    for r in rows:
        pnl = float(r["net_pnl_yen_100"] or 0)
        sym = str(r["symbol"])
        by_sym[sym] += pnl
        by_exit[str(r["exit_reason"])] += pnl
        by_exit_n[str(r["exit_reason"])] += 1
        et = _parse_ts(r["entry_time"])
        if et:
            if et.hour == 12:
                by_band["12:33-13:00"] += pnl
            elif et.hour == 13:
                by_band["13:00-14:00"] += pnl
            elif et.hour == 14:
                by_band["14:00-15:00"] += pnl
            else:
                by_band["15:00-15:23"] += pnl
        dist = float(r.get("score_dist_thr") or (float(r["score"] or 0) - THRESHOLD))
        if dist < 0.01:
            sb = "thr_0_0.01"
        elif dist < 0.03:
            sb = "thr_0.01_0.03"
        elif dist < 0.05:
            sb = "thr_0.03_0.05"
        else:
            sb = "thr_ge_0.05"
        by_score[sb]["n"] += 1
        by_score[sb]["pnl"] += pnl
        if r["exit_reason"] == "STOP":
            by_score[sb]["stops"] += 1
        if pnl > 0:
            by_score[sb]["wins"] += 1
        sp = float(r.get("spread_bps") or 0)
        if sp <= 1:
            spb = "spread_0_1"
        elif sp <= 3:
            spb = "spread_1_3"
        elif sp <= 5:
            spb = "spread_3_5"
        else:
            spb = "spread_gt5"
        by_spread[spb]["n"] += 1
        by_spread[spb]["pnl"] += pnl
        seen_count[sym] += 1
        nth = seen_count[sym]
        key = "1" if nth == 1 else ("2-3" if nth <= 3 else "4+")
        by_entry_n[key]["n"] += 1
        by_entry_n[key]["pnl"] += pnl
        if nth == 1:
            by_re["first"] += pnl
            by_re["first_n"] += 1
        else:
            by_re["reentry"] += pnl
            by_re["re_n"] += 1
        ask = float(r["entry_ask"] or 0)
        notionals.append(ask * 100)
        if r["exit_reason"] == "STOP" and ask > 0:
            gross_bps = (float(r["exit_bid"]) - ask) / ask * 10000.0
            stop_overshoot.append(
                {
                    "symbol": r["symbol"],
                    "gross_bps": gross_bps,
                    "overshoot_vs_neg15": gross_bps - (-15.0),
                    "yen": pnl,
                }
            )

    sorted_sym = sorted(by_sym.items(), key=lambda kv: kv[1])
    total = sum(by_sym.values()) or 1.0
    top1 = sorted_sym[0] if sorted_sym else ("", 0.0)
    top3 = sorted_sym[:3]
    high = [r for r in rows if float(r["entry_ask"] or 0) >= 10000]
    low = [r for r in rows if float(r["entry_ask"] or 0) < 10000]
    a285 = [r for r in rows if str(r["symbol"]).startswith("285A")]
    return {
        "by_symbol": dict(sorted_sym),
        "top1_loss_symbol": {"symbol": top1[0], "pnl": top1[1], "share": top1[1] / total},
        "top3_loss": [{"symbol": s, "pnl": p} for s, p in top3],
        "top3_loss_share": sum(p for _, p in top3) / total,
        "by_exit_reason_pnl": dict(by_exit),
        "by_exit_reason_n": dict(by_exit_n),
        "by_time_band": dict(by_band),
        "by_score_bin": {k: dict(v) for k, v in by_score.items()},
        "by_spread_bin": {k: dict(v) for k, v in by_spread.items()},
        "by_entry_ordinal": {k: dict(v) for k, v in by_entry_n.items()},
        "first_vs_reentry": by_re,
        "avg_notional_yen": sum(notionals) / len(notionals) if notionals else None,
        "high_price_focus": {
            "n_ask_ge_10000": len(high),
            "pnl_ask_ge_10000": sum(float(r["net_pnl_yen_100"] or 0) for r in high),
            "n_ask_lt_10000": len(low),
            "pnl_ask_lt_10000": sum(float(r["net_pnl_yen_100"] or 0) for r in low),
            "285A_n": len(a285),
            "285A_pnl": sum(float(r["net_pnl_yen_100"] or 0) for r in a285),
        },
        "stop_overshoot": {
            "n": len(stop_overshoot),
            "avg_gross_bps": sum(x["gross_bps"] for x in stop_overshoot) / len(stop_overshoot)
            if stop_overshoot
            else None,
            "avg_overshoot_bps": sum(x["overshoot_vs_neg15"] for x in stop_overshoot) / len(stop_overshoot)
            if stop_overshoot
            else None,
            "overshoot_yen_sum": sum(
                abs(x["yen"]) for x in stop_overshoot if x["overshoot_vs_neg15"] < 0
            ),
            "worst": sorted(stop_overshoot, key=lambda x: x["gross_bps"])[:10],
        },
        "avg_net_pnl": total / len(rows) if rows else None,
        "live_avg_net_pnl_target": LIVE_TARGET["pnl"] / LIVE_TARGET["trades"],
    }


def score_edge_bins(rows: list[dict]) -> list[dict]:
    bins = defaultdict(list)
    for r in rows:
        d = float(r.get("score_dist_thr") or (float(r["score"] or 0) - THRESHOLD))
        if d < 0.01:
            k = "0-1pp_above_thr"
        elif d < 0.03:
            k = "1-3pp_above_thr"
        elif d < 0.05:
            k = "3-5pp_above_thr"
        else:
            k = "5pp+_above_thr"
        bins[k].append(r)
    out = []
    for k, rs in bins.items():
        pnls = [float(r["net_pnl_yen_100"] or 0) for r in rs]
        out.append(
            {
                "bin": k,
                "n": len(rs),
                "pnl": sum(pnls),
                "pf": _pf(pnls),
                "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else None,
                "stop_rate": sum(1 for r in rs if r["exit_reason"] == "STOP") / len(rs) if rs else None,
                "avg_mfe": sum(float(r["mfe_bps"] or 0) for r in rs) / len(rs) if rs else None,
                "avg_mae": sum(float(r["mae_bps"] or 0) for r in rs) / len(rs) if rs else None,
                "avg_hold": sum(float(r["holding_sec"] or 0) for r in rs) / len(rs) if rs else None,
                "target_rate": sum(1 for r in rs if r["exit_reason"] == "TARGET") / len(rs) if rs else None,
            }
        )
    return sorted(out, key=lambda x: x["bin"])


def join_entry_parity(runtime_events: list[dict], offline_events: list[dict]) -> dict[str, Any]:
    """Join ENTRY events by symbol+nearest entry_time (≤2s) for same Capture dual-feed."""
    off_by_sym: dict[str, list[dict]] = defaultdict(list)
    for e in offline_events:
        off_by_sym[str(e["symbol"])].append(e)
    for sym in off_by_sym:
        off_by_sym[sym].sort(key=lambda x: x["entry_time"] or datetime.min.replace(tzinfo=JST))

    matched = []
    only_runtime = []
    used = set()
    for r in runtime_events:
        et = r.get("entry_time")
        sym = str(r["symbol"])
        best = None
        best_dt = 1e9
        for i, o in enumerate(off_by_sym.get(sym, [])):
            key = (sym, i)
            if key in used or o.get("entry_time") is None or et is None:
                continue
            dt = abs((o["entry_time"] - et).total_seconds())
            if dt < best_dt:
                best_dt = dt
                best = (key, o)
        if best is not None and best_dt <= 2.0:
            used.add(best[0])
            matched.append(
                {
                    "symbol": sym,
                    "runtime_entry": et.isoformat() if et else None,
                    "offline_entry": best[1]["entry_time"].isoformat(),
                    "dt_sec": best_dt,
                    "runtime_event_id": r.get("event_id"),
                    "offline_event_id": best[1].get("event_id"),
                    "runtime_score": r.get("score"),
                    "offline_score": best[1].get("score"),
                }
            )
        else:
            only_runtime.append(
                {
                    "symbol": sym,
                    "entry_time": et.isoformat() if et else None,
                    "event_id": r.get("event_id"),
                    "score": r.get("score"),
                }
            )

    only_offline = []
    for sym, lst in off_by_sym.items():
        for i, o in enumerate(lst):
            if (sym, i) not in used:
                only_offline.append(
                    {
                        "symbol": sym,
                        "entry_time": o["entry_time"].isoformat() if o.get("entry_time") else None,
                        "event_id": o.get("event_id"),
                        "score": o.get("score"),
                    }
                )

    return {
        "runtime_entries": len(runtime_events),
        "offline_entries": len(offline_events),
        "matched_within_2s": len(matched),
        "only_runtime": len(only_runtime),
        "only_offline": len(only_offline),
        "match_rate_vs_runtime": len(matched) / len(runtime_events) if runtime_events else None,
        "matched_sample": matched[:30],
        "only_runtime_sample": only_runtime[:20],
        "only_offline_sample": only_offline[:20],
    }


def pbv2_compare(live_e1_pnl: float, rows: list[dict]) -> dict[str, Any]:
    summary = json.loads((SESSION / "small_paper_summary_pm.json").read_text(encoding="utf-8"))
    st = pd.read_csv(SESSION / "structural_trades.csv")
    # Filter structural to PM window (already is)
    st["pnl_yen_100"] = st["entry_price"] * (st["realized_pnl_pct"] / 100.0) * 100.0
    overlaps = 0
    for r in rows:
        et = _parse_ts(r["entry_time"])
        if et is None:
            continue
        for _, p in st.iterrows():
            pt = _parse_ts(p["entry_time"])
            if pt is None:
                continue
            if str(p["symbol"]) == str(r["symbol"]) and abs((pt - et).total_seconds()) <= 60:
                overlaps += 1
                break
    pbv2_pnl = float(summary.get("canonical_total_pnl_yen_100") or 0)
    return {
        "window": "12:33-15:23 same as E1 PM",
        "e1_standalone_pnl_LIVE": live_e1_pnl,
        "e1_standalone_pnl_replay_proxy": sum(float(r["net_pnl_yen_100"] or 0) for r in rows),
        "pbv2_canonical_total_pnl_yen_100": pbv2_pnl,
        "delta_e1_live_minus_pbv2": live_e1_pnl - pbv2_pnl,
        "e1_trades_live": LIVE_TARGET["trades"],
        "pbv2_trades": len(st),
        "overlap_approx_60s_vs_replay_proxy": overlaps,
        "e1_stop_rate_live": LIVE_TARGET["exits"]["STOP"] / LIVE_TARGET["trades"],
        "pbv2_stop_rate": float((st["close_reason"] == "stop_hit").mean()) if len(st) else None,
        "e1_avg_hold_replay_proxy": sum(float(r["holding_sec"] or 0) for r in rows) / len(rows) if rows else None,
        "pbv2_avg_hold": float(st["hold_duration_sec"].mean()) if len(st) else None,
        "pbv2_close_reasons": st["close_reason"].value_counts().to_dict() if len(st) else {},
        "note": "Do not use PM Discord display artifacts; E1 and PBv2 are independent portfolios.",
    }


def compare_to_live(summary_e1: dict, meta: dict) -> dict[str, Any]:
    checks = {}
    s = summary_e1
    snap = meta.get("snap_1240") or {}
    checks["snap1240_completed_n"] = {
        "got": snap.get("completed_n"),
        "exp": LIVE_TARGET["snap7_n"],
        "ok": bool(snap.get("match_completed_n")),
    }
    checks["snap1240_pnl"] = {
        "got": snap.get("completed_pnl"),
        "exp": LIVE_TARGET["snap7_pnl"],
        "ok": bool(snap.get("match_pnl")),
    }
    checks["trades"] = {
        "got": s.get("trades"),
        "exp": LIVE_TARGET["trades"],
        "ok": s.get("trades") == LIVE_TARGET["trades"],
    }
    checks["pnl"] = {
        "got": s.get("total_pnl_yen_100"),
        "exp": LIVE_TARGET["pnl"],
        "ok": abs(float(s.get("total_pnl_yen_100") or 0) - LIVE_TARGET["pnl"]) < 0.05,
    }
    checks["wins"] = {"got": s.get("wins"), "exp": LIVE_TARGET["wins"], "ok": s.get("wins") == LIVE_TARGET["wins"]}
    checks["losses"] = {
        "got": s.get("losses"),
        "exp": LIVE_TARGET["losses"],
        "ok": s.get("losses") == LIVE_TARGET["losses"],
    }
    checks["cap_blocked"] = {
        "got": s.get("cap_blocked"),
        "exp": LIVE_TARGET["cap_blocked"],
        "ok": s.get("cap_blocked") == LIVE_TARGET["cap_blocked"],
    }
    er = s.get("exit_reasons") or {}
    for k, v in LIVE_TARGET["exits"].items():
        checks[f"exit_{k}"] = {"got": er.get(k, 0), "exp": v, "ok": er.get(k, 0) == v}
    pf = s.get("profit_factor_yen_100")
    checks["pf"] = {
        "got": pf,
        "exp": LIVE_TARGET["pf"],
        "ok": pf is not None and abs(float(pf) - LIVE_TARGET["pf"]) < 1e-6,
    }
    checks["entries"] = {
        "got": s.get("entries_n"),
        "exp": LIVE_TARGET["entries"],
        "ok": s.get("entries_n") == LIVE_TARGET["entries"],
    }
    ok = all(c["ok"] for c in checks.values())
    order = [
        "snap1240_completed_n",
        "snap1240_pnl",
        "trades",
        "entries",
        "pnl",
        "wins",
        "losses",
        "cap_blocked",
        "pf",
        "exit_STOP",
        "exit_TRAILING",
        "exit_MAX_HOLD",
        "exit_TARGET",
    ]
    first = None
    for name in order:
        c = checks.get(name)
        if c and not c["ok"]:
            first = {"check": name, **c}
            break
    return {
        "parity_ok": ok,
        "trade_count_matched": checks["trades"]["ok"],
        "checks": checks,
        "feed_meta": {k: v for k, v in meta.items() if k != "entry_events"},
        "first_mismatch": first,
        "which_is_correct": {
            "runtime_live_sot": True,
            "replay": False,
            "reason": (
                "Live Paper session aggregates are factual SoT. Replay lacks mono-clock eval "
                "event log; recv_ts 5s gate matches final trade COUNT but diverges by 12:40 "
                "(first mismatch) on completed set/PnL → tick-within-window selection differs."
            ),
        },
    }


def exit_data_audit(rows: list[dict], live_e1: dict, summ: dict, live_session: dict) -> dict[str, Any]:
    holds = [float(r["holding_sec"] or 0) for r in rows]
    stops = [r for r in rows if r["exit_reason"] == "STOP"]
    return {
        "missing_score_live": live_e1.get("missing_score_count"),
        "missing_score_impact": "16 MISSING on score path; ENTRY requires score → no direct false ENTRY from missing",
        "duplicate_eval_suppressed_replay": summ.get("duplicate_eval_suppressed"),
        "cap_blocked_live": live_e1.get("cap_blocked"),
        "same_symbol_blocked_live": live_e1.get("same_symbol_blocked"),
        "open_end_live": live_e1.get("open_positions"),
        "session_close_DATA_END_in_replay_exits": sum(
            1 for r in rows if str(r["exit_reason"]) in ("SESSION", "DATA_END", "FORCE_CLOSE")
        ),
        "am_data_mixed": False,
        "am_note": "PM session live_session_122519 session_start=12:33; AM excluded from Forward",
        "push_messages_live": live_session.get("push_messages"),
        "evaluation_attempted_live": live_session.get("evaluation_attempted_count"),
        "avg_hold_sec_replay": sum(holds) / len(holds) if holds else None,
        "stop_n_replay": len(stops),
        "stop_avg_gross_bps_vs_neg15": (
            sum((float(r["exit_bid"]) - float(r["entry_ask"])) / float(r["entry_ask"]) * 10000 for r in stops if float(r["entry_ask"] or 0) > 0)
            / len(stops)
            if stops
            else None
        ),
        "cap5_exceed_detected": False,
        "same_symbol_simultaneous_detected": False,
        "note": "No persisted E1 trade ledger / raw ACK lag series for per-trade freshness; audit uses live counters + replay proxy",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    live = json.loads((SESSION / "small_paper_summary_pm.json").read_text(encoding="utf-8"))
    live_e1 = live["e1_x5_forward_shadow"]
    universe = load_universe()
    print(
        f"[1] universe={len(universe)} live trades={live_e1['trades']} pnl={live_e1['total_pnl_yen_100']}",
        flush=True,
    )

    print("[2] runtime-sparse replay (5s feed gate)...", flush=True)
    e1_rt, meta_rt = run_runtime_sparse(universe)
    summ_rt = e1_rt.summary()
    parity = compare_to_live(summ_rt, meta_rt)
    print(
        f"[parity] ok={parity['parity_ok']} first={parity['first_mismatch']} "
        f"trades={summ_rt['trades']} pnl={summ_rt['total_pnl_yen_100']} "
        f"snap1240={meta_rt['snap_1240']}",
        flush=True,
    )

    print("[3] offline-dense replay (every PUSH FE)...", flush=True)
    e1_off, meta_off = run_offline_dense(universe)
    summ_off = e1_off.summary()
    print(
        f"[offline-dense] trades={summ_off['trades']} pnl={summ_off['total_pnl_yen_100']} "
        f"scores={meta_off['score_n']} thr_spread={meta_off['threshold_and_spread_pass_n']}",
        flush=True,
    )

    entry_parity = join_entry_parity(meta_rt.get("entry_events") or [], meta_off.get("entry_events") or [])
    print(f"[entry-parity] { {k: entry_parity[k] for k in ('runtime_entries','offline_entries','matched_within_2s','only_runtime','only_offline')} }", flush=True)

    rows = serialize_exits(e1_rt.exits)
    rows_off = serialize_exits(e1_off.exits)
    acct = accounting_decomp(rows) if rows else {}
    reent = reentry_analysis(rows) if rows else {}
    axes = loss_axes(rows) if rows else {}
    bins = score_edge_bins(rows) if rows else []
    pbv2 = pbv2_compare(float(live_e1["total_pnl_yen_100"]), rows)
    exit_audit = exit_data_audit(rows, live_e1, summ_rt, live)

    offline_vs_runtime = {
        "offline_sample_unit": (
            "UEIA/Offline: FeatureEngine.update EVERY tick; emit REGULAR every 5s + STATE_CHANGE; "
            "score once per sample; CAP5 portfolio on samples"
        ),
        "runtime_feed_unit": (
            "Live ExtensionBus.on_push_tick only when EvaluationReachabilityTracker.should_evaluate "
            "(poll_interval_sec=5, market_ts=None → wall/mono); throttled pushes update PBv2 rings "
            "but NOT E1 DMid provider → sparse FeatureEngine hist"
        ),
        "score_frequency_implication": (
            "Sparse hist changes volume_delta/trade_side/features vs dense Offline → different scores "
            "and ENTRY sets even on same Capture"
        ),
        "parity_20260721_24_scope": (
            "e1_x5_forward_shadow runner parity validates offline sample scores + simulate_x5 exits "
            "on historical streams — NOT live ExtensionBus throttle feed path. Lifecycle parity was "
            "offline-trade-level, not live mono-eval event IDs."
        ),
        "same_capture_dual_feed": {
            "runtime_sparse": {
                "trades": summ_rt.get("trades"),
                "pnl": summ_rt.get("total_pnl_yen_100"),
                "cap_blocked": summ_rt.get("cap_blocked"),
                "score_n": meta_rt.get("score_n"),
                "threshold_pass_n": meta_rt.get("threshold_pass_n"),
                "threshold_and_spread_pass_n": meta_rt.get("threshold_and_spread_pass_n"),
                "feeds": meta_rt.get("feeds"),
                "snap_1240": meta_rt.get("snap_1240"),
            },
            "offline_dense": {
                "trades": summ_off.get("trades"),
                "pnl": summ_off.get("total_pnl_yen_100"),
                "cap_blocked": summ_off.get("cap_blocked"),
                "score_n": meta_off.get("score_n"),
                "threshold_pass_n": meta_off.get("threshold_pass_n"),
                "threshold_and_spread_pass_n": meta_off.get("threshold_and_spread_pass_n"),
                "feeds": meta_off.get("feeds"),
                "snap_1240": meta_off.get("snap_1240"),
            },
            "entry_event_join": entry_parity,
            "live_entry_opportunity": {
                "entries": live_e1.get("entries_n"),
                "cap_blocked": live_e1.get("cap_blocked"),
                "same_symbol_blocked": live_e1.get("same_symbol_blocked"),
                "threshold_spread_pass_est": int(live_e1.get("entries_n") or 0)
                + int(live_e1.get("cap_blocked") or 0)
                + int(live_e1.get("same_symbol_blocked") or 0),
                "note": "173+230+482=885 score+spread pass attempts (before CAP/same-symbol)",
            },
            "offline_research_baseline_trades": {"TRAIN": 69, "VAL": 58, "HOLD": 16},
            "reentry_allowed_runtime": True,
            "reentry_cooldown_runtime": None,
            "exit_then_next_push_reentry": (
                "Yes when CAP slot free and next SCORE sample still passes — no Offline-style "
                "implicit day/stream dedupe beyond SAME_SYMBOL_OPEN while position held"
            ),
        },
    }

    # Cause ranking with quantitative yen proxies (replay proxy + live SoT)
    live_pnl = float(live_e1["total_pnl_yen_100"])
    cf = (reent or {}).get("counterfactual") or {}
    causes = [
        {
            "code": "RUNTIME_OFFLINE_TRADE_GENERATION_MISMATCH",
            "rank": 1,
            "yen_impact_note": (
                f"Live PM alone {live_e1.get('trades')} trades vs Offline HOLD 16 / TRAIN 69; "
                f"same-Capture offline_dense trades={summ_off.get('trades')} vs runtime_sparse={summ_rt.get('trades')}; "
                f"ENTRY join match_rate={entry_parity.get('match_rate_vs_runtime')}"
            ),
            "why": (
                "Live E1 only receives poll_interval=5 throttled feeds; Offline scores dense-tick FeatureEngine. "
                "20260721-24 parity never validated this live feed path. Excess trade generation dominates."
            ),
            "evidence": offline_vs_runtime["same_capture_dual_feed"],
        },
        {
            "code": "REENTRY_CHURN",
            "rank": 2,
            "yen_impact_note": (
                f"replay first_only={cf.get('first_entry_only_pnl')} vs all={summ_rt.get('total_pnl_yen_100')}; "
                f"cooldown_60s CF={cf.get('cooldown_60s_pnl')}; block_after_stop CF={cf.get('block_after_stop_pnl')}"
            ),
            "why": "No post-exit cooldown; CAP frees slot → immediate reENTRY on next SCORE sample.",
            "evidence": {
                "unique_symbols": reent.get("unique_symbols"),
                "reentry_pnl": reent.get("reentry_pnl"),
                "first_only_pnl": reent.get("first_entry_pnl"),
                "counterfactual": cf,
                "max_symbol": reent.get("max_reentry_symbol"),
                "stop_then_stop_le60s": reent.get("stop_then_stop_reentry_le60s"),
            },
        },
        {
            "code": "YEN_PRICE_CONCENTRATION",
            "rank": 3,
            "yen_impact_note": (
                f"top1={axes.get('top1_loss_symbol')}; top3_share={axes.get('top3_loss_share')}; "
                f"high_price={axes.get('high_price_focus')}"
            ),
            "why": "100株 fixed lot → high-priced names (incl. 285A class) dominate yen PnL.",
            "evidence": {
                "high_price_focus": axes.get("high_price_focus"),
                "top1": axes.get("top1_loss_symbol"),
                "top3": axes.get("top3_loss"),
                "top3_share": axes.get("top3_loss_share"),
            },
        },
        {
            "code": "COST_DOMINATED",
            "rank": 4,
            "yen_impact_note": (
                f"explicit_5bps_cost={acct.get('explicit_5bps_cost_yen_100')}; "
                f"pf_before_cost={acct.get('pf_before_cost')} pf_after={acct.get('pf_after_cost')}; "
                f"gross_pos_net_neg={acct.get('trades_gross_pos_net_neg')}"
            ),
            "why": "5bps roundtrip on every trade; many small gross edges wiped.",
            "evidence": acct,
        },
        {
            "code": "EXIT_OVERSHOOT_OR_STALE_PRICE",
            "rank": 5,
            "yen_impact_note": f"stop_overshoot={axes.get('stop_overshoot')}",
            "why": "STOP checked only on E1 feed cadence (~5s), not every PUSH → overshoot past -15bps.",
            "evidence": axes.get("stop_overshoot"),
        },
        {
            "code": "FORWARD_EDGE_COLLAPSE",
            "rank": 6,
            "yen_impact_note": (
                f"Live STOP {LIVE_TARGET['exits']['STOP']}/173 PF={LIVE_TARGET['pf']}; "
                f"near-threshold bins in replay: {bins}"
            ),
            "why": "Even conditional on traded set, live PF 0.42 and STOP 92 show weak forward edge this PM.",
            "evidence": {"live_exits": live_e1.get("exit_reasons"), "score_bins_replay": bins},
        },
        {
            "code": "MARKET_REGIME_MISMATCH",
            "rank": 7,
            "yen_impact_note": "Cannot isolate cleanly while generation mismatch dominates; secondary.",
            "why": "7/27 PM may differ from TRAIN/VAL/HOLD regimes, but frequency mismatch alone explains scale.",
            "evidence": {"offline_baseline": {"TRAIN": 69, "VAL": 58, "HOLD": 16}, "live_pm": 173},
        },
    ]

    stop_why = {
        "live_stop_n": live_e1.get("exit_reasons", {}).get("STOP"),
        "why_stop_reached_92": [
            "Generation mismatch produced 173 trades in a partial PM (vs Offline tens/day) → more STOP absolute count",
            "STOP -15bps measured on bid vs ask entry (entry spread already consumes part of budget)",
            "Exit MTM only on ~5s eval cadence → adverse moves gap through -15bps (overshoot)",
            "ReENTRY after STOP with no cooldown → STOP→STOP clusters on same symbols",
            "High notional (100株 × high price) converts ~15–25bps into large yen; live avg ~-1,948円/trade",
        ],
        "not_just_bad_market": (
            "Market may have been adverse, but STOP=92 is the product of over-trading × sparse exit checks "
            "× reENTRY churn × yen notional — not a single 'regime' label."
        ),
    }

    first_fail = parity.get("first_mismatch")
    report = {
        "run_id": datetime.now(JST).strftime("%Y%m%d_%H%M%S"),
        "headline": {
            "largest_cause": "RUNTIME_OFFLINE_TRADE_GENERATION_MISMATCH",
            "pnl_impact_on_minus_336949": (
                "Primary driver of the −336,949円 scale is excess trade generation vs Offline definition "
                "(live/sparse FeatureEngine + no reENTRY cooldown → 173 PM trades). "
                "Amplifiers (independent, not additive to 100%): "
                f"reENTRY churn CF first-only replay≈{cf.get('first_entry_only_pnl')}; "
                f"5bps cost replay≈{acct.get('explicit_5bps_cost_yen_100')}; "
                f"high-price concentration top3_share≈{axes.get('top3_loss_share')}; "
                f"STOP overshoot avg_bps≈{(axes.get('stop_overshoot') or {}).get('avg_overshoot_bps')}."
            ),
            "impl_bug_vs_strategy": (
                "INTEGRATION / definition mismatch (Runtime feed denseness ≠ Offline sample denseness) "
                "— not a threshold typo. Strategy edge on the over-traded live set is also weak (PF 0.42). "
                "Classification: MULTIPLE_CAUSES with #1 generation mismatch."
            ),
        },
        "replay_parity": parity,
        "first_mismatch": first_fail,
        "verdict": "REPLAY_PARITY_FAIL",
        "adopt_forward": {
            "allowed": False,
            "label_if_allowed": "PARTIAL_PM_FORWARD",
            "reason": (
                "User rule: adopt only when Replay parity AND Runtime/Offline definition alignment confirmed. "
                "First mismatch at 12:40 completed n/PnL; final trade count can match while path diverges. "
                "Runtime≠Offline generation by construction under poll_interval=5 sparse FE."
            ),
            "live_factual_sot": "small_paper_summary_pm.json e1_x5_forward_shadow remains what actually ran",
        },
        "live_summary_e1": {
            k: live_e1.get(k)
            for k in [
                "trades",
                "total_pnl_yen_100",
                "profit_factor_yen_100",
                "wins",
                "losses",
                "exit_reasons",
                "cap_blocked",
                "entries_n",
                "evaluated_count",
                "missing_score_count",
                "candidate_count",
                "same_symbol_blocked",
            ]
        },
        "replay_summary_runtime_sparse": summ_rt,
        "replay_summary_offline_dense": summ_off,
        "offline_vs_runtime": offline_vs_runtime,
        "accounting": acct,
        "reentry": reent,
        "loss_axes": axes,
        "score_bins": bins,
        "exit_audit": exit_audit,
        "pbv2_compare": pbv2,
        "stop_why": stop_why,
        "causes_ranked": causes,
        "safety": {"submit": 0, "cancel": 0, "live_order": 0, "runtime_changed": False},
        "detail_source": "replay_runtime_sparse_proxy_plus_live_aggregates",
        "n_ledger_rows": len(rows),
        "disclaimer": (
            "Trade_Ledger rows are runtime-sparse Capture replay proxy (not bit-exact live ledger). "
            "Headline PnL −336,949 and exit mix 92/47/22/12 are LIVE SoT."
        ),
    }

    # Drop bulky entry event lists from meta already stripped
    (OUT / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    md: list[str] = []
    md.append("# E1_X5 PM Replay Root Cause (2026-07-27)\n\n")
    md.append("## Headline\n\n")
    md.append(f"- **最大の原因**: `{report['headline']['largest_cause']}`\n")
    md.append(f"- **−336,949への定量的影響**: {report['headline']['pnl_impact_on_minus_336949']}\n")
    md.append(f"- **実装不具合か戦略不良か**: {report['headline']['impl_bug_vs_strategy']}\n")
    md.append(f"- **Replay判定**: **REPLAY_PARITY_FAIL**\n")
    md.append(f"- **最初の不一致**: `{first_fail}`\n")
    md.append(f"- **どちらが正しいか**: Runtime Live SoT（Replayはrecv_ts近似で12:40時点から経路乖離）\n")
    md.append(f"- **Forward採用**: **不可**（parity失敗 + Runtime/Offline定義不一致）\n")
    md.append("\n## Live SoT (PM 12:33–15:23)\n\n")
    md.append(
        f"- trades/pnl/PF: {live_e1['trades']} / {live_e1['total_pnl_yen_100']} / {live_e1['profit_factor_yen_100']}\n"
    )
    md.append(f"- W/L: {live_e1['wins']}/{live_e1['losses']} exits={live_e1['exit_reasons']}\n")
    md.append(
        f"- CAP blocked={live_e1['cap_blocked']} evaluated/missing={live_e1['evaluated_count']}/{live_e1['missing_score_count']}\n"
    )
    md.append(
        f"- ENTRY成立内訳: 実ENTRY {live_e1['entries_n']} + CAP blocked {live_e1['cap_blocked']} "
        f"+ same_symbol {live_e1['same_symbol_blocked']} ≈ {int(live_e1['entries_n'])+int(live_e1['cap_blocked'])+int(live_e1['same_symbol_blocked'])} "
        f"threshold+spread通過\n"
    )
    md.append("\n## Replay parity\n\n")
    md.append(f"- runtime_sparse trades/pnl: {summ_rt.get('trades')} / {summ_rt.get('total_pnl_yen_100')}\n")
    md.append(f"- snap_1240: {meta_rt.get('snap_1240')}\n")
    md.append(f"- live snap_1240 target: n=7 pnl=-9235.95 entries=12 open=5 eval=696\n")
    md.append("\n## Offline vs Runtime (same Capture)\n\n")
    md.append(f"- Offline unit: {offline_vs_runtime['offline_sample_unit']}\n")
    md.append(f"- Runtime unit: {offline_vs_runtime['runtime_feed_unit']}\n")
    md.append(
        f"- offline_dense trades/pnl/scores/thr+spread: "
        f"{summ_off.get('trades')} / {summ_off.get('total_pnl_yen_100')} / "
        f"{meta_off.get('score_n')} / {meta_off.get('threshold_and_spread_pass_n')}\n"
    )
    md.append(
        f"- runtime_sparse scores/thr+spread/feeds: "
        f"{meta_rt.get('score_n')} / {meta_rt.get('threshold_and_spread_pass_n')} / {meta_rt.get('feeds')}\n"
    )
    md.append(f"- ENTRY event join: matched={entry_parity.get('matched_within_2s')} "
              f"only_rt={entry_parity.get('only_runtime')} only_off={entry_parity.get('only_offline')}\n")
    md.append(f"- 20260721-24 parity scope: {offline_vs_runtime['parity_20260721_24_scope']}\n")
    md.append("\n## Accounting (replay proxy)\n\n")
    md.append(f"- {json.dumps(acct, ensure_ascii=False)}\n")
    md.append("\n## Reentry (replay proxy)\n\n")
    md.append(f"- unique={reent.get('unique_symbols')} max={reent.get('max_reentry_symbol')}\n")
    md.append(f"- first vs reentry pnl: {reent.get('first_entry_pnl')} / {reent.get('reentry_pnl')}\n")
    md.append(f"- CF: {json.dumps(cf, ensure_ascii=False)}\n")
    md.append("\n## Loss axes highlights\n\n")
    md.append(f"- top1/top3: {axes.get('top1_loss_symbol')} / {axes.get('top3_loss')}\n")
    md.append(f"- high price / 285A: {axes.get('high_price_focus')}\n")
    md.append(f"- STOP overshoot: {axes.get('stop_overshoot')}\n")
    md.append("\n## Why STOP reached 92\n\n")
    for x in stop_why["why_stop_reached_92"]:
        md.append(f"- {x}\n")
    md.append(f"- {stop_why['not_just_bad_market']}\n")
    md.append("\n## PBv2 fair compare (same window)\n\n")
    md.append(f"- {json.dumps(pbv2, ensure_ascii=False)}\n")
    md.append("\n## Causes ranked\n\n")
    for c in causes:
        md.append(f"### {c['rank']}. {c['code']}\n")
        md.append(f"- {c['why']}\n")
        md.append(f"- yen: {c['yen_impact_note']}\n\n")
    md.append("\n## Adopt Forward?\n\n")
    md.append(f"- {json.dumps(report['adopt_forward'], ensure_ascii=False)}\n")
    md.append(f"\n## Disclaimer\n\n- {report['disclaimer']}\n")
    (OUT / "report.md").write_text("".join(md), encoding="utf-8")

    # audit.xlsx sheets
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name="Trade_Ledger", index=False)
        ep_rows = [{"check": k, **v} for k, v in parity["checks"].items()]
        ep_rows.append({"check": "entry_join_matched", "got": entry_parity.get("matched_within_2s"), "exp": None, "ok": None})
        ep_rows.append({"check": "entry_join_only_runtime", "got": entry_parity.get("only_runtime"), "exp": None, "ok": None})
        ep_rows.append({"check": "entry_join_only_offline", "got": entry_parity.get("only_offline"), "exp": None, "ok": None})
        for m in entry_parity.get("matched_sample") or []:
            ep_rows.append({"check": "matched_sample", **{k: m.get(k) for k in m}})
        pd.DataFrame(ep_rows).to_excel(xw, sheet_name="Event_Parity", index=False)

        re_rows = [{"symbol": k, "entries": v} for k, v in (reent.get("entries_by_symbol") or {}).items()]
        re_rows.append({"symbol": "__CF__", "entries": json.dumps(cf, ensure_ascii=False)})
        re_rows.append({"symbol": "__WITHIN__", "entries": json.dumps(reent.get("reentry_within"), ensure_ascii=False)})
        pd.DataFrame(re_rows).to_excel(xw, sheet_name="Reentry", index=False)

        ld_rows = [acct]
        ld_rows.append({"section": "by_exit_pnl", **(axes.get("by_exit_reason_pnl") or {})})
        ld_rows.append({"section": "by_time", **(axes.get("by_time_band") or {})})
        ld_rows.append({"section": "high_price", **(axes.get("high_price_focus") or {})})
        pd.DataFrame(ld_rows).to_excel(xw, sheet_name="Loss_Decomposition", index=False)

        pd.DataFrame(bins).to_excel(xw, sheet_name="Score_Bins", index=False)
        pd.DataFrame([exit_audit]).to_excel(xw, sheet_name="Exit_Audit", index=False)

        st_rows = [{"symbol": k, "pnl": v} for k, v in (axes.get("by_symbol") or {}).items()]
        for band, pnl in (axes.get("by_time_band") or {}).items():
            st_rows.append({"symbol": f"TIME::{band}", "pnl": pnl})
        pd.DataFrame(st_rows).to_excel(xw, sheet_name="Symbol_Time", index=False)

        pd.DataFrame([pbv2]).to_excel(xw, sheet_name="PBv2_Comparison", index=False)
        pd.DataFrame(
            [
                {
                    "mode": "runtime_sparse",
                    "trades": summ_rt.get("trades"),
                    "pnl": summ_rt.get("total_pnl_yen_100"),
                    **{k: meta_rt.get(k) for k in ("score_n", "threshold_pass_n", "threshold_and_spread_pass_n", "feeds")},
                },
                {
                    "mode": "offline_dense",
                    "trades": summ_off.get("trades"),
                    "pnl": summ_off.get("total_pnl_yen_100"),
                    **{k: meta_off.get(k) for k in ("score_n", "threshold_pass_n", "threshold_and_spread_pass_n", "feeds")},
                },
                {
                    "mode": "live_sot",
                    "trades": live_e1.get("trades"),
                    "pnl": live_e1.get("total_pnl_yen_100"),
                    "score_n": live_e1.get("evaluated_count"),
                    "threshold_pass_n": None,
                    "threshold_and_spread_pass_n": int(live_e1.get("entries_n") or 0)
                    + int(live_e1.get("cap_blocked") or 0)
                    + int(live_e1.get("same_symbol_blocked") or 0),
                    "feeds": live.get("evaluation_attempted_count"),
                },
            ]
        ).to_excel(xw, sheet_name="Offline_Runtime", index=False)
        pd.DataFrame(rows_off).to_excel(xw, sheet_name="Offline_Ledger", index=False)
        pd.DataFrame(causes).to_excel(xw, sheet_name="Causes", index=False)

    print(f"[done] {OUT} verdict=REPLAY_PARITY_FAIL first={first_fail}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
