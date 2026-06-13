"""
Phase337: Independent EXIT candidate shadow evaluation (research only).

Each candidate gets its own virtual EXIT (one per position). Actual Phase332 exit unchanged.
Uses PUSH BidQty/AskQty only — no REST /board. No tick CSV; minimal in-memory state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from research.research_exit_criteria import _as_float
from small_paper.realtime_board_exit_shadow import (
    IMB_COLLAPSE_DELTA,
    IMB_DETERIORATION_DELTA,
    IMB_MAINTAIN_EPSILON,
    SHADOW_LOSS_ACCELERATION_PNL_PCT,
    SHADOW_PROFIT_PROTECT_DELTA,
    calc_bid_ask_imbalance,
    make_position_id,
)
from storage.intraday_recorder import parse_kabu_time
from universe.filters import calc_spread_bps

EXIT_CANDIDATE_IDS: tuple[str, ...] = (
    "loss_acceleration_exit",
    "profit_protect_exit",
    "board_collapse_profit_exit",
    "high_update_failure_exit",
    "vwap_assisted_loss_exit",
)

PHASE338_CANDIDATE_IDS: tuple[str, ...] = (
    "vwap_assisted_loss_exit",
    "profit_protect_exit",
    "high_update_failure_exit",
)

EXTEND_CANDIDATE_ID = "strength_hold_extend"

ALL_CANDIDATE_IDS: tuple[str, ...] = EXIT_CANDIDATE_IDS + (EXTEND_CANDIDATE_ID,)

COLLAPSE_CONFIRM_TICKS = 2
HIGH_UPDATE_STALL_TICKS = 3
HIGH_UPDATE_MIN_MFE_PCT = 0.6
EXTEND_MIN_MFE_PCT = 1.0
PRICE_ACTION_DROP_PCT = 0.05


def _pnl_pct(entry_price: float, price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((price - entry_price) / entry_price * 100.0, 4)


def _tick_time(payload: Mapping[str, Any]) -> datetime:
    from zoneinfo import ZoneInfo

    jst = ZoneInfo("Asia/Tokyo")
    for key in ("CurrentPriceTime", "BidTime", "AskTime"):
        raw = payload.get(key)
        if raw:
            return parse_kabu_time(raw, fallback=datetime.now(jst))
    return datetime.now(jst)


def _imb_delta(entry_imb: Optional[float], current_imb: Optional[float]) -> Optional[float]:
    if entry_imb is None or current_imb is None:
        return None
    return round(float(current_imb) - float(entry_imb), 6)


def _imb_deteriorated(delta: Optional[float], *, large: bool = False) -> bool:
    if delta is None:
        return False
    thresh = -IMB_COLLAPSE_DELTA if large else -IMB_DETERIORATION_DELTA
    return float(delta) <= thresh


def _price_action_deteriorated(
    *,
    prev_price: Optional[float],
    current_price: float,
    prev_pnl_pct: Optional[float],
    current_pnl_pct: float,
) -> bool:
    if prev_price and prev_price > 0:
        move = (current_price - prev_price) / prev_price * 100.0
        if move <= -PRICE_ACTION_DROP_PCT:
            return True
    if prev_pnl_pct is not None and current_pnl_pct < prev_pnl_pct - 0.02:
        return True
    return False


def _below_vwap(current_price: float, payload: Mapping[str, Any]) -> bool:
    vwap = _as_float(payload.get("VWAP"))
    return vwap is not None and vwap > 0 and current_price < float(vwap)


def _spread_widened(entry_spread: Optional[float], spread: Optional[float]) -> bool:
    if entry_spread is None or spread is None:
        return False
    return float(spread) >= float(entry_spread) + 5.0


def _bid_qty_decreased(prev_bid_qty: Optional[float], bid_qty: Optional[float]) -> bool:
    if prev_bid_qty is None or bid_qty is None:
        return False
    return float(bid_qty) < float(prev_bid_qty) * 0.85


def evaluate_loss_acceleration_exit(ctx: Mapping[str, Any]) -> bool:
    return (
        float(ctx["current_pnl_pct"]) < 0.0
        and _imb_deteriorated(ctx.get("board_imbalance_delta"))
        and bool(ctx.get("price_action_deteriorated"))
        and float(ctx["current_pnl_pct"]) <= SHADOW_LOSS_ACCELERATION_PNL_PCT
    )


def evaluate_profit_protect_exit(ctx: Mapping[str, Any]) -> bool:
    return (
        float(ctx["mfe_pct"]) >= 0.6
        and float(ctx["current_pnl_pct"]) > 0.0
        and float(ctx.get("board_imbalance_delta") or 0.0) <= SHADOW_PROFIT_PROTECT_DELTA
        and bool(ctx.get("high_update_stalled_or_slow"))
    )


def evaluate_board_collapse_profit_exit(ctx: Mapping[str, Any]) -> bool:
    return (
        float(ctx["current_pnl_pct"]) > 0.0
        and _imb_deteriorated(ctx.get("board_imbalance_delta"), large=True)
        and bool(ctx.get("spread_or_bid_weakness"))
        and int(ctx.get("collapse_consecutive") or 0) >= COLLAPSE_CONFIRM_TICKS
    )


def evaluate_high_update_failure_exit(ctx: Mapping[str, Any]) -> bool:
    return (
        float(ctx["mfe_pct"]) >= HIGH_UPDATE_MIN_MFE_PCT
        and float(ctx["current_pnl_pct"]) > 0.0
        and int(ctx.get("ticks_since_high_update") or 0) >= HIGH_UPDATE_STALL_TICKS
        and _imb_deteriorated(ctx.get("board_imbalance_delta"))
    )


def evaluate_vwap_assisted_loss_exit(ctx: Mapping[str, Any]) -> bool:
    if not bool(ctx.get("vwap_available")):
        return False
    return (
        float(ctx["current_pnl_pct"]) < 0.0
        and _imb_deteriorated(ctx.get("board_imbalance_delta"))
        and bool(ctx.get("below_vwap"))
    )


def evaluate_strength_hold_extend(ctx: Mapping[str, Any]) -> bool:
    delta = ctx.get("board_imbalance_delta")
    if delta is None:
        return False
    return (
        float(ctx["mfe_pct"]) >= EXTEND_MIN_MFE_PCT
        and float(delta) >= 0.0
        and bool(ctx.get("board_maintained"))
        and bool(ctx.get("near_high"))
    )


CANDIDATE_EVALUATORS: dict[str, Any] = {
    "loss_acceleration_exit": evaluate_loss_acceleration_exit,
    "profit_protect_exit": evaluate_profit_protect_exit,
    "board_collapse_profit_exit": evaluate_board_collapse_profit_exit,
    "high_update_failure_exit": evaluate_high_update_failure_exit,
    "vwap_assisted_loss_exit": evaluate_vwap_assisted_loss_exit,
    EXTEND_CANDIDATE_ID: evaluate_strength_hold_extend,
}


@dataclass
class _CandidateExitState:
    triggered: bool = False
    exit_time: str = ""
    exit_ts: float = 0.0
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    pnl_yen_100: Optional[float] = None


@dataclass
class _TickRolling:
    prev_price: Optional[float] = None
    prev_pnl_pct: Optional[float] = None
    prev_bid_qty: Optional[float] = None
    session_high: float = 0.0
    ticks_since_high_update: int = 0
    collapse_consecutive: int = 0
    entry_spread: Optional[float] = None
    vwap_tick_count: int = 0
    vwap_missing_ticks: int = 0
    vwap_available: bool = False


@dataclass
class _PositionRecord:
    symbol: str
    position_id: str
    entry_time: str
    entry_price: float
    entry_bid_ask_imbalance: Optional[float] = None
    candidate_states: dict[str, _CandidateExitState] = field(default_factory=dict)
    extend_recorded: bool = False
    rolling: _TickRolling = field(default_factory=_TickRolling)
    actual_exit_reason: str = ""
    actual_exit_time: str = ""
    actual_exit_price: Optional[float] = None
    actual_pnl_pct: Optional[float] = None
    actual_pnl_yen_100: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.candidate_states:
            self.candidate_states = {cid: _CandidateExitState() for cid in EXIT_CANDIDATE_IDS}


@dataclass
class ExitCandidateShadowPack:
    """Memory-light multi-candidate shadow evaluator."""

    active_candidates: tuple[str, ...] = EXIT_CANDIDATE_IDS
    enable_extend: bool = True
    positions: dict[str, _PositionRecord] = field(default_factory=dict)
    trigger_counts: dict[str, int] = field(default_factory=dict)
    extend_candidate_count: int = 0
    vwap_eval_ticks: int = 0
    vwap_missing_ticks: int = 0

    def _candidate_ids(self) -> tuple[str, ...]:
        return tuple(cid for cid in self.active_candidates if cid in CANDIDATE_EVALUATORS)

    def _init_position_states(self, rec: _PositionRecord) -> None:
        rec.candidate_states = {cid: _CandidateExitState() for cid in self._candidate_ids()}

    def register_position(
        self,
        *,
        position_id: str,
        symbol: str,
        entry_time: datetime,
        entry_price: float,
        payload: Mapping[str, Any],
        entry_shadow: Mapping[str, Any],
    ) -> None:
        del entry_shadow
        entry_imb = calc_bid_ask_imbalance(payload)
        entry_imb_r = round(float(entry_imb), 6) if entry_imb is not None else None
        spread = calc_spread_bps(payload)
        rec = _PositionRecord(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time.isoformat(timespec="seconds"),
            entry_price=float(entry_price),
            entry_bid_ask_imbalance=entry_imb_r,
        )
        rec.rolling.entry_spread = round(float(spread), 4) if spread is not None else None
        rec.rolling.session_high = float(entry_price)
        self._init_position_states(rec)
        self.positions[position_id] = rec

    def record_holding_tick(
        self,
        *,
        symbol: str,
        position_id: str,
        entry_time: datetime,
        payload: Mapping[str, Any],
        current_price: float,
        entry_price: float,
        mfe_pct: float,
        entry_shadow: Mapping[str, Any],
    ) -> None:
        del symbol, entry_time, entry_shadow
        rec = self.positions.get(position_id)
        if rec is None:
            return

        roll = rec.rolling
        price = float(current_price)
        pnl = _pnl_pct(entry_price, price)
        current_imb = calc_bid_ask_imbalance(payload)
        current_imb_r = round(float(current_imb), 6) if current_imb is not None else None
        imb_delta = _imb_delta(rec.entry_bid_ask_imbalance, current_imb_r)
        spread = calc_spread_bps(payload)
        bid_qty = _as_float(payload.get("BidQty"))
        tick_dt = _tick_time(payload)

        if price > roll.session_high:
            roll.session_high = price
            roll.ticks_since_high_update = 0
        else:
            roll.ticks_since_high_update += 1

        collapse_signal = (
            float(pnl) > 0.0
            and _imb_deteriorated(imb_delta, large=True)
            and (
                _spread_widened(roll.entry_spread, spread)
                or _bid_qty_decreased(roll.prev_bid_qty, bid_qty)
            )
        )
        roll.collapse_consecutive = roll.collapse_consecutive + 1 if collapse_signal else 0

        vwap_ok = _as_float(payload.get("VWAP")) is not None
        roll.vwap_tick_count += 1
        if vwap_ok:
            roll.vwap_available = True
        else:
            roll.vwap_missing_ticks += 1

        eval_ctx: dict[str, Any] = {
            "current_pnl_pct": pnl,
            "mfe_pct": float(mfe_pct),
            "board_imbalance_delta": imb_delta,
            "price_action_deteriorated": _price_action_deteriorated(
                prev_price=roll.prev_price,
                current_price=price,
                prev_pnl_pct=roll.prev_pnl_pct,
                current_pnl_pct=pnl,
            ),
            "high_update_stalled_or_slow": roll.ticks_since_high_update >= 2,
            "spread_or_bid_weakness": _spread_widened(roll.entry_spread, spread)
            or _bid_qty_decreased(roll.prev_bid_qty, bid_qty),
            "collapse_consecutive": roll.collapse_consecutive,
            "ticks_since_high_update": roll.ticks_since_high_update,
            "below_vwap": _below_vwap(price, payload),
            "vwap_available": roll.vwap_available,
            "board_maintained": (
                rec.entry_bid_ask_imbalance is not None
                and current_imb_r is not None
                and float(current_imb_r) >= float(rec.entry_bid_ask_imbalance) - IMB_MAINTAIN_EPSILON
            ),
            "near_high": float(mfe_pct) > 0 and pnl >= float(mfe_pct) * 0.7,
        }

        from replay.pnl_yen import compute_pnl_yen_100

        for cid in self._candidate_ids():
            st = rec.candidate_states[cid]
            if st.triggered:
                continue
            if CANDIDATE_EVALUATORS[cid](eval_ctx):
                st.triggered = True
                st.exit_time = tick_dt.isoformat(timespec="seconds")
                st.exit_ts = tick_dt.timestamp()
                st.exit_price = round(price, 4)
                st.pnl_pct = pnl
                st.pnl_yen_100 = round(compute_pnl_yen_100(entry_price, price), 2)
                self.trigger_counts[cid] = self.trigger_counts.get(cid, 0) + 1

        if self.enable_extend and not rec.extend_recorded:
            if CANDIDATE_EVALUATORS[EXTEND_CANDIDATE_ID](eval_ctx):
                rec.extend_recorded = True
                self.extend_candidate_count += 1

        roll.prev_price = price
        roll.prev_pnl_pct = pnl
        roll.prev_bid_qty = bid_qty
        self.vwap_eval_ticks = roll.vwap_tick_count
        if roll.vwap_tick_count > 0:
            self.vwap_missing_ticks = max(self.vwap_missing_ticks, roll.vwap_missing_ticks)

    def finalize_position(
        self,
        *,
        position_id: str,
        actual_exit_reason: str,
        actual_exit_time: datetime,
        actual_exit_price: float,
        entry_price: float,
    ) -> None:
        from replay.pnl_yen import compute_pnl_yen_100

        rec = self.positions.get(position_id)
        if rec is None:
            return
        actual_pct = _pnl_pct(entry_price, actual_exit_price)
        yen = round(compute_pnl_yen_100(entry_price, actual_exit_price), 2)
        rec.actual_exit_reason = actual_exit_reason
        rec.actual_exit_time = actual_exit_time.isoformat(timespec="seconds")
        rec.actual_exit_price = round(float(actual_exit_price), 4)
        rec.actual_pnl_pct = actual_pct
        rec.actual_pnl_yen_100 = yen

    def export_trade_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for rec in self.positions.values():
            if not rec.actual_exit_reason:
                continue
            actual_yen = float(rec.actual_pnl_yen_100 or 0.0)
            for cid in self._candidate_ids():
                st = rec.candidate_states[cid]
                if st.triggered and st.pnl_yen_100 is not None:
                    shadow_yen = float(st.pnl_yen_100)
                    no_trigger = False
                else:
                    shadow_yen = actual_yen
                    no_trigger = True
                rows.append(
                    {
                        "symbol": rec.symbol,
                        "position_id": rec.position_id,
                        "entry_time": rec.entry_time,
                        "candidate_id": cid,
                        "shadow_exit_reason": cid if st.triggered else "",
                        "shadow_exit_time": st.exit_time,
                        "shadow_exit_price": st.exit_price,
                        "shadow_pnl_pct": st.pnl_pct if st.triggered else rec.actual_pnl_pct,
                        "shadow_pnl_yen_100": shadow_yen,
                        "actual_exit_reason": rec.actual_exit_reason,
                        "actual_exit_time": rec.actual_exit_time,
                        "actual_exit_price": rec.actual_exit_price,
                        "actual_pnl_pct": rec.actual_pnl_pct,
                        "actual_pnl_yen_100": actual_yen,
                        "candidate_vs_actual_delta_yen": round(shadow_yen - actual_yen, 2),
                        "no_candidate_trigger": no_trigger,
                        "strength_hold_extend": rec.extend_recorded,
                    }
                )
        return rows

    def extend_trade_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": rec.symbol,
                "position_id": rec.position_id,
                "entry_time": rec.entry_time,
                "extend_candidate": rec.extend_recorded,
                "actual_exit_reason": rec.actual_exit_reason,
                "actual_pnl_pct": rec.actual_pnl_pct,
                "actual_pnl_yen_100": rec.actual_pnl_yen_100,
            }
            for rec in self.positions.values()
            if rec.actual_exit_reason and rec.extend_recorded
        ]


def export_exit_candidate_trade_rows(pack: Optional[ExitCandidateShadowPack]) -> list[dict[str, Any]]:
    if pack is None:
        return []
    return pack.export_trade_rows()


__all__ = [
    "ALL_CANDIDATE_IDS",
    "EXIT_CANDIDATE_IDS",
    "PHASE338_CANDIDATE_IDS",
    "EXTEND_CANDIDATE_ID",
    "CANDIDATE_EVALUATORS",
    "ExitCandidateShadowPack",
    "export_exit_candidate_trade_rows",
    "make_position_id",
]
