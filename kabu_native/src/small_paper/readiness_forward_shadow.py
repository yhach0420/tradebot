"""
Phase679: Readiness precision/economics forward shadow (no ENTRY block).

I_precision: live_feature incomplete AND entry_expectancy_score_v2 <= threshold
H_economics: live_feature incomplete AND readiness_bounce_from_recent_low_accept >= threshold
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from research.phase631_profit_source_attribution import _num

BIG_WINNER_YEN = 5000.0
EARLY_STOP_SEC = 300.0
DEFAULT_EXPECTANCY_MAX = 2.5
DEFAULT_BOUNCE_MIN = 0.45
DEFAULT_REFINED_MFE_MAX_PCT = 1.0
PRE_WINDOW_SEC = 120.0

ENTRY_FIELD_KEYS = (
    "readiness_precision_shadow_candidate",
    "readiness_precision_shadow_block",
    "readiness_economics_shadow_candidate",
    "readiness_economics_shadow_block",
    "readiness_shadow_union_block",
    "readiness_shadow_overlap_block",
    "readiness_bounce_from_recent_low_accept",
    "readiness_bounce_from_recent_low",
    "readiness_fall_from_recent_high",
    "readiness_slope_5min",
    "readiness_price_history_sec",
    "readiness_microsequence_ok",
    "readiness_price_history_insufficient",
    "readiness_same_symbol_entry_count_today",
    "mfe_pre_entry_pct",
    "mfe_pre_entry_source",
    "mfe_pre_entry_window_sec",
    "readiness_refined_h_shadow_candidate",
    "readiness_refined_h_shadow_block",
)

EXIT_EXTRA_FIELD_KEYS = (
    "actual_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen",
    "hold_sec",
    "is_stop_hit",
    "is_early_stop_300s",
    "is_winner",
    "is_big_winner",
    "readiness_precision_blocked_early_stop",
    "readiness_economics_blocked_early_stop",
    "readiness_refined_h_shadow_block",
    "readiness_refined_h_blocked_early_stop",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def readiness_precision_shadow_enabled(config: Any) -> bool:
    return bool(getattr(config, "readiness_precision_shadow_enabled", False))


def readiness_economics_shadow_enabled(config: Any) -> bool:
    return bool(getattr(config, "readiness_economics_shadow_enabled", False))


def readiness_refined_h_shadow_enabled(config: Any) -> bool:
    return bool(getattr(config, "readiness_refined_h_shadow_enabled", False))


def readiness_shadow_any_enabled(config: Any) -> bool:
    return (
        readiness_precision_shadow_enabled(config)
        or readiness_economics_shadow_enabled(config)
        or readiness_refined_h_shadow_enabled(config)
    )


def _live_feature_incomplete(trade: Mapping[str, Any], *, config: Any) -> bool:
    if not bool(getattr(config, "readiness_precision_shadow_require_live_incomplete", True)) and not bool(
        getattr(config, "readiness_economics_shadow_require_live_incomplete", True)
    ):
        return True
    return not bool(trade.get("live_feature_complete"))


def _bounce_from_price_ring(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    window_sec: float = PRE_WINDOW_SEC,
) -> Optional[float]:
    if entry_px <= 0:
        return None
    window = [(t, px) for t, px in price_ring if entry_ts - window_sec <= t <= entry_ts and px > 0]
    if len(window) < 2:
        return None
    recent_low = min(px for _, px in window)
    if recent_low <= 0:
        return None
    return round((entry_px - recent_low) / recent_low, 4)


def _price_history_sec(price_ring: Sequence[tuple[float, float]], *, entry_ts: float) -> Optional[float]:
    window = [t for t, px in price_ring if entry_ts - PRE_WINDOW_SEC <= t <= entry_ts and px > 0]
    if len(window) < 2:
        return round(max(0.0, entry_ts - min(window)), 2) if window else 0.0
    return round(max(0.0, entry_ts - min(window)), 2)


def _microsequence_ok_from_ring(price_ring: Sequence[tuple[float, float]], *, entry_ts: float) -> bool:
    pts = [px for t, px in price_ring if entry_ts - PRE_WINDOW_SEC <= t <= entry_ts and px > 0]
    return len(pts) >= 3


def _h_bounce_accept(trade: Mapping[str, Any]) -> Optional[float]:
    bounce = _float(trade.get("readiness_bounce_from_recent_low_accept"))
    if bounce is None:
        bounce = _float(trade.get("bounce_from_recent_low"))
    return bounce


def evaluate_readiness_precision(config: Any, trade: Mapping[str, Any]) -> bool:
    if not readiness_precision_shadow_enabled(config):
        return False
    if bool(getattr(config, "readiness_precision_shadow_require_live_incomplete", True)) and not _live_feature_incomplete(
        trade, config=config
    ):
        return False
    thr = float(getattr(config, "readiness_precision_shadow_expectancy_max", DEFAULT_EXPECTANCY_MAX))
    score = _float(trade.get("entry_expectancy_score_v2"))
    return score is not None and score <= thr


def evaluate_readiness_economics(config: Any, trade: Mapping[str, Any]) -> bool:
    if not readiness_economics_shadow_enabled(config):
        return False
    if bool(getattr(config, "readiness_economics_shadow_require_live_incomplete", True)) and not _live_feature_incomplete(
        trade, config=config
    ):
        return False
    thr = float(getattr(config, "readiness_economics_shadow_bounce_min", DEFAULT_BOUNCE_MIN))
    bounce = _h_bounce_accept(trade)
    return bounce is not None and bounce >= thr


def evaluate_baseline_h(config: Any, trade: Mapping[str, Any]) -> bool:
    if bool(getattr(config, "readiness_economics_shadow_require_live_incomplete", True)) and not _live_feature_incomplete(
        trade, config=config
    ):
        return False
    bounce_min = float(getattr(config, "readiness_economics_shadow_bounce_min", DEFAULT_BOUNCE_MIN))
    bounce = _h_bounce_accept(trade)
    return bounce is not None and bounce >= bounce_min


def evaluate_baseline_h_refined(config: Any, trade: Mapping[str, Any]) -> bool:
    bounce_min = float(getattr(config, "readiness_refined_h_bounce_min", DEFAULT_BOUNCE_MIN))
    if bool(getattr(config, "readiness_refined_h_require_live_incomplete", True)) and not _live_feature_incomplete(
        trade, config=config
    ):
        return False
    bounce = _h_bounce_accept(trade)
    return bounce is not None and bounce >= bounce_min


def evaluate_readiness_refined_h(config: Any, trade: Mapping[str, Any]) -> bool:
    if not readiness_refined_h_shadow_enabled(config):
        return False
    if not evaluate_baseline_h_refined(config, trade):
        return False
    mfe_max = float(getattr(config, "readiness_refined_h_pre_entry_mfe_max_pct", DEFAULT_REFINED_MFE_MAX_PCT))
    mfe = _float(trade.get("mfe_pre_entry_pct"))
    return mfe is not None and mfe < mfe_max


def compute_readiness_shadow_fields(
    config: Any,
    trade: Mapping[str, Any],
    *,
    price_ring: Optional[Sequence[tuple[float, float]]] = None,
    entry_ts: Optional[float] = None,
    same_symbol_entry_count_today: int = 1,
) -> dict[str, Any]:
    entry_px = _float(trade.get("current_price")) or _float(trade.get("CurrentPrice")) or 0.0
    ring = list(price_ring or [])
    hist_sec = _price_history_sec(ring, entry_ts=entry_ts) if entry_ts is not None else None
    ring_bounce = (
        _bounce_from_price_ring(ring, entry_ts=entry_ts, entry_px=entry_px)
        if entry_ts is not None and entry_px > 0
        else None
    )
    accept_bounce = _float(trade.get("bounce_from_recent_low"))
    micro_ok = _microsequence_ok_from_ring(ring, entry_ts=entry_ts) if entry_ts is not None else None
    from small_paper.mfe_pre_entry import compute_mfe_pre_entry_fields

    mfe_fields = (
        compute_mfe_pre_entry_fields(ring, entry_ts=entry_ts, entry_px=entry_px, window_sec=PRE_WINDOW_SEC)
        if entry_ts is not None and entry_px > 0
        else {
            "mfe_pre_entry_pct": None,
            "mfe_pre_entry_source": None,
            "mfe_pre_entry_window_sec": PRE_WINDOW_SEC,
        }
    )
    base = {
        "readiness_precision_shadow_candidate": False,
        "readiness_precision_shadow_block": False,
        "readiness_economics_shadow_candidate": False,
        "readiness_economics_shadow_block": False,
        "readiness_shadow_union_block": False,
        "readiness_shadow_overlap_block": False,
        "readiness_bounce_from_recent_low_accept": accept_bounce,
        "readiness_bounce_from_recent_low": ring_bounce,
        "readiness_fall_from_recent_high": trade.get("fall_from_recent_high"),
        "readiness_slope_5min": trade.get("slope_5min"),
        "readiness_price_history_sec": hist_sec,
        "readiness_microsequence_ok": micro_ok,
        "readiness_price_history_insufficient": bool(hist_sec is not None and hist_sec < PRE_WINDOW_SEC),
        "readiness_same_symbol_entry_count_today": same_symbol_entry_count_today,
        **mfe_fields,
        "readiness_refined_h_shadow_candidate": False,
        "readiness_refined_h_shadow_block": False,
    }
    if not readiness_shadow_any_enabled(config):
        return base
    trade_aug = {**trade, **base}
    prec = evaluate_readiness_precision(config, trade_aug) if readiness_precision_shadow_enabled(config) else False
    econ = evaluate_readiness_economics(config, trade_aug) if readiness_economics_shadow_enabled(config) else False
    refined = evaluate_readiness_refined_h(config, trade_aug) if readiness_refined_h_shadow_enabled(config) else False
    return {
        **base,
        "readiness_precision_shadow_candidate": readiness_precision_shadow_enabled(config),
        "readiness_precision_shadow_block": prec,
        "readiness_economics_shadow_candidate": readiness_economics_shadow_enabled(config),
        "readiness_economics_shadow_block": econ,
        "readiness_shadow_union_block": prec or econ,
        "readiness_shadow_overlap_block": prec and econ,
        "readiness_refined_h_shadow_candidate": readiness_refined_h_shadow_enabled(config),
        "readiness_refined_h_shadow_block": refined,
        "readiness_refined_h_shadow_research_only": bool(
            readiness_refined_h_shadow_enabled(config)
        ),
    }


def enrich_exit_readiness_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    hold_sec: Optional[float] = None,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    prec_block = _bool(entry_shadow.get("readiness_precision_shadow_block"))
    econ_block = _bool(entry_shadow.get("readiness_economics_shadow_block"))
    refined_block = _bool(entry_shadow.get("readiness_refined_h_shadow_block"))
    union_block = _bool(entry_shadow.get("readiness_shadow_union_block"))
    actual_yen = round(compute_pnl_yen_100(entry_price, exit_price), 2)
    shadow_yen = 0.0 if union_block else actual_yen
    stop_hit = exit_reason == "stop_hit"
    hs = _float(hold_sec)
    early = bool(stop_hit and hs is not None and hs <= EARLY_STOP_SEC)
    return {
        "readiness_precision_shadow_block": prec_block,
        "readiness_economics_shadow_block": econ_block,
        "readiness_refined_h_shadow_block": refined_block,
        "readiness_shadow_union_block": union_block,
        "readiness_shadow_overlap_block": _bool(entry_shadow.get("readiness_shadow_overlap_block")),
        "mfe_pre_entry_pct": entry_shadow.get("mfe_pre_entry_pct"),
        "mfe_pre_entry_source": entry_shadow.get("mfe_pre_entry_source"),
        "actual_pnl_yen_100": actual_yen,
        "shadow_pnl_yen_100": shadow_yen,
        "delta_yen": round(shadow_yen - actual_yen, 2),
        "hold_sec": hs,
        "is_stop_hit": stop_hit,
        "is_early_stop_300s": early,
        "is_winner": actual_yen > 0,
        "is_big_winner": actual_yen >= BIG_WINNER_YEN,
        "readiness_precision_blocked_early_stop": bool(prec_block and early),
        "readiness_economics_blocked_early_stop": bool(econ_block and early),
        "readiness_refined_h_blocked_early_stop": bool(refined_block and early),
        "blocked_winner": bool(union_block and actual_yen > 0),
        "blocked_loser": bool(union_block and actual_yen < 0),
        "blocked_big_winner": bool(union_block and actual_yen >= BIG_WINNER_YEN),
        "refined_h_blocked_winner": bool(refined_block and actual_yen > 0),
        "refined_h_blocked_loser": bool(refined_block and actual_yen < 0),
        "refined_h_blocked_big_winner": bool(refined_block and actual_yen >= BIG_WINNER_YEN),
    }


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else 999.0
    return round(gp / gl, 4)


@dataclass
class _LaneStats:
    block_count: int = 0
    delta_yen: float = 0.0
    blocked_early_stop: int = 0
    blocked_stop_hit: int = 0
    blocked_winners: int = 0
    blocked_big_winners: int = 0
    lost_profit_yen: float = 0.0
    avoided_loss_yen: float = 0.0
    _yens: list[float] = field(default_factory=list)
    _shadow_yens: list[float] = field(default_factory=list)

    def record(self, *, blocked: bool, actual_yen: float, shadow_yen: float, early: bool, stop_hit: bool) -> None:
        if blocked:
            self.block_count += 1
            self.delta_yen = round(self.delta_yen + (shadow_yen - actual_yen), 2)
            if early:
                self.blocked_early_stop += 1
            if stop_hit:
                self.blocked_stop_hit += 1
            if actual_yen > 0:
                self.blocked_winners += 1
                self.lost_profit_yen = round(self.lost_profit_yen + actual_yen, 2)
            elif actual_yen < 0:
                self.avoided_loss_yen = round(self.avoided_loss_yen + abs(actual_yen), 2)
            if actual_yen >= BIG_WINNER_YEN:
                self.blocked_big_winners += 1
        self._yens.append(actual_yen)
        self._shadow_yens.append(shadow_yen)

    def net_delta_yen(self) -> float:
        return round(self.avoided_loss_yen - self.lost_profit_yen, 2)

    def pf_delta(self) -> Optional[float]:
        ap = _pf(self._yens)
        sp = _pf(self._shadow_yens)
        if ap is None or sp is None:
            return None
        return round(float(sp) - float(ap), 4)


@dataclass
class ReadinessForwardShadowCounters:
    readiness_shadow_target_count: int = 0
    precision_enabled: bool = False
    economics_enabled: bool = False
    refined_h_enabled: bool = False
    readiness_precision: _LaneStats = field(default_factory=_LaneStats)
    readiness_economics: _LaneStats = field(default_factory=_LaneStats)
    readiness_refined_h: _LaneStats = field(default_factory=_LaneStats)
    readiness_union: _LaneStats = field(default_factory=_LaneStats)
    readiness_overlap: _LaneStats = field(default_factory=_LaneStats)

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if not (
            _bool(fields.get("readiness_precision_shadow_candidate"))
            or _bool(fields.get("readiness_economics_shadow_candidate"))
            or _bool(fields.get("readiness_refined_h_shadow_candidate"))
        ):
            return
        self.readiness_shadow_target_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        if not (
            _bool(row.get("readiness_precision_shadow_candidate"))
            or _bool(row.get("readiness_economics_shadow_candidate"))
            or _bool(row.get("readiness_refined_h_shadow_candidate"))
        ):
            return
        actual = _float(row.get("actual_pnl_yen_100")) or 0.0
        shadow = _float(row.get("shadow_pnl_yen_100")) or 0.0
        early = _bool(row.get("is_early_stop_300s"))
        stop_hit = _bool(row.get("is_stop_hit"))
        prec = _bool(row.get("readiness_precision_shadow_block"))
        econ = _bool(row.get("readiness_economics_shadow_block"))
        refined = _bool(row.get("readiness_refined_h_shadow_block"))
        union = _bool(row.get("readiness_shadow_union_block"))
        overlap = _bool(row.get("readiness_shadow_overlap_block"))
        self.readiness_precision.record(
            blocked=prec, actual_yen=actual, shadow_yen=0.0 if prec else actual, early=early, stop_hit=stop_hit
        )
        self.readiness_economics.record(
            blocked=econ, actual_yen=actual, shadow_yen=0.0 if econ else actual, early=early, stop_hit=stop_hit
        )
        self.readiness_refined_h.record(
            blocked=refined,
            actual_yen=actual,
            shadow_yen=0.0 if refined else actual,
            early=early,
            stop_hit=stop_hit,
        )
        self.readiness_union.record(blocked=union, actual_yen=actual, shadow_yen=shadow, early=early, stop_hit=stop_hit)
        self.readiness_overlap.record(
            blocked=overlap, actual_yen=actual, shadow_yen=0.0 if overlap else actual, early=early, stop_hit=stop_hit
        )

    def summary_fields(self) -> dict[str, Any]:
        p, e, r, u, o = (
            self.readiness_precision,
            self.readiness_economics,
            self.readiness_refined_h,
            self.readiness_union,
            self.readiness_overlap,
        )
        return {
            "readiness_precision_shadow_enabled": self.precision_enabled,
            "readiness_economics_shadow_enabled": self.economics_enabled,
            "readiness_refined_h_shadow_enabled": self.refined_h_enabled,
            "readiness_shadow_target_count": self.readiness_shadow_target_count,
            "readiness_precision_block_count": p.block_count,
            "readiness_precision_delta_yen": p.delta_yen,
            "readiness_precision_blocked_early_stop": p.blocked_early_stop,
            "readiness_precision_blocked_winners": p.blocked_winners,
            "readiness_precision_blocked_big_winners": p.blocked_big_winners,
            "readiness_precision_delta_pf": p.pf_delta(),
            "readiness_economics_block_count": e.block_count,
            "readiness_economics_delta_yen": e.delta_yen,
            "readiness_economics_blocked_early_stop": e.blocked_early_stop,
            "readiness_economics_blocked_winners": e.blocked_winners,
            "readiness_economics_blocked_big_winners": e.blocked_big_winners,
            "readiness_economics_delta_pf": e.pf_delta(),
            "refined_h_shadow_block_count": r.block_count,
            "refined_h_shadow_delta_yen": r.delta_yen,
            "refined_h_shadow_blocked_early_stop": r.blocked_early_stop,
            "refined_h_shadow_blocked_stop_hit": r.blocked_stop_hit,
            "refined_h_shadow_blocked_winners": r.blocked_winners,
            "refined_h_shadow_blocked_big_winners": r.blocked_big_winners,
            "refined_h_shadow_lost_profit_yen": r.lost_profit_yen,
            "refined_h_shadow_avoided_loss_yen": r.avoided_loss_yen,
            "refined_h_shadow_net_delta_yen": r.net_delta_yen(),
            "readiness_union_block_count": u.block_count,
            "readiness_union_delta_yen": u.delta_yen,
            "readiness_union_blocked_early_stop": u.blocked_early_stop,
            "readiness_union_blocked_winners": u.blocked_winners,
            "readiness_overlap_block_count": o.block_count,
            "readiness_overlap_delta_yen": o.delta_yen,
            "readiness_overlap_blocked_early_stop": o.blocked_early_stop,
        }


def build_readiness_forward_shadow_counters(config: Any) -> Optional[ReadinessForwardShadowCounters]:
    if not readiness_shadow_any_enabled(config):
        return None
    return ReadinessForwardShadowCounters(
        precision_enabled=readiness_precision_shadow_enabled(config),
        economics_enabled=readiness_economics_shadow_enabled(config),
        refined_h_enabled=readiness_refined_h_shadow_enabled(config),
    )


def format_readiness_shadow_discord_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary.get("readiness_precision_shadow_enabled") and not summary.get(
        "readiness_economics_shadow_enabled"
    ) and not summary.get("readiness_refined_h_shadow_enabled"):
        return []
    lines = [
        "Readiness Shadow:",
        (
            f"I block={summary.get('readiness_precision_block_count', 0)} "
            f"Δ={summary.get('readiness_precision_delta_yen', 0)} "
            f"ES={summary.get('readiness_precision_blocked_early_stop', 0)}"
        ),
        (
            f"H block={summary.get('readiness_economics_block_count', 0)} "
            f"Δ={summary.get('readiness_economics_delta_yen', 0)} "
            f"ES={summary.get('readiness_economics_blocked_early_stop', 0)}"
        ),
    ]
    if summary.get("readiness_refined_h_shadow_enabled"):
        lines.append(
            (
                f"refined_H block={summary.get('refined_h_shadow_block_count', 0)} "
                f"Δ={summary.get('refined_h_shadow_delta_yen', 0)} "
                f"net={summary.get('refined_h_shadow_net_delta_yen', 0)} "
                f"BW={summary.get('refined_h_shadow_blocked_big_winners', 0)}"
            )
        )
    lines.append(
        (
            f"union={summary.get('readiness_union_block_count', 0)} "
            f"Δ={summary.get('readiness_union_delta_yen', 0)} "
            f"overlap={summary.get('readiness_overlap_block_count', 0)}"
        )
    )
    return lines
