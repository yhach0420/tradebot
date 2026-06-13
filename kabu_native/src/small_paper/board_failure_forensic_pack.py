"""
Phase345: Forensic collector for board_failure_exit shadow (mfe_lt_0p2_confirm5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from small_paper.board_failure_exit_tuning import (
    BOARD_FAILURE_EXIT_ID,
    BoardFailureTuningPack,
    phase344_mfe_lt_0p2_confirm5_variant,
)
from small_paper.exit_candidate_shadow import _imb_delta, _pnl_pct, _tick_time
from small_paper.realtime_board_exit_shadow import calc_bid_ask_imbalance
from replay.pnl_yen import compute_pnl_yen_100

VARIANT_ID = "mfe_lt_0p2_confirm5"
NOISE_DELTA_YEN = 300.0
FALSE_POS_REBOUND_PCT = 0.3
CORRECT_CUT_DROP_PCT = -0.1


def _board_snapshot(payload: Mapping[str, Any], entry_imb: Optional[float]) -> dict[str, Any]:
    imb = calc_bid_ask_imbalance(payload)
    imb_r = round(float(imb), 6) if imb is not None else None
    delta = _imb_delta(entry_imb, imb_r)
    return {
        "board_imbalance": imb_r,
        "board_imbalance_delta": round(float(delta), 6) if delta is not None else None,
        "bid_qty": payload.get("BidQty"),
        "ask_qty": payload.get("AskQty"),
    }


@dataclass
class _ForensicRecord:
    symbol: str
    position_id: str
    day_key: str = ""
    session_id: str = ""
    entry_time: str = ""
    entry_price: float = 0.0
    entry_board_imbalance: Optional[float] = None
    entry_imbalance_percentile: Optional[float] = None
    peak_mfe_pct: float = 0.0
    mae_pct: float = 0.0
    shadow_triggered: bool = False
    shadow_exit_reason: str = ""
    shadow_exit_time: str = ""
    shadow_exit_price: Optional[float] = None
    shadow_pnl_pct: Optional[float] = None
    shadow_pnl_yen_100: Optional[float] = None
    shadow_board_imbalance: Optional[float] = None
    shadow_board_imbalance_delta: Optional[float] = None
    shadow_bid_qty: Any = None
    shadow_ask_qty: Any = None
    post_shadow_max_up_pct: float = 0.0
    post_shadow_max_down_pct: float = 0.0
    actual_exit_reason: str = ""
    actual_exit_time: str = ""
    actual_exit_price: Optional[float] = None
    actual_pnl_pct: Optional[float] = None
    actual_pnl_yen_100: Optional[float] = None
    actual_board_imbalance: Optional[float] = None
    actual_board_imbalance_delta: Optional[float] = None
    last_payload: Optional[dict[str, Any]] = None


@dataclass
class BoardFailureForensicPack:
    """Replay forensic collector wrapping single-variant board failure shadow."""

    tuning: BoardFailureTuningPack = field(
        default_factory=lambda: BoardFailureTuningPack(
            variants=(phase344_mfe_lt_0p2_confirm5_variant(),)
        )
    )
    records: dict[str, _ForensicRecord] = field(default_factory=dict)

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
        self.tuning.register_position(
            position_id=position_id,
            symbol=symbol,
            entry_time=entry_time,
            entry_price=entry_price,
            payload=payload,
            entry_shadow=entry_shadow,
        )
        entry_imb = calc_bid_ask_imbalance(payload)
        entry_imb_r = round(float(entry_imb), 6) if entry_imb is not None else None
        pct = entry_shadow.get("entry_imbalance_percentile")
        try:
            imb_pct = round(float(pct), 2) if pct is not None and pct != "" else None
        except (TypeError, ValueError):
            imb_pct = None
        self.records[position_id] = _ForensicRecord(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time.isoformat(timespec="seconds"),
            entry_price=float(entry_price),
            entry_board_imbalance=entry_imb_r,
            entry_imbalance_percentile=imb_pct,
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
        rec = self.records.get(position_id)
        if rec is None:
            return

        self.tuning.record_holding_tick(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time,
            payload=payload,
            current_price=current_price,
            entry_price=entry_price,
            mfe_pct=mfe_pct,
            entry_shadow=entry_shadow,
        )

        price = float(current_price)
        pnl = _pnl_pct(entry_price, price)
        rec.peak_mfe_pct = max(rec.peak_mfe_pct, float(mfe_pct))
        rec.mae_pct = min(rec.mae_pct, pnl)
        rec.last_payload = dict(payload)

        pos = self.tuning.positions.get(position_id)
        st = pos.variant_states[VARIANT_ID] if pos else None
        if st and st.triggered and not rec.shadow_triggered:
            rec.shadow_triggered = True
            rec.shadow_exit_reason = BOARD_FAILURE_EXIT_ID
            rec.shadow_exit_time = st.exit_time
            rec.shadow_exit_price = st.exit_price
            rec.shadow_pnl_pct = st.pnl_pct
            rec.shadow_pnl_yen_100 = st.pnl_yen_100
            snap = _board_snapshot(payload, rec.entry_board_imbalance)
            rec.shadow_board_imbalance = snap["board_imbalance"]
            rec.shadow_board_imbalance_delta = snap["board_imbalance_delta"]
            rec.shadow_bid_qty = snap["bid_qty"]
            rec.shadow_ask_qty = snap["ask_qty"]

        if rec.shadow_triggered and rec.shadow_exit_price is not None and entry_price > 0:
            ref = float(rec.shadow_exit_price)
            move_pct = (price - ref) / float(entry_price) * 100.0
            rec.post_shadow_max_up_pct = max(rec.post_shadow_max_up_pct, move_pct)
            rec.post_shadow_max_down_pct = min(rec.post_shadow_max_down_pct, move_pct)

    def finalize_position(
        self,
        *,
        position_id: str,
        actual_exit_reason: str,
        actual_exit_time: datetime,
        actual_exit_price: float,
        entry_price: float,
    ) -> None:
        self.tuning.finalize_position(
            position_id=position_id,
            actual_exit_reason=actual_exit_reason,
            actual_exit_time=actual_exit_time,
            actual_exit_price=actual_exit_price,
            entry_price=entry_price,
        )
        rec = self.records.get(position_id)
        if rec is None:
            return
        rec.actual_exit_reason = actual_exit_reason
        rec.actual_exit_time = actual_exit_time.isoformat(timespec="seconds")
        rec.actual_exit_price = round(float(actual_exit_price), 4)
        rec.actual_pnl_pct = _pnl_pct(entry_price, actual_exit_price)
        rec.actual_pnl_yen_100 = round(compute_pnl_yen_100(entry_price, actual_exit_price), 2)
        if rec.last_payload is not None:
            snap = _board_snapshot(rec.last_payload, rec.entry_board_imbalance)
            rec.actual_board_imbalance = snap["board_imbalance"]
            rec.actual_board_imbalance_delta = snap["board_imbalance_delta"]

    def export_forensic_rows(
        self,
        *,
        session_id: str = "",
        day_key: str = "",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for rec in self.records.values():
            if not rec.actual_exit_reason:
                continue
            shadow_yen = (
                float(rec.shadow_pnl_yen_100)
                if rec.shadow_triggered and rec.shadow_pnl_yen_100 is not None
                else float(rec.actual_pnl_yen_100 or 0.0)
            )
            actual_yen = float(rec.actual_pnl_yen_100 or 0.0)
            delta_yen = round(shadow_yen - actual_yen, 2)
            rows.append(
                {
                    "session_id": session_id,
                    "day_key": day_key,
                    "symbol": rec.symbol,
                    "position_id": rec.position_id,
                    "variant_id": VARIANT_ID,
                    "entry_time": rec.entry_time,
                    "shadow_exit_time": rec.shadow_exit_time,
                    "actual_exit_time": rec.actual_exit_time,
                    "shadow_exit_reason": rec.shadow_exit_reason if rec.shadow_triggered else "",
                    "actual_exit_reason": rec.actual_exit_reason,
                    "shadow_exit_price": rec.shadow_exit_price,
                    "actual_exit_price": rec.actual_exit_price,
                    "shadow_pnl_yen_100": shadow_yen,
                    "actual_pnl_yen_100": actual_yen,
                    "pnl_difference_yen_100": delta_yen,
                    "peak_mfe_pct": round(rec.peak_mfe_pct, 4),
                    "mae_pct": round(rec.mae_pct, 4),
                    "post_shadow_max_up_pct": round(rec.post_shadow_max_up_pct, 4),
                    "post_shadow_max_down_pct": round(rec.post_shadow_max_down_pct, 4),
                    "entry_board_imbalance": rec.entry_board_imbalance,
                    "entry_imbalance_percentile": rec.entry_imbalance_percentile,
                    "shadow_board_imbalance": rec.shadow_board_imbalance,
                    "shadow_board_imbalance_delta": rec.shadow_board_imbalance_delta,
                    "shadow_bid_qty": rec.shadow_bid_qty,
                    "shadow_ask_qty": rec.shadow_ask_qty,
                    "actual_board_imbalance": rec.actual_board_imbalance,
                    "actual_board_imbalance_delta": rec.actual_board_imbalance_delta,
                    "shadow_triggered": rec.shadow_triggered,
                    "forensic_class": classify_forensic_trade(
                        shadow_triggered=rec.shadow_triggered,
                        pnl_difference_yen=delta_yen,
                        actual_pnl_yen=actual_yen,
                        actual_exit_reason=rec.actual_exit_reason,
                        post_shadow_max_up_pct=rec.post_shadow_max_up_pct,
                        post_shadow_max_down_pct=rec.post_shadow_max_down_pct,
                    ),
                }
            )
        return rows


def classify_forensic_trade(
    *,
    shadow_triggered: bool,
    pnl_difference_yen: float,
    actual_pnl_yen: float,
    actual_exit_reason: str,
    post_shadow_max_up_pct: float,
    post_shadow_max_down_pct: float,
) -> str:
    if abs(pnl_difference_yen) < NOISE_DELTA_YEN:
        return "C_noise"
    if not shadow_triggered:
        return "N_no_shadow"
    if actual_pnl_yen > 0 and pnl_difference_yen < -NOISE_DELTA_YEN:
        return "B_false_positive"
    if post_shadow_max_up_pct >= FALSE_POS_REBOUND_PCT:
        return "B_false_positive"
    if post_shadow_max_down_pct <= CORRECT_CUT_DROP_PCT or pnl_difference_yen > 0:
        return "A_correct_cut"
    if pnl_difference_yen < 0:
        return "B_false_positive"
    return "C_noise"


__all__ = [
    "BoardFailureForensicPack",
    "VARIANT_ID",
    "classify_forensic_trade",
]
