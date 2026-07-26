"""cost_aware_entry_shadow — observe-only Cap5 Winner+STOP no-fill Shadow.

W54-FIX Final (NP excluded from decisions):
  integrated_score = z(pbv2_score) + 0.35 * winner_enrichment - 0.45 * z(stop_risk)

Enable: COST_AWARE_ENTRY_SHADOW (Paper default ON; elsewhere OFF unless set).
Explicit OFF: COST_AWARE_ENTRY_SHADOW=0
Does NOT block/add real ENTRY, Discord ENTRY, orders, or W43F plumbing.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.forward_observer_defaults import resolve_cost_aware_entry_shadow
from small_paper.cost_aware_price_path import last_valid_price_at_or_before, parse_ts as _parse_ts_helper

JST = ZoneInfo("Asia/Tokyo")

OWNERSHIP = "RESEARCH"
SHADOW_NAME = "cost_aware_entry_shadow"
CAP = 5
HOLD_MINUTES = 30.0
# Cross-sectional stop reject (approx top ~5% of cycle stop_risk)
STOP_Z_REJECT = 1.65
COST_PCT_5BPS = 0.05  # roundtrip 5bps once per completed trade (= 0.05% of notional)


def _cost_yen_5bps(entry_price: float) -> float:
    """Round-trip 5bps cost in yen for 100 shares."""
    return float(entry_price) * 100.0 * (COST_PCT_5BPS / 100.0)


def _yen_100(entry_price: float, exit_price: float) -> float:
    from replay.pnl_yen import compute_pnl_yen_100

    return round(compute_pnl_yen_100(entry_price, exit_price), 2)


def shadow_enabled(cfg: Any = None) -> bool:
    enabled, _src = resolve_cost_aware_entry_shadow(cfg)
    return enabled


def shadow_enabled_with_source(cfg: Any = None) -> tuple[bool, str]:
    return resolve_cost_aware_entry_shadow(cfg)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except (TypeError, ValueError):
        return default


def _cs_z(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    arr = list(values)
    mu = sum(arr) / len(arr)
    var = sum((x - mu) ** 2 for x in arr) / max(1, len(arr) - 1) if len(arr) > 1 else 0.0
    sd = math.sqrt(var) if var > 1e-18 else 1.0
    return [(x - mu) / sd for x in arr]


def compute_runtime_features(trade: Mapping[str, Any]) -> dict[str, float]:
    """Map live trade fields → pbv2 / stop_risk / enrichment inputs (NP not used)."""
    pbv2 = trade.get("entry_expectancy_score_v2")
    if pbv2 is None:
        pbv2 = trade.get("continuation_quality_score")
    rise = _f(trade.get("entry_rise_5min_pct") or trade.get("r60_sec"))
    spread = _f(trade.get("spread_bps"))
    near_high = _f(trade.get("entry_near_day_high_pct") or trade.get("day_high_distance_pct"))
    vwap = _f(trade.get("entry_vwap_dev_pct") or trade.get("vwap_dev_pct"))
    mom = _f(trade.get("entry_momentum_continuation_score") or trade.get("momentum_continuation_score"))
    # stop_risk: chase / exhaustion proxy (higher = worse)
    stop_risk = (
        1.0 * rise
        + 0.3 * (spread / 10.0)
        + 0.5 * max(0.0, near_high)
        + 0.2 * max(0.0, vwap)
        - 0.4 * mom
    )
    # winner enrichment proxies (0–3); refined cross-sectionally in cycle
    return {
        "pbv2_score": _f(pbv2),
        "stop_risk_score": stop_risk,
        "rise": rise,
        "spread": spread,
        "near_high": near_high,
        "vwap": vwap,
        "mom": mom,
        # audit-only NP proxy — NEVER used for reject/score/rank
        "np_risk_score_audit": _f(trade.get("np_risk_score")),
    }


def winner_enrichment_from_cycle(feats: list[dict[str, float]]) -> list[float]:
    """Cycle-relative winner rules (no NP)."""
    if not feats:
        return []
    rises = [f["rise"] for f in feats]
    spreads = [f["spread"] for f in feats]
    moms = [f["mom"] for f in feats]
    nears = [f["near_high"] for f in feats]

    def q(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        i = int(max(0, min(len(s) - 1, round(p * (len(s) - 1)))))
        return s[i]

    r_hi, r_lo = q(rises, 0.8), q(rises, 0.2)
    sp_lo = q(spreads, 0.2)
    mom_hi = q(moms, 0.8)
    near_hi = q(nears, 0.8)
    out = []
    for f in feats:
        n = 0.0
        # high rise × low spread (pressure without wide book)
        if f["rise"] >= r_hi and f["spread"] <= sp_lo:
            n += 1
        # high momentum × not extended near high
        if f["mom"] >= mom_hi and f["near_high"] <= near_hi:
            n += 1
        # moderate rise (not chase) with momentum
        if r_lo <= f["rise"] <= r_hi and f["mom"] >= mom_hi:
            n += 1
        out.append(n)
    return out


def integrated_score(*, z_pbv2: float, winner_enrichment: float, z_stop: float) -> float:
    """Final score — NP term intentionally absent."""
    return float(z_pbv2) + 0.35 * float(winner_enrichment) - 0.45 * float(z_stop)


@dataclass
class ShadowPosition:
    symbol: str
    entry_time: datetime
    entry_price: float
    selection_cycle_id: str
    rank: int
    integrated_score: float
    winner_enrichment: float
    stop_risk: float
    stop_margin_z: float
    pbv2_score: float
    # exits filled later
    fixed_30m_exit_time: Optional[datetime] = None
    fixed_30m_exit_price: Optional[float] = None
    fixed_30m_pnl: Optional[float] = None
    official_runtime_exit_time: Optional[datetime] = None
    official_runtime_exit_price: Optional[float] = None
    official_runtime_exit_pnl: Optional[float] = None
    shadow_exit_policy: str = "fixed_30m"
    shadow_exit_time: Optional[datetime] = None
    shadow_exit_price: Optional[float] = None
    shadow_exit_pnl: Optional[float] = None
    closed: bool = False
    # Phase678 price tracking
    last_mark_price: Optional[float] = None
    last_mark_time: Optional[datetime] = None
    price_path: list[tuple[datetime, float]] = field(default_factory=list)
    shadow_exit_reason: Optional[str] = None
    shadow_exit_price_source: Optional[str] = None
    price_age_sec: Optional[float] = None
    evaluation_type: Optional[str] = None
    gross_pnl_yen_100: Optional[float] = None
    cost_bps: float = 5.0
    net_pnl_yen_100: Optional[float] = None
    is_recovery_finalize: bool = False
    # runtime-compatible (separate from fixed_30m)
    runtime_compatible_exit_time: Optional[datetime] = None
    runtime_compatible_exit_price: Optional[float] = None
    runtime_compatible_gross_yen: Optional[float] = None
    runtime_compatible_net_yen: Optional[float] = None
    runtime_compatible_price_source: Optional[str] = None
    runtime_compatible_price_age_sec: Optional[float] = None
    runtime_compatible_na: bool = False


@dataclass
class CostAwareShadowState:
    events: list[dict] = field(default_factory=list)
    cycles: list[dict] = field(default_factory=list)
    open_shadow: dict[str, ShadowPosition] = field(default_factory=dict)
    closed_trades: list[dict] = field(default_factory=list)
    # scan_id -> list of {symbol, trade, features}
    pending_cycle: dict[str, list[dict]] = field(default_factory=dict)
    # counters
    selection_cycles: int = 0
    shadow_eligible: int = 0
    stop_risk_reject: int = 0
    same_snapshot_nofill: int = 0
    later_fill: int = 0
    never_filled: int = 0
    shadow_entries: int = 0
    official_match: int = 0
    official_mismatch: int = 0
    pending_unfilled: list[dict] = field(default_factory=list)

    def log_path(self, trading_date: str) -> Path:
        root = Path("results/research/pre_entry_market_state/cost_aware_entry_shadow_logs")
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{trading_date}_cost_aware_entry_shadow.jsonl"


def append_shadow_event(state: CostAwareShadowState, trading_date: str, event: dict) -> None:
    state.events.append(event)
    try:
        with state.log_path(str(trading_date or "unknown")).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def note_symbol_eval(
    state: CostAwareShadowState,
    *,
    scan_id: str,
    symbol: str,
    trade: Mapping[str, Any],
    official_accept: bool = False,
) -> None:
    """Accumulate every universe-active eval into the current selection cycle."""
    feats = compute_runtime_features(trade)
    state.pending_cycle.setdefault(scan_id, []).append(
        {
            "symbol": str(symbol),
            "trade": dict(trade),
            "features": feats,
            "official_accept": bool(official_accept),
            "entry_price": _f(trade.get("entry_price") or trade.get("CurrentPrice") or trade.get("current_price")),
        }
    )


def _parse_ts(v: Any) -> Optional[datetime]:
    return _parse_ts_helper(v)


def run_selection_cycle(
    state: CostAwareShadowState,
    *,
    scan_id: str,
    cycle_time: Optional[datetime] = None,
    trading_date: str = "",
    official_accepted_symbols: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Evaluate full pending cycle with Cap5 no-fill. NP never used for decisions."""
    rows = state.pending_cycle.pop(scan_id, [])
    # also clear any orphaned older cycles that were never flushed? keep only this scan
    t = cycle_time or datetime.now(JST)
    # close expired shadow positions (fixed 30m)
    _close_expired(state, now=t, trading_date=trading_date)

    free_before = CAP - len(state.open_shadow)
    open_before = list(state.open_shadow.keys())
    official_set = {str(s) for s in (official_accepted_symbols or [])}

    if not rows:
        state.selection_cycles += 1
        cycle = {
            "selection_cycle_id": scan_id,
            "snapshot_time": t.isoformat(),
            "n_universe": 0,
            "free_slots_before": free_before,
            "accepted": [],
            "rejected": [],
            "unfilled_slots_after": free_before,
        }
        state.cycles.append(cycle)
        return cycle

    feats = [r["features"] for r in rows]
    z_pb = _cs_z([f["pbv2_score"] for f in feats])
    z_st = _cs_z([f["stop_risk_score"] for f in feats])
    we = winner_enrichment_from_cycle(feats)

    scored = []
    for i, r in enumerate(rows):
        score = integrated_score(z_pbv2=z_pb[i], winner_enrichment=we[i], z_stop=z_st[i])
        scored.append(
            {
                **r,
                "z_pbv2": z_pb[i],
                "z_stop": z_st[i],
                "winner_enrichment": we[i],
                "integrated_score": score,
                "np_risk_audit": r["features"].get("np_risk_score_audit"),
            }
        )
    scored.sort(key=lambda x: x["integrated_score"], reverse=True)

    slots = free_before
    accepted: list[str] = []
    rejected: list[dict] = []
    rank_slots_used = 0
    selected = 0

    for rank_i, row in enumerate(scored, start=1):
        sym = row["symbol"]
        if sym in state.open_shadow:
            continue
        stop_reject = row["z_stop"] >= STOP_Z_REJECT
        # NP MUST NOT reject
        if stop_reject:
            state.stop_risk_reject += 1
            rejected.append({"symbol": sym, "reason": "stop_risk", "rank": rank_i, "z_stop": row["z_stop"]})
            # no-fill: consume rank opportunity, do not walk to next for this slot
            if rank_slots_used < slots:
                rank_slots_used += 1
                state.same_snapshot_nofill += 1
                state.pending_unfilled.append(
                    {
                        "cycle_id": scan_id,
                        "t": t,
                        "rejected_symbol": sym,
                        "resolved": False,
                    }
                )
            continue

        state.shadow_eligible += 1
        if selected >= slots:
            break
        if rank_slots_used >= slots:
            break
        rank_slots_used += 1

        px = row["entry_price"] if row["entry_price"] > 0 else _f(row["trade"].get("CurrentPrice"))
        pos = ShadowPosition(
            symbol=sym,
            entry_time=t,
            entry_price=px,
            selection_cycle_id=scan_id,
            rank=rank_i,
            integrated_score=row["integrated_score"],
            winner_enrichment=row["winner_enrichment"],
            stop_risk=row["features"]["stop_risk_score"],
            stop_margin_z=STOP_Z_REJECT - row["z_stop"],
            pbv2_score=row["features"]["pbv2_score"],
        )
        state.open_shadow[sym] = pos
        accepted.append(sym)
        selected += 1
        state.shadow_entries += 1
        # later-fill attribution
        for pend in state.pending_unfilled:
            if pend.get("resolved"):
                continue
            if t > pend["t"]:
                pend["resolved"] = True
                pend["later_symbol"] = sym
                pend["delay_sec"] = (t - pend["t"]).total_seconds()
                state.later_fill += 1
                break
        if sym in official_set:
            state.official_match += 1
        elif official_set:
            state.official_mismatch += 1

        append_shadow_event(
            state,
            trading_date,
            {
                "event": "shadow_entry",
                "selection_cycle_id": scan_id,
                "symbol": sym,
                "shadow_rank": rank_i,
                "integrated_score": row["integrated_score"],
                "z_pbv2": row["z_pbv2"],
                "z_stop": row["z_stop"],
                "winner_enrichment": row["winner_enrichment"],
                "stop_risk": row["features"]["stop_risk_score"],
                "np_risk_audit_only": row.get("np_risk_audit"),
                "shadow_entry_time": t.isoformat(),
                "shadow_entry_price": px,
                "shadow_eligible": True,
                "shadow_reject_reason": None,
                "shadow_no_fill": False,
                "official_accept": sym in official_set,
                "comparison_to_official_pbv2": "match" if sym in official_set else "mismatch_or_no_official",
            },
        )

    # count never-filled at end of day elsewhere; here mark unresolved older than hold
    unfilled_after = CAP - len(state.open_shadow)
    state.selection_cycles += 1
    cycle = {
        "selection_cycle_id": scan_id,
        "snapshot_time": t.isoformat(),
        "n_universe": len(rows),
        "active_positions_before": open_before,
        "free_slots_before": free_before,
        "candidate_symbols_ordered": [r["symbol"] for r in scored],
        "candidate_scores": [r["integrated_score"] for r in scored],
        "rejected": rejected,
        "accepted": accepted,
        "unfilled_slots_after": unfilled_after,
        "official_accepted": list(official_set),
        "score_formula": "z(pbv2)+0.35*winner_enrichment-0.45*z(stop_risk)",
        "np_in_decision": False,
    }
    state.cycles.append(cycle)
    append_shadow_event(state, trading_date, {"event": "selection_cycle", **cycle})
    return cycle


def _apply_fixed_close(
    pos: ShadowPosition,
    *,
    exit_time: datetime,
    exit_price: float,
    reason: str,
    price_source: str,
    price_age_sec: float,
    is_recovery_finalize: bool = False,
) -> None:
    pos.fixed_30m_exit_time = exit_time
    pos.fixed_30m_exit_price = float(exit_price)
    pos.shadow_exit_policy = reason
    pos.shadow_exit_time = exit_time
    pos.shadow_exit_price = float(exit_price)
    pos.shadow_exit_reason = reason
    pos.shadow_exit_price_source = price_source
    pos.price_age_sec = float(price_age_sec)
    pos.evaluation_type = "fixed_30m"
    pos.is_recovery_finalize = bool(is_recovery_finalize)
    if pos.entry_price > 0:
        pos.fixed_30m_pnl = (float(exit_price) / pos.entry_price - 1.0) * 100.0
        pos.shadow_exit_pnl = pos.fixed_30m_pnl
        pos.gross_pnl_yen_100 = _yen_100(pos.entry_price, float(exit_price))
        pos.net_pnl_yen_100 = round(pos.gross_pnl_yen_100 - _cost_yen_5bps(pos.entry_price), 2)
    pos.closed = True


def _close_expired(
    state: CostAwareShadowState,
    *,
    now: datetime,
    trading_date: str,
    price_paths: Optional[Mapping[str, Sequence[tuple[datetime, float]]]] = None,
) -> None:
    """Close positions held >= 30m using last valid price at entry+30m (no future leak)."""
    to_close = []
    for sym, pos in state.open_shadow.items():
        held = (now - pos.entry_time).total_seconds() / 60.0
        if held >= HOLD_MINUTES:
            to_close.append(sym)
    for sym in to_close:
        pos = state.open_shadow.pop(sym)
        target = pos.entry_time + timedelta(minutes=HOLD_MINUTES)
        path = list(pos.price_path)
        if price_paths and sym in price_paths:
            path = list(price_paths[sym])
        hit = last_valid_price_at_or_before(path, asof=target, not_before=pos.entry_time)
        if hit is None and pos.last_mark_price and pos.last_mark_time and pos.last_mark_time <= target:
            hit = (pos.last_mark_time, float(pos.last_mark_price), (target - pos.last_mark_time).total_seconds())
        if hit is None:
            # defer: put back until price arrives or session finalize
            state.open_shadow[sym] = pos
            continue
        _pts, px, age = hit
        _apply_fixed_close(
            pos,
            exit_time=target,
            exit_price=px,
            reason="fixed_30m",
            price_source="last_valid_before_entry_plus_30m",
            price_age_sec=age,
        )
        row = _trade_row(pos)
        state.closed_trades.append(row)
        append_shadow_event(state, trading_date, {"event": "shadow_exit_fixed_30m", **row})


def mark_price_for_open(state: CostAwareShadowState, symbol: str, price: float, ts: Any = None) -> None:
    """Update mark for open shadow; finalize fixed_30m pnl when exit time reached."""
    pos = state.open_shadow.get(str(symbol))
    if pos is None or price <= 0:
        return
    now = _parse_ts(ts) or datetime.now(JST)
    if now < pos.entry_time:
        return  # future/past leak guard for entry
    pos.last_mark_price = float(price)
    pos.last_mark_time = now
    pos.price_path.append((now, float(price)))
    if (now - pos.entry_time).total_seconds() / 60.0 >= HOLD_MINUTES:
        target = pos.entry_time + timedelta(minutes=HOLD_MINUTES)
        # only use this tick if it is not after target (no future price for 30m eval)
        if now <= target:
            px = float(price)
            age = (target - now).total_seconds()
            src = "mark_at_or_before_30m"
        else:
            hit = last_valid_price_at_or_before(pos.price_path, asof=target, not_before=pos.entry_time)
            if hit is None:
                return
            _pts, px, age = hit
            src = "last_valid_before_entry_plus_30m"
        _apply_fixed_close(
            pos,
            exit_time=target,
            exit_price=px,
            reason="fixed_30m",
            price_source=src,
            price_age_sec=age,
        )
        state.open_shadow.pop(str(symbol), None)
        state.closed_trades.append(_trade_row(pos))


def apply_runtime_compatible_exit(
    pos: ShadowPosition,
    *,
    exit_time: datetime,
    exit_price: Optional[float],
    price_source: str,
    price_age_sec: Optional[float] = None,
    na: bool = False,
    join_failure_reason: Optional[str] = None,
) -> None:
    """Attach runtime-compatible evaluation (separate from fixed_30m)."""
    pos.runtime_compatible_exit_time = exit_time
    if na or exit_price is None or exit_price <= 0 or pos.entry_price <= 0:
        pos.runtime_compatible_na = True
        pos.runtime_compatible_exit_price = None
        pos.runtime_compatible_gross_yen = None
        pos.runtime_compatible_net_yen = None
        pos.runtime_compatible_price_source = str(join_failure_reason or price_source or "N/A")
        return
    pos.runtime_compatible_na = False
    pos.runtime_compatible_exit_price = float(exit_price)
    pos.runtime_compatible_price_source = price_source
    pos.runtime_compatible_price_age_sec = price_age_sec
    pos.runtime_compatible_gross_yen = _yen_100(pos.entry_price, float(exit_price))
    pos.runtime_compatible_net_yen = round(
        pos.runtime_compatible_gross_yen - _cost_yen_5bps(pos.entry_price), 2
    )
    pos.official_runtime_exit_time = exit_time
    pos.official_runtime_exit_price = float(exit_price)
    pos.official_runtime_exit_pnl = (float(exit_price) / pos.entry_price - 1.0) * 100.0


def attach_runtime_compatible_to_closed_trades(
    closed_trades: Sequence[Mapping[str, Any]],
    *,
    official_exits: Sequence[tuple[datetime, str, float, str]],
    price_paths: Mapping[str, Sequence[tuple[datetime, float]]],
    force_close_time: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach runtime-compatible PnL to every closed virtual trade (causal; no future prices).

    official_exits: (exit_time, symbol, exit_price, reason) sorted ascending by time.
    For each shadow entry, uses the next official EXIT clock (any symbol) as the
    evaluation time, then takes last valid price of the *virtual* symbol at/before
    that time. Same-symbol recovery_forced_close uses the formal recovery price.
    Force-close / session-end always attempts a fill; failures are explicit reasons.
    """
    exits_sorted = sorted(official_exits, key=lambda x: x[0])
    recovery_by_sym: dict[str, tuple[datetime, float]] = {}
    for ts, sym, px, reason in exits_sorted:
        if reason == "recovery_forced_close" and px > 0:
            recovery_by_sym[sym] = (ts, px)

    enriched: list[dict[str, Any]] = []
    join_success = 0
    join_failed = 0
    reason_counts: dict[str, int] = {}

    for row_in in closed_trades:
        row = dict(row_in)
        sym = str(row.get("symbol") or "")
        et = _parse_ts(row.get("shadow_entry_time") or row.get("entry_time"))
        try:
            ep = float(row.get("shadow_entry_price") or row.get("entry_price") or 0)
        except (TypeError, ValueError):
            ep = 0.0
        if et is None or ep <= 0:
            row["runtime_compatible_na"] = True
            row["join_status"] = "JOIN_FAILED"
            row["join_failure_reason"] = "JOIN_FAILED"
            row["runtime_compatible_price_source"] = "JOIN_FAILED"
            join_failed += 1
            reason_counts["JOIN_FAILED"] = reason_counts.get("JOIN_FAILED", 0) + 1
            enriched.append(row)
            continue

        next_exit = None
        for ts, _o_sym, px, reason in exits_sorted:
            if ts > et:
                next_exit = (ts, _o_sym, px, reason)
                break
        asof = next_exit[0] if next_exit else force_close_time
        src = "runtime_next_official_exit_time" if next_exit else "session_force_close_clock"
        px_out: Optional[float] = None
        age: Optional[float] = None
        fail_reason: Optional[str] = None

        if sym in recovery_by_sym and recovery_by_sym[sym][0] >= et:
            asof = recovery_by_sym[sym][0]
            px_out = recovery_by_sym[sym][1]
            src = "formal_recovery_exit_price"
            age = 0.0
        else:
            hit = last_valid_price_at_or_before(
                list(price_paths.get(sym, [])), asof=asof, not_before=et
            )
            if hit is None:
                # Fall back to fixed/session exit price already on the row (force-close path).
                try:
                    fallback_px = float(
                        row.get("shadow_exit_price")
                        or row.get("fixed_30m_exit_price")
                        or 0
                    )
                except (TypeError, ValueError):
                    fallback_px = 0.0
                if fallback_px > 0 and str(row.get("shadow_exit_price_source") or "") != "N/A_NO_PRICE_PATH":
                    px_out = fallback_px
                    src = "shadow_exit_price_fallback"
                    age = row.get("price_age_sec")
                else:
                    fail_reason = "NO_PRICE_PATH" if not next_exit else "NO_PRICE_PATH"
            else:
                _pts, px_out, age = hit
                src = "last_valid_before_runtime_exit_time"

        pos = ShadowPosition(
            symbol=sym,
            entry_time=et,
            entry_price=ep,
            selection_cycle_id=str(row.get("selection_cycle_id") or ""),
            rank=int(row.get("rank") or 0),
            integrated_score=float(row.get("integrated_score") or 0),
            winner_enrichment=float(row.get("winner_enrichment") or 0),
            stop_risk=float(row.get("stop_risk") or 0),
            stop_margin_z=0.0,
            pbv2_score=0.0,
        )
        if fail_reason or px_out is None or px_out <= 0:
            reason = fail_reason or ("NO_RUNTIME_EXIT" if not next_exit else "JOIN_FAILED")
            apply_runtime_compatible_exit(
                pos,
                exit_time=asof,
                exit_price=None,
                price_source=reason,
                price_age_sec=age,
                na=True,
                join_failure_reason=reason,
            )
            row.update(
                {
                    "runtime_compatible_exit_time": asof.isoformat(),
                    "runtime_compatible_exit_price": None,
                    "runtime_compatible_gross_yen": None,
                    "runtime_compatible_net_yen": None,
                    "runtime_compatible_price_source": reason,
                    "runtime_compatible_price_age_sec": age,
                    "runtime_compatible_na": True,
                    "join_status": reason,
                    "join_failure_reason": reason,
                }
            )
            join_failed += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        else:
            apply_runtime_compatible_exit(
                pos,
                exit_time=asof,
                exit_price=px_out,
                price_source=src,
                price_age_sec=age,
                na=False,
            )
            row.update(
                {
                    "runtime_compatible_exit_time": pos.runtime_compatible_exit_time.isoformat()
                    if pos.runtime_compatible_exit_time
                    else None,
                    "runtime_compatible_exit_price": pos.runtime_compatible_exit_price,
                    "runtime_compatible_gross_yen": pos.runtime_compatible_gross_yen,
                    "runtime_compatible_net_yen": pos.runtime_compatible_net_yen,
                    "runtime_compatible_price_source": pos.runtime_compatible_price_source,
                    "runtime_compatible_price_age_sec": pos.runtime_compatible_price_age_sec,
                    "runtime_compatible_na": False,
                    "official_runtime_exit_time": pos.official_runtime_exit_time.isoformat()
                    if isinstance(pos.official_runtime_exit_time, datetime)
                    else pos.official_runtime_exit_time,
                    "official_runtime_exit_price": pos.official_runtime_exit_price,
                    "official_runtime_exit_pnl": pos.official_runtime_exit_pnl,
                    "join_status": "CLOSED_READY",
                    "join_failure_reason": None,
                    "runtime_compatible_raw_pnl": pos.runtime_compatible_gross_yen,
                    "runtime_compatible_pnl_5bps": pos.runtime_compatible_net_yen,
                    "cost_aware_counterfactual_raw_pnl": row.get("gross_pnl_yen_100"),
                    "cost_aware_counterfactual_pnl_5bps": row.get("net_pnl_yen_100"),
                }
            )
            if isinstance(row.get("gross_pnl_yen_100"), (int, float)) and isinstance(
                pos.runtime_compatible_gross_yen, (int, float)
            ):
                row["delta_raw"] = round(
                    float(row["gross_pnl_yen_100"]) - float(pos.runtime_compatible_gross_yen), 2
                )
            if isinstance(row.get("net_pnl_yen_100"), (int, float)) and isinstance(
                pos.runtime_compatible_net_yen, (int, float)
            ):
                row["delta_5bps"] = round(
                    float(row["net_pnl_yen_100"]) - float(pos.runtime_compatible_net_yen), 2
                )
            join_success += 1
            reason_counts["CLOSED_READY"] = reason_counts.get("CLOSED_READY", 0) + 1
        enriched.append(row)

    stats = {
        "join_success_count": join_success,
        "join_failed_count": join_failed,
        "pending_count": 0,
        "delta_eligible_count": join_success,
        "join_failure_reasons": reason_counts,
    }
    return enriched, stats


def finalize_open_positions(
    state: CostAwareShadowState,
    *,
    force_close_time: datetime,
    trading_date: str = "",
    price_paths: Optional[Mapping[str, Sequence[tuple[datetime, float]]]] = None,
    is_freeze_recovery: bool = False,
) -> int:
    """Force-close all open virtual positions at session/freeze finalize.

    Uses last valid price at or before force_close_time (no future leak).
    """
    closed_n = 0
    for sym in list(state.open_shadow.keys()):
        pos = state.open_shadow.pop(sym)
        path = list(pos.price_path)
        if price_paths and sym in price_paths:
            path = list(price_paths[sym])
        # If held < 30m until session end, exit_time = force_close; else still force at session
        target_30 = pos.entry_time + timedelta(minutes=HOLD_MINUTES)
        if target_30 <= force_close_time:
            # should have closed at 30m; recover with 30m price if possible
            asof = target_30
            reason = "fixed_30m_finalize"
            src = "last_valid_before_entry_plus_30m"
        else:
            asof = force_close_time
            reason = "freeze_recovery_finalize" if is_freeze_recovery else "session_force_close"
            src = "last_valid_before_session_force_close"
        hit = last_valid_price_at_or_before(path, asof=asof, not_before=pos.entry_time)
        if hit is None and pos.last_mark_price and pos.last_mark_time and pos.last_mark_time <= asof:
            hit = (
                pos.last_mark_time,
                float(pos.last_mark_price),
                (asof - pos.last_mark_time).total_seconds(),
            )
        if hit is None:
            # explicit N/A price — still close position at entry (0 yen) with N/A source flagged
            _apply_fixed_close(
                pos,
                exit_time=asof,
                exit_price=pos.entry_price,
                reason=reason,
                price_source="N/A_NO_PRICE_PATH",
                price_age_sec=0.0,
                is_recovery_finalize=is_freeze_recovery or reason == "session_force_close",
            )
            pos.gross_pnl_yen_100 = 0.0
            pos.net_pnl_yen_100 = round(0.0 - _cost_yen_5bps(pos.entry_price), 2)
        else:
            _pts, px, age = hit
            _apply_fixed_close(
                pos,
                exit_time=asof,
                exit_price=px,
                reason=reason,
                price_source=src,
                price_age_sec=age,
                is_recovery_finalize=is_freeze_recovery or reason.startswith("session"),
            )
        state.closed_trades.append(_trade_row(pos))
        append_shadow_event(state, trading_date, {"event": "shadow_exit_finalize", **_trade_row(pos)})
        closed_n += 1
    return closed_n


def mark_official_exit(
    state: CostAwareShadowState,
    *,
    symbol: str,
    exit_time: Any,
    exit_price: float,
    exit_pnl_pct: Optional[float] = None,
) -> None:
    """Attach official runtime exit if same symbol was (or is) a shadow trade."""
    sym = str(symbol)
    pos = state.open_shadow.get(sym)
    target = pos
    et = _parse_ts(exit_time)
    if target is None:
        for t in reversed(state.closed_trades):
            if t.get("symbol") == sym and t.get("official_runtime_exit_time") in (None, "N/A"):
                t["official_runtime_exit_time"] = str(exit_time)
                t["official_runtime_exit_price"] = exit_price
                t["official_runtime_exit_pnl"] = exit_pnl_pct
                return
        return
    target.official_runtime_exit_time = et
    target.official_runtime_exit_price = float(exit_price) if exit_price else None
    if exit_pnl_pct is not None:
        target.official_runtime_exit_pnl = float(exit_pnl_pct)
    elif target.entry_price > 0 and exit_price:
        target.official_runtime_exit_pnl = (float(exit_price) / target.entry_price - 1.0) * 100.0


def _trade_row(pos: ShadowPosition) -> dict[str, Any]:
    return {
        "virtual_position_id": f"{pos.symbol}_{pos.entry_time.isoformat()}",
        "symbol": pos.symbol,
        "shadow_entry_time": pos.entry_time.isoformat() if pos.entry_time else None,
        "shadow_entry_price": pos.entry_price,
        "entry_time": pos.entry_time.isoformat() if pos.entry_time else None,
        "entry_price": pos.entry_price,
        "fixed_30m_exit_time": pos.fixed_30m_exit_time.isoformat() if pos.fixed_30m_exit_time else None,
        "fixed_30m_exit_price": pos.fixed_30m_exit_price,
        "fixed_30m_pnl": pos.fixed_30m_pnl,
        "official_runtime_exit_time": (
            pos.official_runtime_exit_time.isoformat()
            if isinstance(pos.official_runtime_exit_time, datetime)
            else (pos.official_runtime_exit_time or "N/A")
        ),
        "official_runtime_exit_price": pos.official_runtime_exit_price
        if pos.official_runtime_exit_price is not None
        else "N/A",
        "official_runtime_exit_pnl": pos.official_runtime_exit_pnl
        if pos.official_runtime_exit_pnl is not None
        else "N/A",
        "shadow_exit_policy": pos.shadow_exit_policy,
        "shadow_exit_time": pos.shadow_exit_time.isoformat() if pos.shadow_exit_time else None,
        "shadow_exit_price": pos.shadow_exit_price,
        "shadow_exit_pnl": pos.shadow_exit_pnl,
        "shadow_exit_reason": pos.shadow_exit_reason,
        "shadow_exit_price_source": pos.shadow_exit_price_source,
        "price_age_sec": pos.price_age_sec,
        "evaluation_type": pos.evaluation_type,
        "gross_pnl_yen_100": pos.gross_pnl_yen_100,
        "cost_bps": pos.cost_bps,
        "net_pnl_yen_100": pos.net_pnl_yen_100,
        "is_recovery_finalize": pos.is_recovery_finalize,
        "runtime_compatible_exit_time": (
            pos.runtime_compatible_exit_time.isoformat()
            if isinstance(pos.runtime_compatible_exit_time, datetime)
            else None
        ),
        "runtime_compatible_exit_price": pos.runtime_compatible_exit_price,
        "runtime_compatible_gross_yen": pos.runtime_compatible_gross_yen,
        "runtime_compatible_net_yen": pos.runtime_compatible_net_yen,
        "runtime_compatible_price_source": pos.runtime_compatible_price_source,
        "runtime_compatible_price_age_sec": pos.runtime_compatible_price_age_sec,
        "runtime_compatible_na": pos.runtime_compatible_na,
        "selection_cycle_id": pos.selection_cycle_id,
        "rank": pos.rank,
        "integrated_score": pos.integrated_score,
        "winner_enrichment": pos.winner_enrichment,
        "stop_risk": pos.stop_risk,
    }


def finalize_never_filled(state: CostAwareShadowState) -> None:
    for pend in state.pending_unfilled:
        if not pend.get("resolved"):
            state.never_filled += 1
            pend["never_filled"] = True


def _wl_flat(yens: Sequence[float]) -> tuple[int, int, int]:
    return (
        sum(1 for y in yens if y > 0),
        sum(1 for y in yens if y < 0),
        sum(1 for y in yens if y == 0),
    )


def _pf_from_yen(yens: Sequence[float]) -> Optional[float]:
    gp = sum(y for y in yens if y > 0)
    gl = abs(sum(y for y in yens if y < 0))
    if gl > 1e-12:
        return round(gp / gl, 4)
    if gp > 0:
        return 999.0
    return None


def summarize_state(state: CostAwareShadowState) -> dict[str, Any]:
    closed = list(state.closed_trades)
    # Prefer yen_100 fields; fall back to pct→not formal
    raw_30 = [
        t["gross_pnl_yen_100"]
        for t in closed
        if isinstance(t.get("gross_pnl_yen_100"), (int, float))
    ]
    net_30 = [
        t["net_pnl_yen_100"]
        for t in closed
        if isinstance(t.get("net_pnl_yen_100"), (int, float))
    ]
    # legacy pct path (not used as formal when yen present)
    pnls_pct = [t["fixed_30m_pnl"] for t in closed if isinstance(t.get("fixed_30m_pnl"), (int, float))]
    del pnls_pct
    if raw_30:
        gross = round(sum(raw_30), 2)
        net5 = round(sum(net_30), 2) if net_30 else None
        pf = _pf_from_yen(net_30) if net_30 else None
    elif not closed:
        gross = 0.0
        net5 = 0.0
        pf = None
    else:
        # closed without yen → incomplete (do not publish fake 0)
        gross = None
        net5 = None
        pf = None

    rt_raw = [
        t["runtime_compatible_gross_yen"]
        for t in closed
        if isinstance(t.get("runtime_compatible_gross_yen"), (int, float)) and not t.get("runtime_compatible_na")
    ]
    rt_net = [
        t["runtime_compatible_net_yen"]
        for t in closed
        if isinstance(t.get("runtime_compatible_net_yen"), (int, float)) and not t.get("runtime_compatible_na")
    ]
    # also accept official_runtime_exit_pnl converted? keep separate
    w30, l30, f30 = _wl_flat(net_30) if net_30 else (0, 0, 0)
    wrt, lrt, frt = _wl_flat(rt_net) if rt_net else (0, 0, 0)
    freeze_n = sum(1 for t in closed if t.get("is_recovery_finalize") or str(t.get("shadow_exit_reason") or "").startswith("freeze"))
    session_n = sum(1 for t in closed if str(t.get("shadow_exit_reason") or "") in ("session_force_close", "freeze_recovery_finalize"))

    join_success = sum(1 for t in closed if t.get("join_status") == "CLOSED_READY" or (
        isinstance(t.get("runtime_compatible_gross_yen"), (int, float)) and not t.get("runtime_compatible_na")
    ))
    join_failed = sum(
        1
        for t in closed
        if t.get("runtime_compatible_na")
        or str(t.get("join_status") or "") in ("JOIN_FAILED", "NO_PRICE_PATH", "NO_RUNTIME_EXIT")
    )
    # Avoid double-count: if na but also counted in success path above, prefer explicit na
    join_success = sum(
        1
        for t in closed
        if isinstance(t.get("runtime_compatible_gross_yen"), (int, float)) and not t.get("runtime_compatible_na")
    )
    join_failed = len(closed) - join_success
    reason_counts: dict[str, int] = {}
    for t in closed:
        if isinstance(t.get("runtime_compatible_gross_yen"), (int, float)) and not t.get("runtime_compatible_na"):
            reason_counts["CLOSED_READY"] = reason_counts.get("CLOSED_READY", 0) + 1
        else:
            r = str(t.get("join_failure_reason") or t.get("join_status") or t.get("runtime_compatible_price_source") or "JOIN_FAILED")
            reason_counts[r] = reason_counts.get(r, 0) + 1

    open_n = len(state.open_shadow)
    runtime_total_raw = round(sum(rt_raw), 2) if rt_raw else (None if closed else 0.0)
    runtime_total_5bps = round(sum(rt_net), 2) if rt_net else (None if closed else 0.0)
    rt_pf = _pf_from_yen(rt_net) if rt_net else None
    # Paired delta only over join-success rows (same denominator).
    paired_ca_raw = [
        float(t["gross_pnl_yen_100"])
        for t in closed
        if isinstance(t.get("runtime_compatible_gross_yen"), (int, float))
        and not t.get("runtime_compatible_na")
        and isinstance(t.get("gross_pnl_yen_100"), (int, float))
    ]
    paired_ca_net = [
        float(t["net_pnl_yen_100"])
        for t in closed
        if isinstance(t.get("runtime_compatible_net_yen"), (int, float))
        and not t.get("runtime_compatible_na")
        and isinstance(t.get("net_pnl_yen_100"), (int, float))
    ]
    paired_ca_pf = _pf_from_yen(paired_ca_net) if paired_ca_net else None
    pf_delta = None
    if isinstance(paired_ca_pf, (int, float)) and isinstance(rt_pf, (int, float)):
        pf_delta = round(float(paired_ca_pf) - float(rt_pf), 4)
    elif isinstance(pf, (int, float)) and isinstance(rt_pf, (int, float)) and join_failed == 0:
        pf_delta = round(float(pf) - float(rt_pf), 4)
    delta_raw = None
    delta_5bps = None
    if paired_ca_raw and isinstance(runtime_total_raw, (int, float)):
        delta_raw = round(sum(paired_ca_raw) - float(runtime_total_raw), 2)
    if paired_ca_net and isinstance(runtime_total_5bps, (int, float)):
        delta_5bps = round(sum(paired_ca_net) - float(runtime_total_5bps), 2)

    status = "RUNNING_PNL_COMPLETE"
    status_reason = None
    if open_n > 0:
        status = "PENDING"
        status_reason = "open_shadow_remaining"
    elif closed and (gross is None or net5 is None):
        status = "PARTIAL_PIPELINE"
        status_reason = "fixed_30m_incomplete"
    elif closed and not rt_raw:
        status = "PARTIAL_PIPELINE"
        status_reason = "runtime_compatible_missing"
    elif closed and join_failed and join_success:
        status = "PARTIAL_PIPELINE"
        status_reason = "partial_runtime_join"
    elif closed and join_success == len(closed):
        status = "CLOSED_READY"

    return {
        "shadow_name": SHADOW_NAME,
        "enabled": True,
        "blocks_real_entry": False,
        "observe_only": True,
        "np_in_decision": False,
        "score_formula": "z(pbv2)+0.35*winner_enrichment-0.45*z(stop_risk)",
        "selection_cycles": state.selection_cycles,
        "shadow_eligible": state.shadow_eligible,
        "stop_risk_reject": state.stop_risk_reject,
        "same_snapshot_nofill": state.same_snapshot_nofill,
        "later_fill": state.later_fill,
        "never_filled": state.never_filled,
        "shadow_entries": state.shadow_entries,
        "virtual_entry_count": state.shadow_entries,
        "real_block_count": 0,
        "evaluable_count": len(closed),
        "official_entry_match": state.official_match,
        "official_entry_mismatch": state.official_mismatch,
        "candidates": len(state.events),
        "eligible": state.shadow_eligible,
        "no_fill": state.same_snapshot_nofill,
        # four separate metrics (do not mix)
        "fixed_30m_raw": gross,
        "fixed_30m_5bps_roundtrip": net5,
        "runtime_compatible_raw": runtime_total_raw,
        "runtime_compatible_5bps_roundtrip": runtime_total_5bps,
        # Task4 aliases
        "runtime_total_raw": runtime_total_raw,
        "runtime_total_5bps": runtime_total_5bps,
        # Paired totals (join-success only) for delta / PF差; full-set remains in pnl_after_5bps_30m.
        "cost_aware_total_raw": round(sum(paired_ca_raw), 2) if paired_ca_raw else gross,
        "cost_aware_total_5bps": round(sum(paired_ca_net), 2) if paired_ca_net else net5,
        "cost_aware_pf_5bps_paired": paired_ca_pf,
        "delta_total_raw": delta_raw,
        "delta_total_5bps": delta_5bps,
        # legacy aliases
        "gross_pnl_30m": gross,
        "pnl_after_5bps_30m": net5,
        "runtime_compatible_pnl": runtime_total_raw,
        "shadow_pf_5bps_30m": pf,
        "fixed_30m_pf_5bps": pf,
        "runtime_compatible_pf_5bps": rt_pf,
        "runtime_pf_5bps": rt_pf,
        "cost_aware_pf_5bps": paired_ca_pf if paired_ca_pf is not None else pf,
        "pf_delta_5bps": pf_delta,
        "fixed_30m_wins": w30,
        "fixed_30m_losses": l30,
        "fixed_30m_flats": f30,
        "runtime_compatible_wins": wrt,
        "runtime_compatible_losses": lrt,
        "runtime_compatible_flats": frt,
        "n_closed": len(closed),
        "n_open": open_n,
        "join_success_count": join_success,
        "join_failed_count": join_failed,
        "pending_count": open_n,
        "delta_eligible_count": join_success,
        "join_failure_reasons": reason_counts,
        "recovery_finalize_count": freeze_n,
        "session_force_close_finalize_count": session_n,
        "status": status,
        "status_reason": status_reason,
        "stop_rate": None,
        "no_progress_rate": None,
        "closed_trades": closed,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
    }


def format_shadow_summary_lines(summary_or_state: Any) -> list[str]:
    if isinstance(summary_or_state, CostAwareShadowState):
        s = summarize_state(summary_or_state)
    elif isinstance(summary_or_state, Mapping):
        s = dict(summary_or_state)
    else:
        return []
    if not s:
        return []
    return [
        f"[SHADOW {SHADOW_NAME}] cycles={s.get('selection_cycles')} eligible={s.get('shadow_eligible')} "
        f"STOP_rej={s.get('stop_risk_reject')} nofill={s.get('same_snapshot_nofill')} "
        f"later={s.get('later_fill')} never={s.get('never_filled')} entries={s.get('shadow_entries')} "
        f"official_match={s.get('official_entry_match')}/{s.get('official_entry_mismatch')} "
        f"pnl30m={s.get('gross_pnl_30m')} pnl5bps={s.get('pnl_after_5bps_30m')} "
        f"rt_pnl={s.get('runtime_compatible_pnl')} PF={s.get('shadow_pf_5bps_30m')} "
        f"(observe-only; NP not in score)",
    ]


# ---------------------------------------------------------------------------
# Pure no-fill unit helpers (also used by tests)
# ---------------------------------------------------------------------------


def simulate_nofill_decision(
    ranked: Sequence[Mapping[str, Any]],
    *,
    free_slots: int,
    stop_z_thr: float = STOP_Z_REJECT,
) -> dict[str, Any]:
    """
    ranked items need: symbol, z_stop, integrated_score (desc).
    no-fill: reject consumes slot opportunity; do not take next same-snapshot.
    """
    accepted = []
    rejected = []
    rank_used = 0
    for rank_i, row in enumerate(ranked, start=1):
        if rank_used >= free_slots:
            break
        rank_used += 1
        if _f(row.get("z_stop")) >= stop_z_thr:
            rejected.append(str(row.get("symbol")))
            continue
        accepted.append(str(row.get("symbol")))
        if len(accepted) >= free_slots:
            break
    return {
        "accepted": accepted,
        "rejected": rejected,
        "unfilled_slots": free_slots - len(accepted),
    }
