"""
Phase339: vwap_assisted_loss_exit parameter tuning (research only).

Evaluates multiple VWAP-tuning variants in one replay pass. Other candidates frozen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from research.research_exit_criteria import _as_float
from small_paper.exit_candidate_shadow import (
    IMB_DETERIORATION_DELTA,
    _imb_delta,
    _pnl_pct,
    _tick_time,
    calc_bid_ask_imbalance,
)
from small_paper.realtime_board_exit_shadow import make_position_id

VWAP_CANDIDATE_ID = "vwap_assisted_loss_exit"


@dataclass(frozen=True)
class VwapTuningVariant:
    variant_id: str
    below_vwap_confirm_ticks: int = 1
    min_vwap_dev_pct: float = 0.0
    board_delta_threshold: float = IMB_DETERIORATION_DELTA
    investigation_axis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "below_vwap_confirm_ticks": self.below_vwap_confirm_ticks,
            "min_vwap_dev_pct": self.min_vwap_dev_pct,
            "board_delta_threshold": self.board_delta_threshold,
            "investigation_axis": self.investigation_axis,
        }


def phase341_vwap_dev_0p4pct_variant() -> VwapTuningVariant:
    return VwapTuningVariant(
        "vwap_dev_0p4pct",
        min_vwap_dev_pct=0.4,
        investigation_axis="vwap_deviation_robustness",
    )


def default_phase340_variants() -> tuple[VwapTuningVariant, ...]:
    """Phase340: fine-tune VWAP deviation around 0.5%."""
    base_board = IMB_DETERIORATION_DELTA
    return (
        VwapTuningVariant(
            "baseline",
            below_vwap_confirm_ticks=1,
            min_vwap_dev_pct=0.0,
            board_delta_threshold=base_board,
            investigation_axis="baseline",
        ),
        VwapTuningVariant(
            "vwap_dev_0p3pct",
            min_vwap_dev_pct=0.3,
            investigation_axis="vwap_deviation_finetune",
        ),
        VwapTuningVariant(
            "vwap_dev_0p4pct",
            min_vwap_dev_pct=0.4,
            investigation_axis="vwap_deviation_finetune",
        ),
        VwapTuningVariant(
            "vwap_dev_0p5pct",
            min_vwap_dev_pct=0.5,
            investigation_axis="vwap_deviation_finetune",
        ),
        VwapTuningVariant(
            "vwap_dev_0p6pct",
            min_vwap_dev_pct=0.6,
            investigation_axis="vwap_deviation_finetune",
        ),
        VwapTuningVariant(
            "vwap_dev_0p7pct",
            min_vwap_dev_pct=0.7,
            investigation_axis="vwap_deviation_finetune",
        ),
    )


def default_phase339_variants() -> tuple[VwapTuningVariant, ...]:
    """Tuning grid for Phase339 investigation axes."""
    base_board = IMB_DETERIORATION_DELTA
    return (
        VwapTuningVariant(
            "baseline",
            below_vwap_confirm_ticks=1,
            min_vwap_dev_pct=0.0,
            board_delta_threshold=base_board,
            investigation_axis="baseline",
        ),
        VwapTuningVariant(
            "vwap_break_immediate",
            below_vwap_confirm_ticks=1,
            investigation_axis="vwap_confirm_ticks",
        ),
        VwapTuningVariant(
            "vwap_break_2tick",
            below_vwap_confirm_ticks=2,
            investigation_axis="vwap_confirm_ticks",
        ),
        VwapTuningVariant(
            "vwap_break_3tick",
            below_vwap_confirm_ticks=3,
            investigation_axis="vwap_confirm_ticks",
        ),
        VwapTuningVariant(
            "vwap_dev_0p1pct",
            min_vwap_dev_pct=0.1,
            investigation_axis="vwap_deviation_pct",
        ),
        VwapTuningVariant(
            "vwap_dev_0p2pct",
            min_vwap_dev_pct=0.2,
            investigation_axis="vwap_deviation_pct",
        ),
        VwapTuningVariant(
            "vwap_dev_0p5pct",
            min_vwap_dev_pct=0.5,
            investigation_axis="vwap_deviation_pct",
        ),
        VwapTuningVariant(
            "board_delta_0p03",
            board_delta_threshold=0.03,
            investigation_axis="board_deterioration_threshold",
        ),
        VwapTuningVariant(
            "board_delta_0p08",
            board_delta_threshold=0.08,
            investigation_axis="board_deterioration_threshold",
        ),
    )


def _vwap_dev_pct_below(current_price: float, vwap: float) -> float:
    if vwap <= 0 or current_price >= vwap:
        return 0.0
    return round((float(vwap) - float(current_price)) / float(vwap) * 100.0, 4)


def _board_deteriorated(delta: Optional[float], threshold: float) -> bool:
    if delta is None:
        return False
    return float(delta) <= -float(threshold)


def vwap_variant_tick_signal(
    *,
    variant: VwapTuningVariant,
    current_pnl_pct: float,
    board_imbalance_delta: Optional[float],
    below_vwap: bool,
    vwap_dev_pct: float,
    vwap_available: bool,
) -> bool:
    """Single-tick signal (VWAP alone never triggers)."""
    if not vwap_available:
        return False
    if float(current_pnl_pct) >= 0.0:
        return False
    if not below_vwap:
        return False
    if not _board_deteriorated(board_imbalance_delta, variant.board_delta_threshold):
        return False
    if vwap_dev_pct < float(variant.min_vwap_dev_pct):
        return False
    return True


def evaluate_vwap_variant(
    *,
    variant: VwapTuningVariant,
    current_pnl_pct: float,
    board_imbalance_delta: Optional[float],
    below_vwap_streak: int,
    vwap_dev_pct: float,
    vwap_available: bool,
    below_vwap: bool,
) -> bool:
    need = max(1, int(variant.below_vwap_confirm_ticks))
    if int(below_vwap_streak) < need:
        return False
    return vwap_variant_tick_signal(
        variant=variant,
        current_pnl_pct=current_pnl_pct,
        board_imbalance_delta=board_imbalance_delta,
        below_vwap=below_vwap,
        vwap_dev_pct=vwap_dev_pct,
        vwap_available=vwap_available,
    )


@dataclass
class _VariantExitState:
    triggered: bool = False
    exit_time: str = ""
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    pnl_yen_100: Optional[float] = None


@dataclass
class _TuningPosition:
    symbol: str
    position_id: str
    entry_time: str
    entry_price: float
    entry_bid_ask_imbalance: Optional[float] = None
    variant_states: dict[str, _VariantExitState] = field(default_factory=dict)
    variant_streaks: dict[str, int] = field(default_factory=dict)
    vwap_available: bool = False
    actual_exit_reason: str = ""
    actual_exit_time: str = ""
    actual_exit_price: Optional[float] = None
    actual_pnl_pct: Optional[float] = None
    actual_pnl_yen_100: Optional[float] = None


@dataclass
class VwapAssistedLossTuningPack:
    """Multi-variant VWAP shadow evaluator (single replay, all variants in parallel)."""

    variants: tuple[VwapTuningVariant, ...] = field(default_factory=default_phase339_variants)
    positions: dict[str, _TuningPosition] = field(default_factory=dict)
    trigger_counts: dict[str, int] = field(default_factory=dict)
    vwap_eval_ticks: int = 0
    vwap_missing_ticks: int = 0

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
        rec = _TuningPosition(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time.isoformat(timespec="seconds"),
            entry_price=float(entry_price),
            entry_bid_ask_imbalance=entry_imb_r,
            variant_states={v.variant_id: _VariantExitState() for v in self.variants},
        )
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
        del symbol, entry_time, entry_shadow, mfe_pct
        rec = self.positions.get(position_id)
        if rec is None:
            return

        price = float(current_price)
        pnl = _pnl_pct(entry_price, price)
        current_imb = calc_bid_ask_imbalance(payload)
        current_imb_r = round(float(current_imb), 6) if current_imb is not None else None
        imb_delta = _imb_delta(rec.entry_bid_ask_imbalance, current_imb_r)
        tick_dt = _tick_time(payload)

        vwap = _as_float(payload.get("VWAP"))
        vwap_ok = vwap is not None and float(vwap) > 0
        rec.vwap_available = rec.vwap_available or vwap_ok
        self.vwap_eval_ticks += 1
        if not vwap_ok:
            self.vwap_missing_ticks += 1

        vwap_dev = _vwap_dev_pct_below(price, float(vwap)) if vwap_ok else 0.0
        below_vwap = bool(vwap_ok and price < float(vwap))

        from replay.pnl_yen import compute_pnl_yen_100

        for variant in self.variants:
            vid = variant.variant_id
            st = rec.variant_states[vid]
            if st.triggered:
                continue
            if vwap_variant_tick_signal(
                variant=variant,
                current_pnl_pct=pnl,
                board_imbalance_delta=imb_delta,
                below_vwap=below_vwap,
                vwap_dev_pct=vwap_dev,
                vwap_available=rec.vwap_available,
            ):
                rec.variant_streaks[vid] = rec.variant_streaks.get(vid, 0) + 1
            else:
                rec.variant_streaks[vid] = 0
            if evaluate_vwap_variant(
                variant=variant,
                current_pnl_pct=pnl,
                board_imbalance_delta=imb_delta,
                below_vwap_streak=rec.variant_streaks.get(vid, 0),
                vwap_dev_pct=vwap_dev,
                vwap_available=rec.vwap_available,
                below_vwap=below_vwap,
            ):
                st.triggered = True
                st.exit_time = tick_dt.isoformat(timespec="seconds")
                st.exit_price = round(price, 4)
                st.pnl_pct = pnl
                st.pnl_yen_100 = round(compute_pnl_yen_100(entry_price, price), 2)
                self.trigger_counts[variant.variant_id] = (
                    self.trigger_counts.get(variant.variant_id, 0) + 1
                )

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
                        "candidate_id": VWAP_CANDIDATE_ID,
                        "variant_id": vid,
                        "investigation_axis": variant.investigation_axis,
                        "shadow_exit_reason": VWAP_CANDIDATE_ID if st.triggered else "",
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
                        **variant.to_dict(),
                    }
                )
        return rows


def export_vwap_tuning_trade_rows(pack: Optional[VwapAssistedLossTuningPack]) -> list[dict[str, Any]]:
    if pack is None:
        return []
    return pack.export_trade_rows()


__all__ = [
    "VWAP_CANDIDATE_ID",
    "VwapAssistedLossTuningPack",
    "VwapTuningVariant",
    "default_phase339_variants",
    "default_phase340_variants",
    "phase341_vwap_dev_0p4pct_variant",
    "evaluate_vwap_variant",
    "vwap_variant_tick_signal",
    "export_vwap_tuning_trade_rows",
    "make_position_id",
]
