"""IOAR state machine — sell pressure → absorption → reverse → ENTRY → exits."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.integrated_order_flow_absorption_reversal.constants import (
    ABSORB_MIN_REPLENISH,
    ABSORB_MIN_SELL_QTY,
    ABSORB_SELL_IMPACT_DECAY,
    ACCEPT_HOLD_EVENTS,
    BALANCE_LOOKBACK_SEC,
    BUY_RATIO_MIN,
    COST_BPS,
    DIAG_EXIT_SEC,
    EXHAUST_MFE_MIN,
    EXHAUST_SELL_FREQ_RATIO,
    EXHAUST_STALL_SEC,
    GIVEBACK_FRAC,
    GIVEBACK_MFE_MIN,
    HARD_SPREAD_BPS,
    HORIZON_SEC,
    NO_DEMAND_MFE_MAX,
    NO_DEMAND_SEC,
    PRE_STAGE_MAX_SEC,
    SELL_MIN_N,
    SELL_MIN_V,
    SELL_PRESSURE_BUY_RATIO_MAX,
    ZONE_COOLDOWN_SEC,
)
from research.integrated_order_flow_absorption_reversal.loader import Tick, exec_entry_ok, first_valid_ask
from research.integrated_order_flow_absorption_reversal.observations import (
    balance_snapshot,
    detect_bid_replenish,
    window_flow,
)


@dataclass
class Episode:
    episode_id: str
    day: str
    symbol: str
    stream_key: str
    states: list[str] = field(default_factory=list)
    t_first: dict[str, Optional[datetime]] = field(default_factory=dict)
    # metrics bags
    pressure: dict[str, Any] = field(default_factory=dict)
    absorption: dict[str, Any] = field(default_factory=dict)
    exhaustion: dict[str, Any] = field(default_factory=dict)
    reversal: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)
    # levels
    absorption_price: Optional[float] = None
    absorption_zone_low: Optional[float] = None
    absorption_zone_high: Optional[float] = None
    pressure_low: Optional[float] = None
    # entry
    entry_idx: Optional[int] = None
    entry_time: Optional[datetime] = None
    entry_ask: Optional[float] = None
    entry_bid: Optional[float] = None
    initial_stop_level: Optional[float] = None
    # exit idxs
    idx_abs_fail: Optional[int] = None
    idx_no_demand: Optional[int] = None
    idx_demand_exh: Optional[int] = None
    idx_giveback: Optional[int] = None
    idx_hard: Optional[int] = None
    idx_diag: Optional[int] = None
    idx_horizon: Optional[int] = None
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    saw_s6: bool = False
    fail_stage: str = ""
    status: str = "OPEN"
    # feature row for distribution (filled at entry or fail)
    features: dict[str, Any] = field(default_factory=dict)


def _mark(ep: Episode, state: str, ts: datetime) -> None:
    if state not in ep.states:
        ep.states.append(state)
    if ep.t_first.get(state) is None:
        ep.t_first[state] = ts


def _gap(a: Tick, b: Tick) -> bool:
    return (b.ts - a.ts).total_seconds() > 90


def _run_post(ep: Episode, ticks: Sequence[Tick], start_i: int) -> None:
    assert ep.entry_idx is not None and ep.entry_ask is not None
    t0 = ticks[ep.entry_idx].ts
    ask = ep.entry_ask
    zone_lo = ep.absorption_zone_low
    post_high = ask
    last_hh = t0
    mfe = mae = 0.0
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
        ret = (float(b) - ask) / ask * 100.0
        mfe = max(mfe, ret)
        mae = min(mae, ret)
        if p is not None and p > post_high:
            post_high = p
            last_hh = tk.ts
        fl10 = window_flow(ticks, k, 10)
        fl30 = window_flow(ticks, k, 30)
        sp = tk.board.canonical_spread_bps

        if sp is not None and sp >= HARD_SPREAD_BPS and ep.idx_hard is None:
            ep.idx_hard = k
            _mark(ep, "HARD_EXIT", tk.ts)

        # S6 demand continuation
        if (
            fl10["buy_ratio"] >= 0.55 and mfe >= COST_BPS / 100.0
            and (zone_lo is None or b >= zone_lo)
            and fl10["buy_n"] >= 1
        ):
            if not ep.saw_s6:
                ep.saw_s6 = True
                _mark(ep, "S6_DEMAND_CONTINUATION", tk.ts)

        # S7 absorption failure
        if zone_lo is not None and b < zone_lo:
            sell_up = fl10["sell_ratio"] >= 0.55 or fl10["sell_v"] > fl30["sell_v"] * 0.6
            repl = detect_bid_replenish(tk) == "none"
            if (sell_up or repl) and ep.idx_abs_fail is None:
                below = sum(
                    1 for m in range(k, min(len(ticks), k + 3))
                    if ticks[m].board.canonical_best_bid is not None
                    and ticks[m].board.canonical_best_bid < zone_lo
                )
                if below >= 2:
                    ep.idx_abs_fail = k
                    _mark(ep, "S7_ABSORPTION_FAILURE", tk.ts)

        # S8 no demand follow through
        if not ep.saw_s6 and dt >= NO_DEMAND_SEC and mfe < NO_DEMAND_MFE_MAX:
            weak = fl10["buy_ratio"] < 0.52 and fl10["freq"] <= (fl30["freq"] or 1) * 0.7
            near = zone_lo is not None and b <= (ep.absorption_price or zone_lo) * 1.0015
            if weak and near and ep.idx_no_demand is None:
                ep.idx_no_demand = k
                _mark(ep, "S8_NO_DEMAND_FOLLOW_THROUGH", tk.ts)

        # S9 demand exhaustion
        if ep.saw_s6 and mfe >= EXHAUST_MFE_MIN:
            stall = (tk.ts - last_hh).total_seconds() >= EXHAUST_STALL_SEC
            signs = int(stall)
            if fl10["buy_ratio"] < 0.50:
                signs += 1
            if fl10["sell_v"] > fl30["sell_v"] * 0.55:
                signs += 1
            if fl10["freq"] < (fl30["freq"] or 1) * 0.55:
                signs += 1
            if signs >= 2 and ep.idx_demand_exh is None:
                ep.idx_demand_exh = k
                _mark(ep, "S9_DEMAND_EXHAUSTION", tk.ts)

        # S10 profit giveback
        if mfe >= GIVEBACK_MFE_MIN and 0 < ret < mfe * (1 - GIVEBACK_FRAC):
            stall = (tk.ts - last_hh).total_seconds() >= 12
            fade = fl10["buy_ratio"] < 0.52 or fl10["sell_v"] > fl10["buy_v"]
            if stall and fade and ep.idx_giveback is None:
                ep.idx_giveback = k
                _mark(ep, "S10_PROFIT_GIVEBACK", tk.ts)

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
    i = 25
    last_zone_end: Optional[datetime] = None
    last_zone_lo: Optional[float] = None

    while i < len(ticks) - 20:
        bal = balance_snapshot(ticks, i, BALANCE_LOOKBACK_SEC)
        if not bal.get("ok"):
            i += 5
            continue

        # cooldown same zone
        if last_zone_end is not None and last_zone_lo is not None:
            if (ticks[i].ts - last_zone_end).total_seconds() < ZONE_COOLDOWN_SEC:
                if ticks[i].px and abs(ticks[i].px - last_zone_lo) / last_zone_lo < 0.003:
                    i += 8
                    continue

        ep = Episode(
            episode_id=f"{day}|{symbol}|IOAR|sp{ticks[i].event_seq}",
            day=day, symbol=symbol, stream_key=stream_key,
        )
        _mark(ep, "S0_MARKET_BALANCE", ticks[i].ts)
        state = "S0_MARKET_BALANCE"
        t0 = ticks[i].ts
        sell_peak_freq = 0.0
        sell_peak_qty = 0.0
        sell_peak_ratio = 0.0
        impact_start = None
        impact_end = None
        absorbed_sell = 0.0
        replenish_n = 0
        last_replenish_seq = -1
        pressure_low = ticks[i].px
        absorb_px = None
        hold_n = 0
        j = i + 1
        entered = False

        while j < len(ticks) - 3 and not entered:
            t = ticks[j]
            if _gap(ticks[j - 1], t) or t.session != ticks[i].session:
                ep.status, ep.fail_stage = "INVALIDATED", state
                break
            age = (t.ts - t0).total_seconds()
            if age > PRE_STAGE_MAX_SEC and ep.entry_idx is None:
                ep.status, ep.fail_stage = "EXPIRED", state
                break

            fl10 = window_flow(ticks, j, 10)
            fl30 = window_flow(ticks, j, 30)
            px = t.px
            bid = t.board.canonical_best_bid
            ask = t.board.canonical_best_ask
            spread = t.board.canonical_spread_bps

            if state == "S0_MARKET_BALANCE":
                sell_ok = (
                    fl10["sell_n"] >= SELL_MIN_N
                    and fl10["sell_v"] >= SELL_MIN_V
                    and fl10["buy_ratio"] <= SELL_PRESSURE_BUY_RATIO_MAX
                    and fl10["sell_ratio"] >= 0.55
                )
                down_ok = fl10["down_ticks"] >= 1 or (
                    bid is not None and bal["bid"] is not None and bid < bal["bid"]
                )
                if sell_ok and down_ok:
                    state = "S1_SELL_PRESSURE"
                    _mark(ep, state, t.ts)
                    sell_peak_freq = fl10["freq"]
                    sell_peak_qty = fl10["sell_v"]
                    sell_peak_ratio = fl10["sell_ratio"]
                    impact_start = fl10["down_tick_per_sell_qty"]
                    pressure_low = px or pressure_low
                    ep.pressure = {
                        "sell_trade_count": fl10["sell_n"], "sell_trade_qty": fl10["sell_v"],
                        "sell_trade_ratio": fl10["sell_ratio"], "sell_frequency": fl10["freq"],
                        "down_ticks": fl10["down_ticks"],
                        "sell_qty_per_down_tick": fl10["sell_qty_per_down_tick"],
                        "down_tick_per_sell_qty": fl10["down_tick_per_sell_qty"],
                    }
                    ep.episode_id = f"{day}|{symbol}|IOAR|sp{t.event_seq}"

            elif state == "S1_SELL_PRESSURE":
                if fl10["sell_v"] > sell_peak_qty:
                    sell_peak_qty = fl10["sell_v"]
                if fl10["freq"] > sell_peak_freq:
                    sell_peak_freq = fl10["freq"]
                if fl10["sell_ratio"] > sell_peak_ratio:
                    sell_peak_ratio = fl10["sell_ratio"]
                if px is not None and (pressure_low is None or px < pressure_low):
                    pressure_low = px
                # absorption: sells continue but impact decays + bid holds/replenishes
                impact_now = fl10["down_tick_per_sell_qty"]
                if impact_start is None or impact_start <= 0:
                    impact_start = max(impact_now, 1e-9)
                impact_end = impact_now
                decay = (impact_end / impact_start) if impact_start > 0 else 1.0
                rtype = detect_bid_replenish(t)
                if rtype in ("same_price_qty_recover",) and t.event_seq != last_replenish_seq:
                    # require sell interaction for true absorb replenish
                    if t.trade_side == "SELL" or (t.prev_bid_qty is not None and t.board.canonical_bid_qty and t.board.canonical_bid_qty > t.prev_bid_qty):
                        replenish_n += 1
                        last_replenish_seq = t.event_seq
                absorbed_sell += t.volume_delta if (t.trade_side == "SELL" and t.volume_delta) else 0.0
                bid_hold = bid is not None and pressure_low is not None and bid >= pressure_low * 0.999
                if (
                    absorbed_sell >= ABSORB_MIN_SELL_QTY
                    and decay <= ABSORB_SELL_IMPACT_DECAY
                    and replenish_n >= ABSORB_MIN_REPLENISH
                    and bid_hold
                    and fl10["sell_v"] >= SELL_MIN_V * 0.5
                    and (spread is None or spread < 70)
                ):
                    state = "S2_ABSORPTION_ACTIVE"
                    _mark(ep, state, t.ts)
                    absorb_px = float(bid) if bid else float(px or 0)
                    ep.absorption_price = absorb_px
                    ep.absorption_zone_low = float(pressure_low) if pressure_low else absorb_px
                    ep.absorption_zone_high = absorb_px
                    ep.pressure_low = pressure_low
                    ep.absorption = {
                        "absorbed_sell_qty": absorbed_sell,
                        "bid_replenishment_count": replenish_n,
                        "sell_impact_start": impact_start,
                        "sell_impact_end": impact_end,
                        "sell_impact_decay": decay,
                        "sell_qty_per_down_tick": fl10["sell_qty_per_down_tick"],
                        "low_update_interval": fl10["down_ticks"],
                    }

            elif state == "S2_ABSORPTION_ACTIVE":
                if px is not None and pressure_low is not None and px < ep.absorption_zone_low:
                    # still can absorb deeper slightly
                    ep.absorption_zone_low = min(ep.absorption_zone_low, px)
                rtype = detect_bid_replenish(t)
                if rtype == "same_price_qty_recover" and t.event_seq != last_replenish_seq:
                    replenish_n += 1
                    last_replenish_seq = t.event_seq
                # exhaustion: sell flow decays vs peak
                if sell_peak_freq > 0 and fl10["freq"] <= sell_peak_freq * EXHAUST_SELL_FREQ_RATIO:
                    if fl10["sell_v"] <= sell_peak_qty * EXHAUST_SELL_FREQ_RATIO and fl10["sell_ratio"] < sell_peak_ratio:
                        if fl10["down_ticks"] <= 1:
                            state = "S3_SELL_EXHAUSTION"
                            _mark(ep, state, t.ts)
                            ep.exhaustion = {
                                "sell_freq_peak": sell_peak_freq, "sell_freq_after": fl10["freq"],
                                "sell_qty_peak": sell_peak_qty, "sell_qty_after": fl10["sell_v"],
                                "sell_ratio_peak": sell_peak_ratio, "sell_ratio_after": fl10["sell_ratio"],
                            }

            elif state == "S3_SELL_EXHAUSTION":
                buy_ok = fl10["buy_ratio"] >= BUY_RATIO_MIN and fl10["buy_v"] > 0 and fl10["buy_n"] >= 1
                step_up = detect_bid_replenish(t) == "bid_step_up" or (
                    ask is not None and ep.absorption_price and ask > ep.absorption_price
                )
                ask_hit = t.trade_side == "BUY"
                if buy_ok and ask_hit and step_up:
                    state = "S4_BUY_FLOW_REVERSAL"
                    _mark(ep, state, t.ts)
                    ep.reversal = {
                        "buy_trade_count": fl10["buy_n"], "buy_trade_qty": fl10["buy_v"],
                        "buy_trade_ratio": fl10["buy_ratio"], "buy_frequency": fl10["freq"],
                    }

            elif state == "S4_BUY_FLOW_REVERSAL":
                zone_lo = ep.absorption_zone_low
                if zone_lo is None:
                    ep.status, ep.fail_stage = "INVALIDATED", "no_zone"
                    break
                above = bid is not None and bid > zone_lo and (
                    ep.absorption_price is None or bid >= ep.absorption_price * 0.999
                )
                flow_ok = fl10["buy_ratio"] >= 0.55 and fl10["buy_n"] >= 1
                if above and flow_ok and (spread is None or spread < 60):
                    hold_n += 1
                else:
                    hold_n = max(0, hold_n - 1)
                if hold_n >= ACCEPT_HOLD_EVENTS:
                    # reject if already extended too far
                    if ep.absorption_price and ask and (ask - ep.absorption_price) / ep.absorption_price > 0.008:
                        ep.status, ep.fail_stage = "INVALIDATED", "entry_too_far"
                        break
                    state = "S5_ACCEPTANCE_CONFIRM"
                    _mark(ep, state, t.ts)
                    if exec_entry_ok(t) and ask:
                        eidx, eask = j, float(ask)
                    else:
                        fill = first_valid_ask(list(ticks), j)
                        if fill is None:
                            ep.status, ep.fail_stage = "INVALIDATED", "no_ask"
                            break
                        eidx, eask = fill
                    ep.entry_idx = eidx
                    ep.entry_time = ticks[eidx].ts
                    ep.entry_ask = eask
                    ep.entry_bid = float(ticks[eidx].board.canonical_best_bid or eask)
                    ep.initial_stop_level = float(ep.absorption_zone_low)
                    ep.acceptance = {
                        "hold_events": hold_n,
                        "distance_entry_from_absorption": (
                            (eask - ep.absorption_price) / ep.absorption_price
                            if ep.absorption_price else None
                        ),
                        "spread_at_entry": ticks[eidx].board.canonical_spread_bps,
                    }
                    _mark(ep, "ENTRY", ticks[eidx].ts)
                    ep.features = {
                        **ep.pressure, **ep.absorption, **ep.exhaustion, **ep.reversal,
                        **ep.acceptance,
                        "bid_replenishment_count": ep.absorption.get("bid_replenishment_count"),
                        "sell_impact_decay": ep.absorption.get("sell_impact_decay"),
                    }
                    entered = True
                    last_zone_end = ticks[eidx].ts
                    last_zone_lo = ep.absorption_zone_low
                    _run_post(ep, ticks, i)
                    break
            j += 1

        if not entered:
            if not ep.fail_stage:
                ep.fail_stage = state
            if ep.status == "OPEN":
                ep.status = "EXPIRED"
            # save partial features for distribution
            ep.features = {**ep.pressure, **ep.absorption, **ep.exhaustion, **ep.reversal}

        out.append(ep)
        if entered and ep.entry_idx is not None:
            i = ep.entry_idx + 10
        else:
            i = max(i + 12, j + 1)
    return out
