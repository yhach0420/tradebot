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

JST = ZoneInfo("Asia/Tokyo")

OWNERSHIP = "RESEARCH"
SHADOW_NAME = "cost_aware_entry_shadow"
CAP = 5
HOLD_MINUTES = 30.0
# Cross-sectional stop reject (approx top ~5% of cycle stop_risk)
STOP_Z_REJECT = 1.65
COST_PCT_5BPS = 0.05  # roundtrip 5bps once per completed trade


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
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=JST)
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=JST)
    except Exception:
        return datetime.now(JST)


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


def _close_expired(state: CostAwareShadowState, *, now: datetime, trading_date: str) -> None:
    to_close = []
    for sym, pos in state.open_shadow.items():
        held = (now - pos.entry_time).total_seconds() / 60.0
        if held >= HOLD_MINUTES:
            to_close.append(sym)
    for sym in to_close:
        pos = state.open_shadow.pop(sym)
        # without live mark price, pnl left None until price tick update
        pos.fixed_30m_exit_time = pos.entry_time + timedelta(minutes=HOLD_MINUTES)
        pos.shadow_exit_policy = "fixed_30m"
        pos.shadow_exit_time = pos.fixed_30m_exit_time
        pos.closed = True
        row = _trade_row(pos)
        state.closed_trades.append(row)
        append_shadow_event(state, trading_date, {"event": "shadow_exit_fixed_30m", **row})


def mark_price_for_open(state: CostAwareShadowState, symbol: str, price: float, ts: Any = None) -> None:
    """Update mark for open shadow; finalize fixed_30m pnl when exit time reached."""
    pos = state.open_shadow.get(str(symbol))
    if pos is None or price <= 0:
        return
    now = _parse_ts(ts) or datetime.now(JST)
    # if past 30m, close with this price
    if (now - pos.entry_time).total_seconds() / 60.0 >= HOLD_MINUTES:
        pos.fixed_30m_exit_time = pos.entry_time + timedelta(minutes=HOLD_MINUTES)
        pos.fixed_30m_exit_price = float(price)
        if pos.entry_price > 0:
            pos.fixed_30m_pnl = (float(price) / pos.entry_price - 1.0) * 100.0
        pos.shadow_exit_policy = "fixed_30m"
        pos.shadow_exit_time = pos.fixed_30m_exit_time
        pos.shadow_exit_price = pos.fixed_30m_exit_price
        pos.shadow_exit_pnl = pos.fixed_30m_pnl
        pos.closed = True
        state.open_shadow.pop(str(symbol), None)
        state.closed_trades.append(_trade_row(pos))


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
    if target is None:
        for t in reversed(state.closed_trades):
            if t.get("symbol") == sym and t.get("official_runtime_exit_time") in (None, "N/A"):
                t["official_runtime_exit_time"] = str(exit_time)
                t["official_runtime_exit_price"] = exit_price
                t["official_runtime_exit_pnl"] = exit_pnl_pct
                return
        return
    target.official_runtime_exit_time = _parse_ts(exit_time)
    target.official_runtime_exit_price = float(exit_price) if exit_price else None
    if exit_pnl_pct is not None:
        target.official_runtime_exit_pnl = float(exit_pnl_pct)
    elif target.entry_price > 0 and exit_price:
        target.official_runtime_exit_pnl = (float(exit_price) / target.entry_price - 1.0) * 100.0


def _trade_row(pos: ShadowPosition) -> dict[str, Any]:
    return {
        "symbol": pos.symbol,
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


def summarize_state(state: CostAwareShadowState) -> dict[str, Any]:
    pnls = [t["fixed_30m_pnl"] for t in state.closed_trades if isinstance(t.get("fixed_30m_pnl"), (int, float))]
    gross = float(sum(pnls)) if pnls else 0.0
    net5 = float(sum(p - COST_PCT_5BPS for p in pnls)) if pnls else 0.0
    wins = sum(p for p in pnls if p - COST_PCT_5BPS > 0)
    losses = -sum(p for p in pnls if p - COST_PCT_5BPS < 0)
    pf = (wins / losses) if losses > 1e-12 else (999.0 if wins > 0 else None)
    rt_pnls = [
        t["official_runtime_exit_pnl"]
        for t in state.closed_trades
        if isinstance(t.get("official_runtime_exit_pnl"), (int, float))
    ]
    return {
        "shadow_name": SHADOW_NAME,
        "enabled": True,
        "blocks_real_entry": False,
        "np_in_decision": False,
        "score_formula": "z(pbv2)+0.35*winner_enrichment-0.45*z(stop_risk)",
        "selection_cycles": state.selection_cycles,
        "shadow_eligible": state.shadow_eligible,
        "stop_risk_reject": state.stop_risk_reject,
        "same_snapshot_nofill": state.same_snapshot_nofill,
        "later_fill": state.later_fill,
        "never_filled": state.never_filled,
        "shadow_entries": state.shadow_entries,
        "official_entry_match": state.official_match,
        "official_entry_mismatch": state.official_mismatch,
        "candidates": len(state.events),
        "eligible": state.shadow_eligible,
        "no_fill": state.same_snapshot_nofill,
        "gross_pnl_30m": gross,
        "pnl_after_5bps_30m": net5,
        "runtime_compatible_pnl": float(sum(rt_pnls)) if rt_pnls else None,
        "shadow_pf_5bps_30m": pf,
        "n_closed": len(state.closed_trades),
        "n_open": len(state.open_shadow),
        "stop_rate": None,  # filled when stop_proxy labels available
        "no_progress_rate": None,
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
