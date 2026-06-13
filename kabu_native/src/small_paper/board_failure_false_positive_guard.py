"""
Phase346: False-positive guards for board_failure_exit (mfe_lt_0p2_confirm5 base).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from small_paper.board_failure_exit_shadow import (
    BOARD_FAILURE_EXIT_ID,
    BOARD_FAILURE_IMB_DELTA,
    board_failure_arm_tick,
    board_failure_deterioration_tick,
    evaluate_board_failure_exit,
    mfe_bucket,
)
from small_paper.board_failure_exit_tuning import mfe_filter_allows
from small_paper.board_failure_forensic_pack import classify_forensic_trade
from small_paper.exit_candidate_shadow import (
    _imb_delta,
    _pnl_pct,
    _tick_time,
    calc_bid_ask_imbalance,
)
from small_paper.realtime_board_exit_shadow import make_position_id

ENTRY_COOLDOWN_OPTIONS_SEC: tuple[int, ...] = (30, 60, 90)
PHASE347_COOLDOWN_OPTIONS_SEC: tuple[int, ...] = (45, 60, 75, 90)
RECLAIM_TICK_OPTIONS: tuple[int, ...] = (3, 5, 10)

BASE_VARIANT_ID = "mfe_lt_0p2_confirm5"


@dataclass(frozen=True)
class BoardFailureGuardVariant:
    variant_id: str
    max_mfe_pct: float = 0.2
    confirm_ticks: int = 5
    entry_cooldown_sec: int = 0
    reclaim_ticks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "max_mfe_pct": self.max_mfe_pct,
            "confirm_ticks": self.confirm_ticks,
            "entry_cooldown_sec": self.entry_cooldown_sec,
            "reclaim_ticks": self.reclaim_ticks,
            "board_imbalance_delta_lte": BOARD_FAILURE_IMB_DELTA,
            "uses_vwap": False,
        }


def entry_cooldown_blocks(
    *,
    entry_time: datetime,
    tick_time: datetime,
    cooldown_sec: int,
) -> bool:
    if int(cooldown_sec) <= 0:
        return False
    elapsed = (tick_time - entry_time).total_seconds()
    return elapsed < float(cooldown_sec)


def price_reclaim_cancels(*, price: float, arm_low: float) -> bool:
    return float(price) > float(arm_low)


def reclaim_window_active(*, ticks_since_arm: int, reclaim_ticks: int) -> bool:
    return int(reclaim_ticks) > 0 and int(ticks_since_arm) <= int(reclaim_ticks)


def default_phase346_variants() -> tuple[BoardFailureGuardVariant, ...]:
    base = BoardFailureGuardVariant(variant_id=BASE_VARIANT_ID)
    out: list[BoardFailureGuardVariant] = [base]
    for sec in ENTRY_COOLDOWN_OPTIONS_SEC:
        out.append(
            BoardFailureGuardVariant(
                variant_id=f"{BASE_VARIANT_ID}_cd{sec}",
                entry_cooldown_sec=int(sec),
            )
        )
    for ticks in RECLAIM_TICK_OPTIONS:
        out.append(
            BoardFailureGuardVariant(
                variant_id=f"{BASE_VARIANT_ID}_rc{ticks}",
                reclaim_ticks=int(ticks),
            )
        )
    out.append(
        BoardFailureGuardVariant(
            variant_id=f"{BASE_VARIANT_ID}_cd60_rc5",
            entry_cooldown_sec=60,
            reclaim_ticks=5,
        )
    )
    return tuple(out)


def default_phase347_variants() -> tuple[BoardFailureGuardVariant, ...]:
    return tuple(
        BoardFailureGuardVariant(
            variant_id=f"{BASE_VARIANT_ID}_cd{sec}",
            entry_cooldown_sec=int(sec),
        )
        for sec in PHASE347_COOLDOWN_OPTIONS_SEC
    )


@dataclass
class _VariantExitState:
    triggered: bool = False
    exit_time: str = ""
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    pnl_yen_100: Optional[float] = None
    peak_mfe_pct: float = 0.0
    armed: bool = False
    confirm_streak: int = 0
    arm_low: Optional[float] = None
    ticks_since_arm: int = 0
    post_shadow_max_up_pct: float = 0.0
    post_shadow_max_down_pct: float = 0.0


@dataclass
class _GuardPosition:
    symbol: str
    position_id: str
    entry_time: str
    entry_dt: datetime
    entry_price: float
    entry_bid_ask_imbalance: Optional[float] = None
    session_low: float = 0.0
    peak_mfe_pct: float = 0.0
    variant_states: dict[str, _VariantExitState] = field(default_factory=dict)
    actual_exit_reason: str = ""
    actual_exit_time: str = ""
    actual_exit_price: Optional[float] = None
    actual_pnl_pct: Optional[float] = None
    actual_pnl_yen_100: Optional[float] = None


@dataclass
class BoardFailureGuardTuningPack:
    """Multi-variant board failure shadow with false-positive guards."""

    variants: tuple[BoardFailureGuardVariant, ...] = field(
        default_factory=default_phase346_variants
    )
    positions: dict[str, _GuardPosition] = field(default_factory=dict)
    trigger_counts: dict[str, int] = field(default_factory=dict)
    board_tick_count: int = 0

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
        ep = float(entry_price)
        self.positions[position_id] = _GuardPosition(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time.isoformat(timespec="seconds"),
            entry_dt=entry_time,
            entry_price=ep,
            entry_bid_ask_imbalance=entry_imb_r,
            session_low=ep,
            variant_states={v.variant_id: _VariantExitState() for v in self.variants},
        )

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

        self.board_tick_count += 1
        price = float(current_price)
        pnl = _pnl_pct(entry_price, price)
        rec.peak_mfe_pct = max(rec.peak_mfe_pct, float(mfe_pct))

        current_imb = calc_bid_ask_imbalance(payload)
        current_imb_r = round(float(current_imb), 6) if current_imb is not None else None
        imb_delta = _imb_delta(rec.entry_bid_ask_imbalance, current_imb_r)

        low_updated = False
        if price < rec.session_low:
            rec.session_low = price
            low_updated = True

        deteriorating = board_failure_deterioration_tick(
            current_pnl_pct=pnl,
            board_imbalance_delta=imb_delta,
        )

        from replay.pnl_yen import compute_pnl_yen_100

        tick_dt = _tick_time(payload)

        for variant in self.variants:
            vid = variant.variant_id
            st = rec.variant_states[vid]
            if st.triggered:
                if st.exit_price is not None and entry_price > 0:
                    ref = float(st.exit_price)
                    move_pct = (price - ref) / float(entry_price) * 100.0
                    st.post_shadow_max_up_pct = max(st.post_shadow_max_up_pct, move_pct)
                    st.post_shadow_max_down_pct = min(st.post_shadow_max_down_pct, move_pct)
                continue

            if not mfe_filter_allows(
                peak_mfe_pct=rec.peak_mfe_pct,
                max_mfe_pct=variant.max_mfe_pct,
            ):
                st.armed = False
                st.confirm_streak = 0
                st.arm_low = None
                st.ticks_since_arm = 0
                continue

            if entry_cooldown_blocks(
                entry_time=rec.entry_dt,
                tick_time=tick_dt,
                cooldown_sec=variant.entry_cooldown_sec,
            ):
                st.armed = False
                st.confirm_streak = 0
                st.arm_low = None
                st.ticks_since_arm = 0
                continue

            if not st.armed:
                if board_failure_arm_tick(
                    current_pnl_pct=pnl,
                    board_imbalance_delta=imb_delta,
                    low_updated_this_tick=low_updated,
                ):
                    st.armed = True
                    st.confirm_streak = 1
                    st.arm_low = rec.session_low
                    st.ticks_since_arm = 1
            else:
                if (
                    variant.reclaim_ticks > 0
                    and st.arm_low is not None
                    and reclaim_window_active(
                        ticks_since_arm=st.ticks_since_arm,
                        reclaim_ticks=variant.reclaim_ticks,
                    )
                    and price_reclaim_cancels(price=price, arm_low=st.arm_low)
                ):
                    st.armed = False
                    st.confirm_streak = 0
                    st.arm_low = None
                    st.ticks_since_arm = 0
                elif deteriorating:
                    st.confirm_streak += 1
                    st.ticks_since_arm += 1
                else:
                    st.armed = False
                    st.confirm_streak = 0
                    st.arm_low = None
                    st.ticks_since_arm = 0

            if evaluate_board_failure_exit(
                armed=st.armed,
                confirm_streak=st.confirm_streak,
                confirm_ticks=variant.confirm_ticks,
            ) and mfe_filter_allows(
                peak_mfe_pct=rec.peak_mfe_pct,
                max_mfe_pct=variant.max_mfe_pct,
            ):
                st.triggered = True
                st.exit_time = tick_dt.isoformat(timespec="seconds")
                st.exit_price = round(price, 4)
                st.pnl_pct = pnl
                st.pnl_yen_100 = round(compute_pnl_yen_100(entry_price, price), 2)
                st.peak_mfe_pct = rec.peak_mfe_pct
                self.trigger_counts[vid] = self.trigger_counts.get(vid, 0) + 1

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
        rec.actual_exit_reason = actual_exit_reason
        rec.actual_exit_time = actual_exit_time.isoformat(timespec="seconds")
        rec.actual_exit_price = round(float(actual_exit_price), 4)
        rec.actual_pnl_pct = _pnl_pct(entry_price, actual_exit_price)
        rec.actual_pnl_yen_100 = round(compute_pnl_yen_100(entry_price, actual_exit_price), 2)

    def export_trade_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for rec in self.positions.values():
            if not rec.actual_exit_reason:
                continue
            actual_yen = float(rec.actual_pnl_yen_100 or 0.0)
            for variant in self.variants:
                vid = variant.variant_id
                st = rec.variant_states[vid]
                peak_mfe = round(
                    st.peak_mfe_pct if st.triggered else rec.peak_mfe_pct,
                    4,
                )
                if st.triggered and st.pnl_yen_100 is not None:
                    shadow_yen = float(st.pnl_yen_100)
                    no_trigger = False
                else:
                    shadow_yen = actual_yen
                    no_trigger = True
                delta_yen = round(shadow_yen - actual_yen, 2)
                forensic_class = classify_forensic_trade(
                    shadow_triggered=st.triggered,
                    pnl_difference_yen=delta_yen,
                    actual_pnl_yen=actual_yen,
                    actual_exit_reason=rec.actual_exit_reason,
                    post_shadow_max_up_pct=st.post_shadow_max_up_pct,
                    post_shadow_max_down_pct=st.post_shadow_max_down_pct,
                )
                rows.append(
                    {
                        "symbol": rec.symbol,
                        "position_id": rec.position_id,
                        "entry_time": rec.entry_time,
                        "candidate_id": BOARD_FAILURE_EXIT_ID,
                        "variant_id": vid,
                        "max_mfe_pct": variant.max_mfe_pct,
                        "confirm_ticks": variant.confirm_ticks,
                        "entry_cooldown_sec": variant.entry_cooldown_sec,
                        "reclaim_ticks": variant.reclaim_ticks,
                        "shadow_exit_reason": BOARD_FAILURE_EXIT_ID if st.triggered else "",
                        "shadow_exit_time": st.exit_time,
                        "shadow_exit_price": st.exit_price,
                        "shadow_pnl_pct": st.pnl_pct if st.triggered else rec.actual_pnl_pct,
                        "shadow_pnl_yen_100": shadow_yen,
                        "actual_exit_reason": rec.actual_exit_reason,
                        "actual_exit_time": rec.actual_exit_time,
                        "actual_exit_price": rec.actual_exit_price,
                        "actual_pnl_pct": rec.actual_pnl_pct,
                        "actual_pnl_yen_100": actual_yen,
                        "candidate_vs_actual_delta_yen": delta_yen,
                        "no_candidate_trigger": no_trigger,
                        "peak_mfe_pct": peak_mfe,
                        "mfe_bucket": mfe_bucket(peak_mfe),
                        "post_shadow_max_up_pct": round(st.post_shadow_max_up_pct, 4),
                        "post_shadow_max_down_pct": round(st.post_shadow_max_down_pct, 4),
                        "forensic_class": forensic_class,
                    }
                )
        return rows


def export_board_failure_guard_trade_rows(
    pack: Optional[BoardFailureGuardTuningPack],
) -> list[dict[str, Any]]:
    if pack is None:
        return []
    return pack.export_trade_rows()


__all__ = [
    "BASE_VARIANT_ID",
    "BOARD_FAILURE_EXIT_ID",
    "BoardFailureGuardTuningPack",
    "BoardFailureGuardVariant",
    "ENTRY_COOLDOWN_OPTIONS_SEC",
    "PHASE347_COOLDOWN_OPTIONS_SEC",
    "RECLAIM_TICK_OPTIONS",
    "default_phase346_variants",
    "default_phase347_variants",
    "entry_cooldown_blocks",
    "export_board_failure_guard_trade_rows",
    "make_position_id",
    "price_reclaim_cancels",
    "reclaim_window_active",
]
