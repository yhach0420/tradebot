"""
Phase356: Combined EXIT rebaseline pack (post-Phase355 ENTRY guard).

Evaluates in one push-replay pass:
- current_board_dynamic (actual production baseline)
- board_failure_cd60
- profit_protect_exit / high_update_failure_exit
- board-dynamic parameter tuning variants
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from small_paper.board_dynamic_trailing_shadow import (
    board_tier_from_percentile,
    simulate_board_dynamic_shadow_exit,
    trailing_params_for_board_tier,
)
from small_paper.board_failure_false_positive_guard import (
    BoardFailureGuardTuningPack,
    BoardFailureGuardVariant,
)
from small_paper.exit_candidate_shadow import ExitCandidateShadowPack

CURRENT_BOARD_DYNAMIC_ID = "current_board_dynamic"
BOARD_FAILURE_CD60_ID = "board_failure_cd60"
BF_INTERNAL_CD60_ID = "mfe_lt_0p2_confirm5_cd60"

PHASE356_EXIT_CANDIDATE_IDS: tuple[str, ...] = (
    CURRENT_BOARD_DYNAMIC_ID,
    BOARD_FAILURE_CD60_ID,
    "profit_protect_exit",
    "high_update_failure_exit",
    "bd_current_resim",
    "bd_low_act_0.8",
    "bd_low_gb_30",
    "bd_low_gb_50",
    "bd_high_gb_50",
    "bd_high_gb_70",
)


@dataclass(frozen=True)
class BoardDynamicTuningSpec:
    candidate_id: str
    low_activate_pct: float
    low_giveback_frac: float
    high_activate_pct: float
    high_giveback_frac: float


def default_board_dynamic_tuning_specs() -> tuple[BoardDynamicTuningSpec, ...]:
    prod_low = (0.6, 0.40)
    prod_high = (1.0, 0.60)
    return (
        BoardDynamicTuningSpec("bd_current_resim", *prod_low, *prod_high),
        BoardDynamicTuningSpec("bd_low_act_0.8", 0.8, 0.40, *prod_high),
        BoardDynamicTuningSpec("bd_low_gb_30", 0.6, 0.30, *prod_high),
        BoardDynamicTuningSpec("bd_low_gb_50", 0.6, 0.50, *prod_high),
        BoardDynamicTuningSpec("bd_high_gb_50", *prod_low, 1.0, 0.50),
        BoardDynamicTuningSpec("bd_high_gb_70", *prod_low, 1.0, 0.70),
    )


def _cd60_variant() -> BoardFailureGuardVariant:
    return BoardFailureGuardVariant(
        variant_id=BF_INTERNAL_CD60_ID,
        entry_cooldown_sec=60,
    )


@dataclass
class Phase356ExitRebaselinePack:
    hard_stop_pct: float = 1.2
    exit_pack: ExitCandidateShadowPack = field(default_factory=lambda: ExitCandidateShadowPack(
        active_candidates=("profit_protect_exit", "high_update_failure_exit"),
        enable_extend=False,
    ))
    bf_pack: BoardFailureGuardTuningPack = field(
        default_factory=lambda: BoardFailureGuardTuningPack(variants=(_cd60_variant(),))
    )
    bd_specs: tuple[BoardDynamicTuningSpec, ...] = field(
        default_factory=default_board_dynamic_tuning_specs
    )
    _rich_ticks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _entry_imb_pct: dict[str, Optional[float]] = field(default_factory=dict)
    _actual_exit_ts: dict[str, float] = field(default_factory=dict)
    _universe_meta: dict[str, dict[str, str]] = field(default_factory=dict)

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
        self.exit_pack.register_position(
            position_id=position_id,
            symbol=symbol,
            entry_time=entry_time,
            entry_price=entry_price,
            payload=payload,
            entry_shadow=entry_shadow,
        )
        self.bf_pack.register_position(
            position_id=position_id,
            symbol=symbol,
            entry_time=entry_time,
            entry_price=entry_price,
            payload=payload,
            entry_shadow=entry_shadow,
        )
        imb = entry_shadow.get("entry_imbalance_percentile")
        try:
            imb_f = float(imb) if imb not in (None, "") else None
        except (TypeError, ValueError):
            imb_f = None
        self._entry_imb_pct[position_id] = imb_f
        self._rich_ticks[position_id] = []
        slot = str(entry_shadow.get("universe_slot") or payload.get("universe_slot") or "")
        bucket = str(
            entry_shadow.get("universe_bucket")
            or entry_shadow.get("source_bucket")
            or payload.get("universe_bucket")
            or payload.get("source_bucket")
            or ""
        )
        self._universe_meta[position_id] = {
            "universe_slot": slot,
            "universe_bucket": bucket,
        }

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
        from small_paper.exit_candidate_shadow import _pnl_pct, _tick_time

        tick_dt = _tick_time(payload)
        price = float(current_price)
        pnl = _pnl_pct(entry_price, price)
        ticks = self._rich_ticks.setdefault(position_id, [])
        ticks.append(
            {
                "ts": tick_dt.isoformat(timespec="seconds"),
                "ts_epoch": tick_dt.timestamp(),
                "price": price,
                "pnl_pct": pnl,
            }
        )
        self.exit_pack.record_holding_tick(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time,
            payload=payload,
            current_price=current_price,
            entry_price=entry_price,
            mfe_pct=mfe_pct,
            entry_shadow=entry_shadow,
        )
        self.bf_pack.record_holding_tick(
            symbol=symbol,
            position_id=position_id,
            entry_time=entry_time,
            payload=payload,
            current_price=current_price,
            entry_price=entry_price,
            mfe_pct=mfe_pct,
            entry_shadow=entry_shadow,
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
        self.exit_pack.finalize_position(
            position_id=position_id,
            actual_exit_reason=actual_exit_reason,
            actual_exit_time=actual_exit_time,
            actual_exit_price=actual_exit_price,
            entry_price=entry_price,
        )
        self.bf_pack.finalize_position(
            position_id=position_id,
            actual_exit_reason=actual_exit_reason,
            actual_exit_time=actual_exit_time,
            actual_exit_price=actual_exit_price,
            entry_price=entry_price,
        )
        self._actual_exit_ts[position_id] = actual_exit_time.timestamp()

    def _bd_shadow_row(
        self,
        *,
        rec: Any,
        spec: BoardDynamicTuningSpec,
        actual_yen: float,
    ) -> dict[str, Any]:
        position_id = rec.position_id
        ticks = self._rich_ticks.get(position_id) or []
        imb = self._entry_imb_pct.get(position_id)
        tier = board_tier_from_percentile(imb)
        if tier == "board_high":
            act, gb = spec.high_activate_pct, spec.high_giveback_frac
        else:
            act, gb = spec.low_activate_pct, spec.low_giveback_frac
        cutoff = self._actual_exit_ts.get(position_id)
        sim = simulate_board_dynamic_shadow_exit(
            ticks,
            entry_price=float(rec.entry_price),
            hard_stop_pct=self.hard_stop_pct,
            entry_imbalance_percentile=imb,
            cutoff_ts=cutoff,
            activate_pct=act,
            giveback_frac=gb,
            tier_label=tier,
        )
        shadow_yen = float(sim.get("shadow_pnl_yen_100") or 0.0)
        delta = round(shadow_yen - actual_yen, 2)
        uni = self._universe_meta.get(position_id) or {}
        return {
            "symbol": rec.symbol,
            "position_id": position_id,
            "entry_time": rec.entry_time,
            "universe_slot": uni.get("universe_slot") or "",
            "universe_bucket": uni.get("universe_bucket") or "",
            "candidate_id": spec.candidate_id,
            "shadow_exit_reason": sim.get("shadow_exit_reason") or "",
            "shadow_pnl_yen_100": shadow_yen,
            "actual_exit_reason": rec.actual_exit_reason,
            "actual_exit_time": rec.actual_exit_time,
            "actual_exit_price": rec.actual_exit_price,
            "actual_pnl_pct": rec.actual_pnl_pct,
            "actual_pnl_yen_100": actual_yen,
            "candidate_vs_actual_delta_yen": delta,
            "no_candidate_trigger": abs(delta) < 0.01,
            "board_dynamic_tier": tier,
            "board_dynamic_activate_pct": act,
            "board_dynamic_giveback_frac": gb,
        }

    def export_trade_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for rec in self.exit_pack.positions.values():
            if not rec.actual_exit_reason:
                continue
            actual_yen = float(rec.actual_pnl_yen_100 or 0.0)
            uni = self._universe_meta.get(rec.position_id) or {}
            rows.append(
                {
                    "symbol": rec.symbol,
                    "position_id": rec.position_id,
                    "entry_time": rec.entry_time,
                    "universe_slot": uni.get("universe_slot") or "",
                    "universe_bucket": uni.get("universe_bucket") or "",
                    "candidate_id": CURRENT_BOARD_DYNAMIC_ID,
                    "shadow_exit_reason": rec.actual_exit_reason,
                    "shadow_pnl_yen_100": actual_yen,
                    "actual_exit_reason": rec.actual_exit_reason,
                    "actual_exit_time": rec.actual_exit_time,
                    "actual_exit_price": rec.actual_exit_price,
                    "actual_pnl_pct": rec.actual_pnl_pct,
                    "actual_pnl_yen_100": actual_yen,
                    "candidate_vs_actual_delta_yen": 0.0,
                    "no_candidate_trigger": True,
                }
            )

        for er in self.exit_pack.export_trade_rows():
            cid = str(er.get("candidate_id") or "")
            if cid in ("profit_protect_exit", "high_update_failure_exit"):
                row = dict(er)
                pid = str(row.get("position_id") or "")
                uni = self._universe_meta.get(pid) or {}
                row.setdefault("universe_slot", uni.get("universe_slot") or "")
                row.setdefault("universe_bucket", uni.get("universe_bucket") or "")
                rows.append(row)

        for br in self.bf_pack.export_trade_rows():
            if br.get("variant_id") != BF_INTERNAL_CD60_ID:
                continue
            pid = str(br.get("position_id") or "")
            uni = self._universe_meta.get(pid) or {}
            rows.append(
                {
                    "symbol": br.get("symbol"),
                    "position_id": pid,
                    "entry_time": br.get("entry_time") or "",
                    "universe_slot": uni.get("universe_slot") or "",
                    "universe_bucket": uni.get("universe_bucket") or "",
                    "candidate_id": BOARD_FAILURE_CD60_ID,
                    "shadow_exit_reason": BOARD_FAILURE_CD60_ID if not br.get("no_candidate_trigger") else "",
                    "shadow_pnl_yen_100": br.get("shadow_pnl_yen_100"),
                    "actual_exit_reason": br.get("actual_exit_reason"),
                    "actual_exit_time": "",
                    "actual_exit_price": None,
                    "actual_pnl_pct": None,
                    "actual_pnl_yen_100": br.get("actual_pnl_yen_100"),
                    "candidate_vs_actual_delta_yen": br.get("candidate_vs_actual_delta_yen"),
                    "no_candidate_trigger": br.get("no_candidate_trigger"),
                    "peak_mfe_pct": br.get("peak_mfe_pct"),
                }
            )

        for rec in self.exit_pack.positions.values():
            if not rec.actual_exit_reason:
                continue
            actual_yen = float(rec.actual_pnl_yen_100 or 0.0)
            for spec in self.bd_specs:
                rows.append(self._bd_shadow_row(rec=rec, spec=spec, actual_yen=actual_yen))

        return rows


def export_phase356_exit_rebaseline_trade_rows(
    pack: Optional[Phase356ExitRebaselinePack],
) -> list[dict[str, Any]]:
    if pack is None:
        return []
    return pack.export_trade_rows()
