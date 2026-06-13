"""
Phase342: Board failure EXIT shadow (no VWAP).

Detects dying positions via board collapse + new price low + 3-tick confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from small_paper.exit_candidate_shadow import (
    _imb_delta,
    _pnl_pct,
    _tick_time,
    calc_bid_ask_imbalance,
)
from small_paper.realtime_board_exit_shadow import make_position_id

BOARD_FAILURE_EXIT_ID = "board_failure_exit"
BOARD_FAILURE_IMB_DELTA = -0.08
BOARD_FAILURE_CONFIRM_TICKS = 3

MFE_BUCKET_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("mfe_lt_0p3", 0.3),
    ("mfe_lt_0p5", 0.5),
    ("mfe_lt_1p0", 1.0),
)


def mfe_bucket(peak_mfe_pct: float) -> str:
    if peak_mfe_pct < 0.3:
        return "mfe_lt_0p3"
    if peak_mfe_pct < 0.5:
        return "mfe_lt_0p5"
    if peak_mfe_pct < 1.0:
        return "mfe_lt_1p0"
    return "mfe_ge_1p0"


def board_failure_deterioration_tick(
    *,
    current_pnl_pct: float,
    board_imbalance_delta: Optional[float],
) -> bool:
    if float(current_pnl_pct) >= 0.0:
        return False
    if board_imbalance_delta is None:
        return False
    return float(board_imbalance_delta) <= BOARD_FAILURE_IMB_DELTA


def board_failure_arm_tick(
    *,
    current_pnl_pct: float,
    board_imbalance_delta: Optional[float],
    low_updated_this_tick: bool,
) -> bool:
    return board_failure_deterioration_tick(
        current_pnl_pct=current_pnl_pct,
        board_imbalance_delta=board_imbalance_delta,
    ) and bool(low_updated_this_tick)


def evaluate_board_failure_exit(
    *,
    armed: bool,
    confirm_streak: int,
    confirm_ticks: int = BOARD_FAILURE_CONFIRM_TICKS,
) -> bool:
    return bool(armed) and int(confirm_streak) >= max(1, int(confirm_ticks))


@dataclass
class _ShadowExitState:
    triggered: bool = False
    exit_time: str = ""
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    pnl_yen_100: Optional[float] = None
    peak_mfe_pct: float = 0.0
    armed: bool = False
    confirm_streak: int = 0


@dataclass
class _PositionRecord:
    symbol: str
    position_id: str
    entry_time: str
    entry_price: float
    entry_bid_ask_imbalance: Optional[float] = None
    session_low: float = 0.0
    peak_mfe_pct: float = 0.0
    shadow: _ShadowExitState = field(default_factory=_ShadowExitState)
    actual_exit_reason: str = ""
    actual_exit_time: str = ""
    actual_exit_price: Optional[float] = None
    actual_pnl_pct: Optional[float] = None
    actual_pnl_yen_100: Optional[float] = None


@dataclass
class BoardFailureExitShadowPack:
    """Single-candidate board failure shadow (PUSH bid/ask only)."""

    positions: dict[str, _PositionRecord] = field(default_factory=dict)
    trigger_count: int = 0
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
        self.positions[position_id] = _PositionRecord(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time.isoformat(timespec="seconds"),
            entry_price=ep,
            entry_bid_ask_imbalance=entry_imb_r,
            session_low=ep,
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

        st = rec.shadow
        if not st.triggered:
            deteriorating = board_failure_deterioration_tick(
                current_pnl_pct=pnl,
                board_imbalance_delta=imb_delta,
            )
            if not st.armed:
                if board_failure_arm_tick(
                    current_pnl_pct=pnl,
                    board_imbalance_delta=imb_delta,
                    low_updated_this_tick=low_updated,
                ):
                    st.armed = True
                    st.confirm_streak = 1
            elif deteriorating:
                st.confirm_streak += 1
            else:
                st.armed = False
                st.confirm_streak = 0

            if evaluate_board_failure_exit(
                armed=st.armed,
                confirm_streak=st.confirm_streak,
            ):
                from replay.pnl_yen import compute_pnl_yen_100

                tick_dt = _tick_time(payload)
                st.triggered = True
                st.exit_time = tick_dt.isoformat(timespec="seconds")
                st.exit_price = round(price, 4)
                st.pnl_pct = pnl
                st.pnl_yen_100 = round(compute_pnl_yen_100(entry_price, price), 2)
                st.peak_mfe_pct = rec.peak_mfe_pct
                self.trigger_count += 1

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
            st = rec.shadow
            actual_yen = float(rec.actual_pnl_yen_100 or 0.0)
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
            rows.append(
                {
                    "symbol": rec.symbol,
                    "position_id": rec.position_id,
                    "entry_time": rec.entry_time,
                    "candidate_id": BOARD_FAILURE_EXIT_ID,
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
                    "candidate_vs_actual_delta_yen": round(shadow_yen - actual_yen, 2),
                    "no_candidate_trigger": no_trigger,
                    "peak_mfe_pct": peak_mfe,
                    "mfe_bucket": mfe_bucket(peak_mfe),
                }
            )
        return rows


def export_board_failure_trade_rows(
    pack: Optional[BoardFailureExitShadowPack],
) -> list[dict[str, Any]]:
    if pack is None:
        return []
    return pack.export_trade_rows()


def trade_in_mfe_cohort(row: Mapping[str, Any], cohort: str) -> bool:
    """Cohort = all | mfe_lt_0p3 | mfe_lt_0p5 | mfe_lt_1p0 (cumulative max MFE ceiling)."""
    if cohort == "all":
        return True
    peak = float(row.get("peak_mfe_pct") or 0.0)
    for name, thresh in MFE_BUCKET_THRESHOLDS:
        if cohort == name:
            return peak < thresh
    return mfe_bucket(peak) == cohort


__all__ = [
    "BOARD_FAILURE_EXIT_ID",
    "BOARD_FAILURE_CONFIRM_TICKS",
    "BOARD_FAILURE_IMB_DELTA",
    "MFE_BUCKET_THRESHOLDS",
    "BoardFailureExitShadowPack",
    "board_failure_arm_tick",
    "board_failure_deterioration_tick",
    "evaluate_board_failure_exit",
    "export_board_failure_trade_rows",
    "make_position_id",
    "mfe_bucket",
    "trade_in_mfe_cohort",
]
