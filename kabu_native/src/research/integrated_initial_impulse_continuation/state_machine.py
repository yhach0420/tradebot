"""IIC integrated state machine — S0→ENTRY→post states. No FCR/PBv2 reuse."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from research.integrated_initial_impulse_continuation.constants import (
    BREAK_HOLD_EVENTS,
    COST_BPS,
    DIAG_EXIT_SEC,
    EXHAUST_MFE_MIN,
    EXHAUST_STALL_SEC,
    FLOW_BUY_RATIO_MIN,
    FLOW_VOL_MULT,
    GIVEBACK_FRAC,
    GIVEBACK_MFE_MIN,
    HARD_SPREAD_BPS,
    HORIZON_SEC,
    NO_FOLLOW_MFE_MAX,
    NO_FOLLOW_SEC,
    QUIET_LOOKBACK_SEC,
    QUIET_RANGE_BPS_MAX,
    QUIET_RET_ABS_MAX,
)
from research.integrated_initial_impulse_continuation.loader import Tick, exec_entry_ok, first_valid_ask
from research.integrated_initial_impulse_continuation.observations import quiet_base_metrics, window_flow


@dataclass
class Episode:
    episode_id: str
    day: str
    symbol: str
    stream_key: str
    states: list[str] = field(default_factory=list)
    t_first: dict[str, Optional[datetime]] = field(default_factory=dict)
    base_low: Optional[float] = None
    base_high: Optional[float] = None
    break_level: Optional[float] = None
    flow_ref: Optional[float] = None
    vol_ref: Optional[float] = None
    spread_ref: Optional[float] = None
    bid_ref: Optional[float] = None
    ask_ref: Optional[float] = None
    entry_idx: Optional[int] = None
    entry_time: Optional[datetime] = None
    entry_ask: Optional[float] = None
    initial_stop_level: Optional[float] = None
    expected_first_target: Optional[float] = None
    idx_break_failure: Optional[int] = None
    idx_no_follow: Optional[int] = None
    idx_exhaust: Optional[int] = None
    idx_giveback: Optional[int] = None
    idx_hard: Optional[int] = None
    idx_diag: Optional[int] = None
    idx_horizon: Optional[int] = None
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    saw_s4: bool = False
    saw_s5: bool = False
    status: str = "OPEN"
    fail_reason: str = ""


def _mark(ep: Episode, state: str, ts: datetime) -> None:
    if state not in ep.states:
        ep.states.append(state)
    if ep.t_first.get(state) is None:
        ep.t_first[state] = ts


def _gap(a: Tick, b: Tick) -> bool:
    return (b.ts - a.ts).total_seconds() > 90


def _run_post_entry(ep: Episode, ticks: Sequence[Tick], start_i: int) -> None:
    assert ep.entry_idx is not None and ep.entry_ask is not None
    t0 = ticks[ep.entry_idx].ts
    entry_ask = ep.entry_ask
    break_level = ep.break_level
    post_high = entry_ask
    last_hh = t0
    mfe = mae = 0.0
    cost_pct = COST_BPS / 100.0  # bps→% for mfe compare: 5bps = 0.05%

    for k in range(ep.entry_idx + 1, len(ticks)):
        tk = ticks[k]
        if _gap(ticks[k - 1], tk) or tk.session != ticks[start_i].session:
            if ep.idx_hard is None:
                ep.idx_hard = k
                _mark(ep, "HARD_EXIT", tk.ts)
            break
        dt = (tk.ts - t0).total_seconds()
        b = tk.board.canonical_best_bid
        p = tk.px
        if b is None or b <= 0:
            continue
        ret = (float(b) - entry_ask) / entry_ask * 100.0
        mfe = max(mfe, ret)
        mae = min(mae, ret)
        if p is not None and p > post_high:
            post_high = p
            last_hh = tk.ts
        sp = tk.board.canonical_spread_bps
        fl10 = window_flow(ticks, k, 10)
        fl30 = window_flow(ticks, k, 30)

        if sp is not None and sp >= HARD_SPREAD_BPS and ep.idx_hard is None:
            ep.idx_hard = k
            _mark(ep, "HARD_EXIT", tk.ts)
        if ep.initial_stop_level and p is not None and p < ep.initial_stop_level and ep.idx_hard is None:
            ep.idx_hard = k
            _mark(ep, "HARD_EXIT", tk.ts)

        if not ep.saw_s4 and mfe >= cost_pct and p is not None and break_level and p >= break_level:
            if fl10["buy_ratio"] >= 0.52:
                ep.saw_s4 = True
                _mark(ep, "S4_IMPULSE_ADVANCE", tk.ts)

        if ep.saw_s4 and mfe >= 0.20 and fl10["buy_ratio"] >= 0.55:
            if break_level is None or (p is not None and p >= break_level):
                if not ep.saw_s5:
                    ep.saw_s5 = True
                    _mark(ep, "S5_HEALTHY_CONTINUATION", tk.ts)

        if break_level is not None and b < break_level * 0.999:
            buy_gone = fl10["buy_ratio"] < 0.48 or fl10["buy_v"] <= fl30["buy_v"] * 0.45 + 1e-9
            if buy_gone and ep.idx_break_failure is None:
                below = sum(
                    1 for m in range(k, min(len(ticks), k + 3))
                    if ticks[m].board.canonical_best_bid is not None
                    and ticks[m].board.canonical_best_bid < break_level * 0.999
                )
                if below >= 2:
                    ep.idx_break_failure = k
                    _mark(ep, "S6_BREAK_FAILURE", tk.ts)

        if not ep.saw_s4 and dt >= NO_FOLLOW_SEC and mfe < NO_FOLLOW_MFE_MAX:
            stagnant = fl10["freq"] <= (fl30["freq"] or 1) * 0.7 and fl10["buy_ratio"] < 0.55
            if stagnant and ep.idx_no_follow is None:
                ep.idx_no_follow = k
                _mark(ep, "S7_NO_FOLLOW_THROUGH", tk.ts)

        if ep.saw_s4 and mfe >= EXHAUST_MFE_MIN:
            stall = (tk.ts - last_hh).total_seconds() >= EXHAUST_STALL_SEC
            signs = int(stall)
            if fl10["buy_ratio"] < 0.50:
                signs += 1
            if fl10["sell_v"] > fl30["sell_v"] * 0.55 + 1e-9:
                signs += 1
            if fl10["freq"] < (fl30["freq"] or 1) * 0.6:
                signs += 1
            if signs >= 2 and ep.idx_exhaust is None:
                ep.idx_exhaust = k
                _mark(ep, "S8_MOMENTUM_EXHAUSTION", tk.ts)

        if mfe >= GIVEBACK_MFE_MIN and 0 < ret < mfe * (1 - GIVEBACK_FRAC):
            stall = (tk.ts - last_hh).total_seconds() >= 15
            fade = fl10["buy_ratio"] < 0.52 or fl10["sell_v"] > fl10["buy_v"]
            if stall and fade and ep.idx_giveback is None:
                ep.idx_giveback = k
                _mark(ep, "S9_PROFIT_GIVEBACK", tk.ts)

        if dt >= DIAG_EXIT_SEC and ep.idx_diag is None:
            ep.idx_diag = k
        if dt >= HORIZON_SEC:
            ep.idx_horizon = k
            break

    ep.mfe_pct = mfe
    ep.mae_pct = mae
    if ep.idx_horizon is None:
        ep.idx_horizon = min(len(ticks) - 1, ep.entry_idx + 1)
    ep.status = "COMPLETE"


def build_episodes(stream_key: str, ticks: Sequence[Tick]) -> list[Episode]:
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    ep_n = 0
    i = 30
    while i < len(ticks) - 15:
        qb = quiet_base_metrics(ticks, i, QUIET_LOOKBACK_SEC)
        if not qb.get("ok"):
            i += 5
            continue
        if qb["range_bps"] > QUIET_RANGE_BPS_MAX or abs(qb["ret"]) > QUIET_RET_ABS_MAX:
            i += 4
            continue
        if qb["spread_bps"] is not None and qb["spread_bps"] > 50:
            i += 4
            continue

        ep_n += 1
        ep = Episode(
            episode_id=f"{day}|{symbol}|IIC|imp{ticks[i].event_seq}|{ticks[i].ts.isoformat()}",
            day=day, symbol=symbol, stream_key=stream_key,
            base_low=float(qb["base_low"]),
            base_high=float(qb["base_high"]),
            flow_ref=float(qb["flow"]["buy_ratio"]),
            vol_ref=max(float(qb["flow"]["vol"] or 1.0), 1.0),
            spread_ref=float(qb["spread_bps"]) if qb["spread_bps"] is not None else None,
            bid_ref=float(ticks[i].board.canonical_best_bid) if ticks[i].board.canonical_best_bid else None,
            ask_ref=float(ticks[i].board.canonical_best_ask) if ticks[i].board.canonical_best_ask else None,
        )
        _mark(ep, "S0_QUIET_BASE", ticks[i].ts)
        state = "S0_QUIET_BASE"
        base_high = ep.base_high
        vol_ref = ep.vol_ref or 1.0
        hold_count = 0
        t_start = ticks[i].ts
        j = i + 1
        entered = False

        while j < len(ticks) - 3 and not entered:
            t = ticks[j]
            if _gap(ticks[j - 1], t) or t.session != ticks[i].session:
                ep.status, ep.fail_reason = "INVALIDATED", "session_or_gap"
                break
            if (t.ts - t_start).total_seconds() > 300:
                ep.status, ep.fail_reason = "EXPIRED", "pre_entry"
                break
            px = t.px
            bid = t.board.canonical_best_bid
            ask = t.board.canonical_best_ask
            spread = t.board.canonical_spread_bps
            fl10 = window_flow(ticks, j, 10)
            fl45 = window_flow(ticks, j, 45)

            if state == "S0_QUIET_BASE":
                ign = (
                    fl10["buy_ratio"] >= FLOW_BUY_RATIO_MIN
                    and fl10["buy_v"] > 0
                    and fl10["vol"] >= vol_ref * FLOW_VOL_MULT
                    and fl10["freq"] >= max(2.0, fl45["freq"] * 0.35)
                    and (spread is None or ep.spread_ref is None or spread <= ep.spread_ref * 1.35 + 5)
                )
                bid_lift = ep.bid_ref is not None and bid is not None and bid > ep.bid_ref
                ask_absorb = (
                    t.prev_ask_qty is not None and t.board.canonical_ask_qty is not None
                    and t.board.canonical_ask_qty < t.prev_ask_qty and t.trade_side == "BUY"
                )
                if ign and (bid_lift or ask_absorb or fl10["buy_n"] >= 2):
                    state = "S1_FLOW_IGNITION"
                    _mark(ep, state, t.ts)

            elif state == "S1_FLOW_IGNITION":
                if fl10["buy_ratio"] < 0.50 and fl10["vol"] < vol_ref:
                    ep.status, ep.fail_reason = "INVALIDATED", "flow_fade"
                    break
                if px is not None and base_high is not None and px > base_high:
                    if bid is not None and bid >= base_high * 0.999 and (spread is None or spread < 55):
                        state = "S2_RANGE_BREAK"
                        _mark(ep, state, t.ts)
                        ep.break_level = float(base_high)
                        hold_count = 0

            elif state == "S2_RANGE_BREAK":
                lvl = ep.break_level
                if lvl is None or px is None:
                    j += 1
                    continue
                if px < lvl:
                    ep.status, ep.fail_reason = "INVALIDATED", "break_reject"
                    break
                above = px >= lvl and (bid is None or bid >= lvl * 0.9985)
                if above and fl10["buy_ratio"] >= 0.52 and (spread is None or spread < 55):
                    hold_count += 1
                else:
                    hold_count = max(0, hold_count - 1)
                if hold_count >= BREAK_HOLD_EVENTS:
                    state = "S3_BREAK_HOLD"
                    _mark(ep, state, t.ts)
                    if exec_entry_ok(t) and ask:
                        eidx, eask = j, float(ask)
                    else:
                        fill = first_valid_ask(list(ticks), j)
                        if fill is None:
                            ep.status, ep.fail_reason = "INVALIDATED", "no_ask"
                            break
                        eidx, eask = fill
                    ep.entry_idx = eidx
                    ep.entry_time = ticks[eidx].ts
                    ep.entry_ask = eask
                    ep.initial_stop_level = float(ep.base_low) if ep.base_low else eask * 0.997
                    ep.expected_first_target = eask * (1 + COST_BPS / 10000.0)
                    _mark(ep, "ENTRY", ticks[eidx].ts)
                    entered = True
                    _run_post_entry(ep, ticks, i)
                    break
            j += 1

        if ep.status == "OPEN" and not entered:
            ep.status = ep.status if ep.fail_reason else "EXPIRED"
            ep.fail_reason = ep.fail_reason or "no_entry"

        out.append(ep)
        if entered and ep.entry_idx is not None:
            i = ep.entry_idx + 8
        else:
            i = max(i + 10, j + 1)
    return out
