"""
kabu_exit_v1 — 大損優先の EXIT 評価（シャドウ算出のみ Phase 5F-E0）。

仕様: docs/kabu_signal_design.md §11
実際の paper_trade 決済・Discord には接続しない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

EXIT_VERSION = "kabu_exit_v1"

PRIORITY_HARD_STOP = 1
PRIORITY_BREAKOUT_FAILURE = 2
PRIORITY_VWAP_RECLAIM_FAILURE = 3
PRIORITY_HIGH_UPDATE_STALL = 4
PRIORITY_BOARD_IMBALANCE_DETERIORATION = 5
PRIORITY_SPREAD_WIDENING = 6
PRIORITY_PUSH_DENSITY_DROP = 7
PRIORITY_TIME_STOP = 8

REASON_NO_POSITION = "NO_POSITION_SHADOW"


@dataclass(frozen=True)
class KabuExitV1Config:
    hard_stop_pct_a: float = 1.35
    hard_stop_pct_b: float = 1.20
    fail_buffer_pct_a: float = 0.12
    fail_buffer_pct_b: float = 0.10
    fail_window_sec: float = 120.0
    vwap_exit_below_pct_a: float = -0.05
    vwap_exit_below_pct_b: float = -0.03
    entry_vwap_min_for_exit: float = 0.25
    high_stall_min_a: float = 5.0
    high_stall_min_b: float = 4.0
    high_stall_max_pnl_a: float = 0.15
    high_stall_max_pnl_b: float = 0.20
    giveback_from_peak_pct_a: float = 0.25
    giveback_from_peak_pct_b: float = 0.20
    high_stall_tick_tolerance: float = 0.01
    imb_exit_max_a: float = 0.46
    imb_exit_max_b: float = 0.48
    imb_low_streak_required: int = 3
    imb_exit_max_pnl_pct: float = 0.40
    spread_hold_max_a: float = 18.0
    spread_hold_max_b: float = 15.0
    spread_exit_max_pnl_pct: float = 0.20
    push_min_hold_a: float = 5.0
    push_min_hold_b: float = 6.0
    push_sparse_min_elapsed_min: float = 2.0
    time_stop_max_a: float = 12.0
    time_stop_max_b: float = 9.0
    mfe_min_pct_a: float = 0.25
    mfe_min_pct_b: float = 0.30


@dataclass
class KabuExitEvalInput:
    entry_price: float
    current_price: float
    entry_time: datetime
    now_time: datetime
    high_since_entry: float
    max_favorable_excursion_pct: Optional[float] = None
    current_vwap: Optional[float] = None
    entry_vwap_dist_pct: Optional[float] = None
    spread_bps: Optional[float] = None
    board_imbalance: Optional[float] = None
    push_density_1m: int = 0
    push_density_3m_avg: Optional[float] = None
    tier: str = "B"
    breakout_trigger_level: float = 0.0
    session_high_at_entry: Optional[float] = None
    session_high_now: Optional[float] = None
    imbalance_low_streak: int = 0
    max_price_since_entry: Optional[float] = None


@dataclass
class KabuExitEvalResult:
    would_exit: bool
    exit_reason: str
    exit_priority: int
    unrealized_pct: Optional[float]
    mfe_pct: Optional[float]
    elapsed_min: Optional[float]
    exit_thresholds_used: dict[str, Any] = field(default_factory=dict)
    exit_debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "would_exit": self.would_exit,
            "exit_reason": self.exit_reason,
            "exit_priority": self.exit_priority,
            "unrealized_pct": self.unrealized_pct,
            "mfe_pct": self.mfe_pct,
            "elapsed_min": self.elapsed_min,
            "exit_thresholds_used": dict(self.exit_thresholds_used),
            "exit_debug": dict(self.exit_debug),
        }


def _tier_key(tier: str) -> str:
    t = (tier or "B").strip().upper()
    return t if t in ("A", "B", "C") else "B"


def _pct_change(current: float, base: float) -> Optional[float]:
    if base <= 0:
        return None
    return ((float(current) - float(base)) / float(base)) * 100.0


def _elapsed_minutes(entry_time: datetime, now_time: datetime) -> float:
    e = entry_time.astimezone(timezone.utc)
    n = now_time.astimezone(timezone.utc)
    return max(0.0, (n - e).total_seconds() / 60.0)


def _thresholds_for_tier(cfg: KabuExitV1Config, tier: str) -> dict[str, Any]:
    if _tier_key(tier) == "A":
        return {
            "tier": "A",
            "hard_stop_pct": cfg.hard_stop_pct_a,
            "fail_buffer_pct": cfg.fail_buffer_pct_a,
            "fail_window_sec": cfg.fail_window_sec,
            "vwap_exit_below_pct": cfg.vwap_exit_below_pct_a,
            "high_stall_min": cfg.high_stall_min_a,
            "high_stall_max_pnl": cfg.high_stall_max_pnl_a,
            "giveback_from_peak_pct": cfg.giveback_from_peak_pct_a,
            "imb_exit_max": cfg.imb_exit_max_a,
            "spread_hold_max": cfg.spread_hold_max_a,
            "push_min_hold": cfg.push_min_hold_a,
            "time_stop_max": cfg.time_stop_max_a,
            "mfe_min_pct": cfg.mfe_min_pct_a,
        }
    return {
        "tier": "B",
        "hard_stop_pct": cfg.hard_stop_pct_b,
        "fail_buffer_pct": cfg.fail_buffer_pct_b,
        "fail_window_sec": cfg.fail_window_sec,
        "vwap_exit_below_pct": cfg.vwap_exit_below_pct_b,
        "high_stall_min": cfg.high_stall_min_b,
        "high_stall_max_pnl": cfg.high_stall_max_pnl_b,
        "giveback_from_peak_pct": cfg.giveback_from_peak_pct_b,
        "imb_exit_max": cfg.imb_exit_max_b,
        "spread_hold_max": cfg.spread_hold_max_b,
        "push_min_hold": cfg.push_min_hold_b,
        "time_stop_max": cfg.time_stop_max_b,
        "mfe_min_pct": cfg.mfe_min_pct_b,
    }


def evaluate_kabu_exit_v1(
    inp: KabuExitEvalInput,
    *,
    has_position: bool = True,
    cfg: Optional[KabuExitV1Config] = None,
) -> KabuExitEvalResult:
    """
    kabu_exit_v1 を 1 回評価。§11.9 の優先順で最初に成立したルールを返す。

    has_position=False のときは would_exit=false, exit_reason=NO_POSITION_SHADOW。
    """
    cfg = cfg or KabuExitV1Config()
    thr = _thresholds_for_tier(cfg, inp.tier)

    entry = float(inp.entry_price)
    price = float(inp.current_price)
    peak = float(inp.high_since_entry)
    max_px = float(inp.max_price_since_entry) if inp.max_price_since_entry is not None else peak
    trigger = float(inp.breakout_trigger_level)

    unrealized = _pct_change(price, entry)
    mfe = (
        float(inp.max_favorable_excursion_pct)
        if inp.max_favorable_excursion_pct is not None
        else _pct_change(peak, entry)
    )
    elapsed = _elapsed_minutes(inp.entry_time, inp.now_time)
    elapsed_sec = elapsed * 60.0

    debug_base: dict[str, Any] = {
        "version": EXIT_VERSION,
        "entry_price": entry,
        "current_price": price,
        "peak_since_entry": peak,
        "max_price_since_entry": max_px,
        "trigger_level": trigger,
        "unrealized_pct": unrealized,
        "mfe_pct": mfe,
        "elapsed_min": elapsed,
    }

    if not has_position:
        return KabuExitEvalResult(
            would_exit=False,
            exit_reason=REASON_NO_POSITION,
            exit_priority=0,
            unrealized_pct=unrealized,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={**debug_base, "has_position": False},
        )

    if unrealized is None:
        return KabuExitEvalResult(
            would_exit=False,
            exit_reason="INVALID_ENTRY_PRICE",
            exit_priority=0,
            unrealized_pct=None,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={**debug_base, "error": "entry_price<=0"},
        )

    vwap_dist_now: Optional[float] = None
    if inp.current_vwap is not None and float(inp.current_vwap) > 0:
        vwap_dist_now = _pct_change(price, float(inp.current_vwap))

    hard_lim = float(thr["hard_stop_pct"])
    if unrealized <= -hard_lim:
        return KabuExitEvalResult(
            would_exit=True,
            exit_reason="hard_stop",
            exit_priority=PRIORITY_HARD_STOP,
            unrealized_pct=unrealized,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={**debug_base, "rule": "unrealized<=-hard_stop_pct", "limit": -hard_lim},
        )

    fail_pct = float(thr["fail_buffer_pct"])
    fail_window = float(thr["fail_window_sec"])
    fail_level = trigger * (1.0 - fail_pct / 100.0)
    if elapsed_sec <= fail_window and max_px >= trigger and price <= fail_level:
        return KabuExitEvalResult(
            would_exit=True,
            exit_reason="breakout_failure",
            exit_priority=PRIORITY_BREAKOUT_FAILURE,
            unrealized_pct=unrealized,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={
                **debug_base,
                "fail_level": fail_level,
                "fail_buffer_pct": fail_pct,
                "within_fail_window_sec": True,
            },
        )

    entry_vwap_dist = inp.entry_vwap_dist_pct
    vwap_below = float(thr["vwap_exit_below_pct"])
    if (
        entry_vwap_dist is not None
        and float(entry_vwap_dist) >= cfg.entry_vwap_min_for_exit
        and vwap_dist_now is not None
        and vwap_dist_now <= vwap_below
    ):
        return KabuExitEvalResult(
            would_exit=True,
            exit_reason="vwap_reclaim_failure",
            exit_priority=PRIORITY_VWAP_RECLAIM_FAILURE,
            unrealized_pct=unrealized,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={
                **debug_base,
                "entry_vwap_dist_pct": entry_vwap_dist,
                "vwap_dist_now": vwap_dist_now,
                "vwap_below_limit": vwap_below,
            },
        )

    h0 = inp.session_high_at_entry
    h_now = inp.session_high_now
    stall_min = float(thr["high_stall_min"])
    stall_max_pnl = float(thr["high_stall_max_pnl"])
    giveback_lim = float(thr["giveback_from_peak_pct"])
    if (
        h0 is not None
        and h_now is not None
        and h_now <= float(h0) + cfg.high_stall_tick_tolerance
        and elapsed >= stall_min
        and unrealized <= stall_max_pnl
        and peak > 0
    ):
        giveback = ((peak - price) / peak) * 100.0
        if giveback >= giveback_lim:
            return KabuExitEvalResult(
                would_exit=True,
                exit_reason="high_update_stall",
                exit_priority=PRIORITY_HIGH_UPDATE_STALL,
                unrealized_pct=unrealized,
                mfe_pct=mfe,
                elapsed_min=elapsed,
                exit_thresholds_used=thr,
                exit_debug={
                    **debug_base,
                    "session_high_at_entry": h0,
                    "session_high_now": h_now,
                    "giveback_from_peak_pct": giveback,
                },
            )

    imb_max = float(thr["imb_exit_max"])
    if (
        inp.board_imbalance is not None
        and inp.imbalance_low_streak >= cfg.imb_low_streak_required
        and float(inp.board_imbalance) <= imb_max
        and unrealized < cfg.imb_exit_max_pnl_pct
    ):
        return KabuExitEvalResult(
            would_exit=True,
            exit_reason="board_imbalance_deterioration",
            exit_priority=PRIORITY_BOARD_IMBALANCE_DETERIORATION,
            unrealized_pct=unrealized,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={
                **debug_base,
                "board_imbalance": inp.board_imbalance,
                "imbalance_low_streak": inp.imbalance_low_streak,
                "imb_exit_max": imb_max,
            },
        )

    spread_lim = float(thr["spread_hold_max"])
    if (
        inp.spread_bps is not None
        and float(inp.spread_bps) >= spread_lim
        and unrealized < cfg.spread_exit_max_pnl_pct
    ):
        return KabuExitEvalResult(
            would_exit=True,
            exit_reason="spread_widening",
            exit_priority=PRIORITY_SPREAD_WIDENING,
            unrealized_pct=unrealized,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={**debug_base, "spread_bps": inp.spread_bps, "spread_limit": spread_lim},
        )

    push_min_hold = float(thr["push_min_hold"])
    if (
        inp.push_density_3m_avg is not None
        and elapsed >= cfg.push_sparse_min_elapsed_min
        and float(inp.push_density_3m_avg) < push_min_hold
    ):
        return KabuExitEvalResult(
            would_exit=True,
            exit_reason="push_density_drop",
            exit_priority=PRIORITY_PUSH_DENSITY_DROP,
            unrealized_pct=unrealized,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={
                **debug_base,
                "push_density_3m_avg": inp.push_density_3m_avg,
                "push_min_hold": push_min_hold,
            },
        )

    time_max = float(thr["time_stop_max"])
    mfe_min = float(thr["mfe_min_pct"])
    if elapsed >= time_max and (mfe is None or mfe < mfe_min):
        return KabuExitEvalResult(
            would_exit=True,
            exit_reason="time_stop",
            exit_priority=PRIORITY_TIME_STOP,
            unrealized_pct=unrealized,
            mfe_pct=mfe,
            elapsed_min=elapsed,
            exit_thresholds_used=thr,
            exit_debug={**debug_base, "time_stop_max": time_max, "mfe_min_pct": mfe_min},
        )

    return KabuExitEvalResult(
        would_exit=False,
        exit_reason="HOLD_SHADOW",
        exit_priority=0,
        unrealized_pct=unrealized,
        mfe_pct=mfe,
        elapsed_min=elapsed,
        exit_thresholds_used=thr,
        exit_debug={**debug_base, "has_position": True},
    )


def evaluate_kabu_exit_v1_from_mapping(
    data: Mapping[str, Any],
    *,
    has_position: bool = True,
    cfg: Optional[KabuExitV1Config] = None,
) -> KabuExitEvalResult:
    """dict から KabuExitEvalInput を組み立てて評価。"""

    def _dt(key: str) -> datetime:
        raw = data[key]
        if isinstance(raw, datetime):
            return raw.astimezone(timezone.utc)
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        raise ValueError(f"missing or invalid datetime: {key}")

    inp = KabuExitEvalInput(
        entry_price=float(data["entry_price"]),
        current_price=float(data["current_price"]),
        entry_time=_dt("entry_time"),
        now_time=_dt("now_time"),
        high_since_entry=float(data.get("high_since_entry", data["current_price"])),
        max_favorable_excursion_pct=(
            float(data["max_favorable_excursion_pct"])
            if data.get("max_favorable_excursion_pct") is not None
            else None
        ),
        current_vwap=float(data["current_vwap"]) if data.get("current_vwap") is not None else None,
        entry_vwap_dist_pct=(
            float(data["entry_vwap_dist_pct"])
            if data.get("entry_vwap_dist_pct") is not None
            else None
        ),
        spread_bps=float(data["spread_bps"]) if data.get("spread_bps") is not None else None,
        board_imbalance=(
            float(data["board_imbalance"]) if data.get("board_imbalance") is not None else None
        ),
        push_density_1m=int(data.get("push_density_1m") or 0),
        push_density_3m_avg=(
            float(data["push_density_3m_avg"])
            if data.get("push_density_3m_avg") is not None
            else None
        ),
        tier=str(data.get("tier") or "B"),
        breakout_trigger_level=float(data.get("breakout_trigger_level") or data["entry_price"]),
        session_high_at_entry=(
            float(data["session_high_at_entry"])
            if data.get("session_high_at_entry") is not None
            else None
        ),
        session_high_now=(
            float(data["session_high_now"]) if data.get("session_high_now") is not None else None
        ),
        imbalance_low_streak=int(data.get("imbalance_low_streak") or 0),
        max_price_since_entry=(
            float(data["max_price_since_entry"])
            if data.get("max_price_since_entry") is not None
            else None
        ),
    )
    return evaluate_kabu_exit_v1(inp, has_position=has_position, cfg=cfg)
