"""
ENTRY v2 prototype strategies for Logic Lab (Phase 23–24).

Scores G3/G5/G6 as components (no simple gate removal). Candidate vs entry thresholds
are fixed per profile — no per-symbol/day/time tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from research.g3_diagnostic import g3_classify, g5_classify, g6_classify
from src.kabu_signal_engine import (
    HIGH_PRICE_PROXIMITY_MIN,
    SPREAD_BPS_MAX,
    VWAP_DISTANCE_PCT_MIN,
)

ENTRY_V2_PHASE23_PROFILE_NAMES: tuple[str, ...] = (
    "continuation_score_v2",
    "pullback_vwap_v1",
    "breakout_confirm_v2",
    "momentum_volume_v1",
    "candidate_only_v1",
)

ENTRY_V2_PHASE24_PROFILE_NAMES: tuple[str, ...] = (
    "pullback_vwap_v2",
    "momentum_volume_v2",
    "hybrid_vwap_momentum_v1",
)

ENTRY_V2_PHASE25_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v3_entry_guard",
    "momentum_volume_v3_exit_guard",
    "momentum_volume_v3_take_guard",
    "momentum_volume_v3_combined",
)

ENTRY_V2_PHASE26_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v4_early_guard",
    "momentum_volume_v4_recovery_guard",
    "momentum_volume_v4_imbalance_confirm",
    "momentum_volume_v4_combined",
)

ENTRY_V2_PHASE27_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v5_recovery_exit",
    "momentum_volume_v5_delayed_imbalance_exit",
    "momentum_volume_v5_recovery_or_cut",
    "momentum_volume_v5_combined",
)

ENTRY_V2_PHASE28_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v6_noise_tolerant",
    "momentum_volume_v6_structure_break",
    "momentum_volume_v6_recovery_bias",
    "momentum_volume_v6_combined",
)

ENTRY_V2_PHASE29_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v7_delayed_imb",
    "momentum_volume_v7_recovery_check",
    "momentum_volume_v7_structure_break",
    "momentum_volume_v7_combined",
)

ENTRY_V2_PHASE30_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v8_reclaim_persistence",
    "momentum_volume_v8_favorable_persistence",
    "momentum_volume_v8_delayed_imb_refined",
    "momentum_volume_v8_structure_break_refined",
    "momentum_volume_v8_combined",
)

ENTRY_V2_PHASE31_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v9_state_persistence",
    "momentum_volume_v9_structure_break_state",
    "momentum_volume_v9_recovery_state",
    "momentum_volume_v9_combined",
)

ENTRY_V2_PHASE32_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v10_transition_persistence",
    "momentum_volume_v10_recovery_transition",
    "momentum_volume_v10_structure_transition",
    "momentum_volume_v10_combined",
)

ENTRY_V2_PHASE33_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v11_bullish_duration",
    "momentum_volume_v11_bearish_duration",
    "momentum_volume_v11_decay_detection",
    "momentum_volume_v11_combined",
)

ENTRY_V2_PHASE34_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v12_bullish_continuation",
    "momentum_volume_v12_decay_exit",
    "momentum_volume_v12_bearish_accumulation",
    "momentum_volume_v12_combined",
)

ENTRY_V2_PHASE35_PROFILE_NAMES: tuple[str, ...] = (
    "momentum_volume_v13_momentum_priority",
    "momentum_volume_v13_decay_exit",
    "momentum_volume_v13_bearish_accumulation",
    "momentum_volume_v13_combined",
)

MOMENTUM_V2_REFERENCE = "momentum_volume_v2"
MOMENTUM_V10_COMBINED_REFERENCE = "momentum_volume_v10_combined"
MOMENTUM_V11_COMBINED_REFERENCE = "momentum_volume_v11_combined"
MOMENTUM_V12_COMBINED_REFERENCE = "momentum_volume_v12_combined"
MOMENTUM_V13_COMBINED_REFERENCE = "momentum_volume_v13_combined"
MOMENTUM_V8_COMBINED_REFERENCE = "momentum_volume_v8_combined"
MOMENTUM_V9_COMBINED_REFERENCE = "momentum_volume_v9_combined"
MOMENTUM_V5_COMBINED_REFERENCE = "momentum_volume_v5_combined"
MOMENTUM_V6_COMBINED_REFERENCE = "momentum_volume_v6_combined"
MOMENTUM_V7_COMBINED_REFERENCE = "momentum_volume_v7_combined"

ENTRY_V2_PROFILE_NAMES: tuple[str, ...] = (
    ENTRY_V2_PHASE23_PROFILE_NAMES
    + ENTRY_V2_PHASE24_PROFILE_NAMES
    + ENTRY_V2_PHASE25_PROFILE_NAMES
    + ENTRY_V2_PHASE26_PROFILE_NAMES
    + ENTRY_V2_PHASE27_PROFILE_NAMES
    + ENTRY_V2_PHASE28_PROFILE_NAMES
    + ENTRY_V2_PHASE29_PROFILE_NAMES
    + ENTRY_V2_PHASE30_PROFILE_NAMES
    + ENTRY_V2_PHASE31_PROFILE_NAMES
    + ENTRY_V2_PHASE32_PROFILE_NAMES
    + ENTRY_V2_PHASE33_PROFILE_NAMES
    + ENTRY_V2_PHASE34_PROFILE_NAMES
    + ENTRY_V2_PHASE35_PROFILE_NAMES
)

ENTRY_V2_PHASE35_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V12_COMBINED_REFERENCE,
) + ENTRY_V2_PHASE35_PROFILE_NAMES

ENTRY_V2_PHASE34_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V11_COMBINED_REFERENCE,
) + ENTRY_V2_PHASE34_PROFILE_NAMES

ENTRY_V2_PHASE33_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V10_COMBINED_REFERENCE,
) + ENTRY_V2_PHASE33_PROFILE_NAMES

ENTRY_V2_PHASE32_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V9_COMBINED_REFERENCE,
) + ENTRY_V2_PHASE32_PROFILE_NAMES

ENTRY_V2_PHASE31_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V8_COMBINED_REFERENCE,
) + ENTRY_V2_PHASE31_PROFILE_NAMES

ENTRY_V2_PHASE30_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V5_COMBINED_REFERENCE,
    MOMENTUM_V6_COMBINED_REFERENCE,
    MOMENTUM_V7_COMBINED_REFERENCE,
) + ENTRY_V2_PHASE30_PROFILE_NAMES

ENTRY_V2_PHASE29_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V5_COMBINED_REFERENCE,
    MOMENTUM_V6_COMBINED_REFERENCE,
) + ENTRY_V2_PHASE29_PROFILE_NAMES

ENTRY_V2_PHASE28_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
) + ENTRY_V2_PHASE28_PROFILE_NAMES

ENTRY_V2_PHASE27_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
) + ENTRY_V2_PHASE27_PROFILE_NAMES

ENTRY_V2_PHASE26_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
) + ENTRY_V2_PHASE26_PROFILE_NAMES

ENTRY_V2_PHASE25_PROFILES: tuple[str, ...] = (
    MOMENTUM_V2_REFERENCE,
) + ENTRY_V2_PHASE25_PROFILE_NAMES

ENTRY_V2_PHASE24_PROFILES: tuple[str, ...] = (
    "pullback_vwap_v1",
    "momentum_volume_v1",
) + ENTRY_V2_PHASE24_PROFILE_NAMES

ENTRY_V2_DEEP_DIVE_PROFILES: tuple[str, ...] = (
    "pullback_vwap_v1",
    "momentum_volume_v1",
)

ENTRY_V2_REFERENCE_PROFILE: dict[str, str] = {
    "pullback_vwap_v1": "pullback_vwap_v1",
    "pullback_vwap_v2": "pullback_vwap_v1",
    "momentum_volume_v1": "momentum_volume_v1",
    "momentum_volume_v2": "momentum_volume_v1",
    "hybrid_vwap_momentum_v1": "pullback_vwap_v1",
}

ENTRY_V2_COMPARISON_PROFILES: tuple[str, ...] = ("baseline",) + ENTRY_V2_PROFILE_NAMES

# Fixed thresholds (global, not optimized per symbol)
CANDIDATE_THRESHOLDS: dict[str, float] = {
    "continuation_score_v2": 48.0,
    "pullback_vwap_v1": 45.0,
    "pullback_vwap_v2": 48.0,
    "breakout_confirm_v2": 45.0,
    "momentum_volume_v1": 46.0,
    "momentum_volume_v2": 48.0,
    "momentum_volume_v3_entry_guard": 48.0,
    "momentum_volume_v3_exit_guard": 48.0,
    "momentum_volume_v3_take_guard": 48.0,
    "momentum_volume_v3_combined": 48.0,
    "momentum_volume_v4_early_guard": 48.0,
    "momentum_volume_v4_recovery_guard": 48.0,
    "momentum_volume_v4_imbalance_confirm": 48.0,
    "momentum_volume_v4_combined": 48.0,
    "momentum_volume_v5_recovery_exit": 48.0,
    "momentum_volume_v5_delayed_imbalance_exit": 48.0,
    "momentum_volume_v5_recovery_or_cut": 48.0,
    "momentum_volume_v5_combined": 48.0,
    "momentum_volume_v6_noise_tolerant": 48.0,
    "momentum_volume_v6_structure_break": 48.0,
    "momentum_volume_v6_recovery_bias": 48.0,
    "momentum_volume_v6_combined": 48.0,
    "momentum_volume_v7_delayed_imb": 48.0,
    "momentum_volume_v7_recovery_check": 48.0,
    "momentum_volume_v7_structure_break": 48.0,
    "momentum_volume_v7_combined": 48.0,
    "momentum_volume_v8_reclaim_persistence": 48.0,
    "momentum_volume_v8_favorable_persistence": 48.0,
    "momentum_volume_v8_delayed_imb_refined": 48.0,
    "momentum_volume_v8_structure_break_refined": 48.0,
    "momentum_volume_v8_combined": 48.0,
    "momentum_volume_v9_state_persistence": 48.0,
    "momentum_volume_v9_structure_break_state": 48.0,
    "momentum_volume_v9_recovery_state": 48.0,
    "momentum_volume_v9_combined": 48.0,
    "momentum_volume_v10_transition_persistence": 48.0,
    "momentum_volume_v10_recovery_transition": 48.0,
    "momentum_volume_v10_structure_transition": 48.0,
    "momentum_volume_v10_combined": 48.0,
    "momentum_volume_v11_bullish_duration": 48.0,
    "momentum_volume_v11_bearish_duration": 48.0,
    "momentum_volume_v11_decay_detection": 48.0,
    "momentum_volume_v11_combined": 48.0,
    "momentum_volume_v12_bullish_continuation": 48.0,
    "momentum_volume_v12_decay_exit": 48.0,
    "momentum_volume_v12_bearish_accumulation": 48.0,
    "momentum_volume_v12_combined": 48.0,
    "momentum_volume_v13_momentum_priority": 48.0,
    "momentum_volume_v13_decay_exit": 48.0,
    "momentum_volume_v13_bearish_accumulation": 48.0,
    "momentum_volume_v13_combined": 48.0,
    "candidate_only_v1": 50.0,
    "hybrid_vwap_momentum_v1": 50.0,
}
ENTRY_THRESHOLDS: dict[str, float] = {
    "continuation_score_v2": 62.0,
    "pullback_vwap_v1": 58.0,
    "pullback_vwap_v2": 62.0,
    "breakout_confirm_v2": 55.0,
    "momentum_volume_v1": 60.0,
    "momentum_volume_v2": 64.0,
    "momentum_volume_v3_entry_guard": 64.0,
    "momentum_volume_v3_exit_guard": 64.0,
    "momentum_volume_v3_take_guard": 64.0,
    "momentum_volume_v3_combined": 64.0,
    "momentum_volume_v4_early_guard": 64.0,
    "momentum_volume_v4_recovery_guard": 64.0,
    "momentum_volume_v4_imbalance_confirm": 64.0,
    "momentum_volume_v4_combined": 64.0,
    "momentum_volume_v5_recovery_exit": 64.0,
    "momentum_volume_v5_delayed_imbalance_exit": 64.0,
    "momentum_volume_v5_recovery_or_cut": 64.0,
    "momentum_volume_v5_combined": 64.0,
    "momentum_volume_v6_noise_tolerant": 64.0,
    "momentum_volume_v6_structure_break": 64.0,
    "momentum_volume_v6_recovery_bias": 64.0,
    "momentum_volume_v6_combined": 64.0,
    "momentum_volume_v7_delayed_imb": 64.0,
    "momentum_volume_v7_recovery_check": 64.0,
    "momentum_volume_v7_structure_break": 64.0,
    "momentum_volume_v7_combined": 64.0,
    "momentum_volume_v8_reclaim_persistence": 64.0,
    "momentum_volume_v8_favorable_persistence": 64.0,
    "momentum_volume_v8_delayed_imb_refined": 64.0,
    "momentum_volume_v8_structure_break_refined": 64.0,
    "momentum_volume_v8_combined": 64.0,
    "momentum_volume_v9_state_persistence": 64.0,
    "momentum_volume_v9_structure_break_state": 64.0,
    "momentum_volume_v9_recovery_state": 64.0,
    "momentum_volume_v9_combined": 64.0,
    "momentum_volume_v10_transition_persistence": 64.0,
    "momentum_volume_v10_recovery_transition": 64.0,
    "momentum_volume_v10_structure_transition": 64.0,
    "momentum_volume_v10_combined": 64.0,
    "momentum_volume_v11_bullish_duration": 64.0,
    "momentum_volume_v11_bearish_duration": 64.0,
    "momentum_volume_v11_decay_detection": 64.0,
    "momentum_volume_v11_combined": 64.0,
    "momentum_volume_v12_bullish_continuation": 64.0,
    "momentum_volume_v12_decay_exit": 64.0,
    "momentum_volume_v12_bearish_accumulation": 64.0,
    "momentum_volume_v12_combined": 64.0,
    "momentum_volume_v13_momentum_priority": 64.0,
    "momentum_volume_v13_decay_exit": 64.0,
    "momentum_volume_v13_bearish_accumulation": 64.0,
    "momentum_volume_v13_combined": 64.0,
    "candidate_only_v1": 72.0,
    "hybrid_vwap_momentum_v1": 65.0,
}

PULLBACK_PROFILES = frozenset({"pullback_vwap_v1", "pullback_vwap_v2", "hybrid_vwap_momentum_v1"})
MOMENTUM_PROFILES = frozenset(
    {
        "momentum_volume_v1",
        "momentum_volume_v2",
        "hybrid_vwap_momentum_v1",
        "momentum_volume_v3_entry_guard",
        "momentum_volume_v3_exit_guard",
        "momentum_volume_v3_take_guard",
        "momentum_volume_v3_combined",
        "momentum_volume_v4_early_guard",
        "momentum_volume_v4_recovery_guard",
        "momentum_volume_v4_imbalance_confirm",
        "momentum_volume_v4_combined",
        "momentum_volume_v5_recovery_exit",
        "momentum_volume_v5_delayed_imbalance_exit",
        "momentum_volume_v5_recovery_or_cut",
        "momentum_volume_v5_combined",
        "momentum_volume_v6_noise_tolerant",
        "momentum_volume_v6_structure_break",
        "momentum_volume_v6_recovery_bias",
        "momentum_volume_v6_combined",
        "momentum_volume_v7_delayed_imb",
        "momentum_volume_v7_recovery_check",
        "momentum_volume_v7_structure_break",
        "momentum_volume_v7_combined",
        "momentum_volume_v8_reclaim_persistence",
        "momentum_volume_v8_favorable_persistence",
        "momentum_volume_v8_delayed_imb_refined",
        "momentum_volume_v8_structure_break_refined",
        "momentum_volume_v8_combined",
        "momentum_volume_v9_state_persistence",
        "momentum_volume_v9_structure_break_state",
        "momentum_volume_v9_recovery_state",
        "momentum_volume_v9_combined",
        "momentum_volume_v10_transition_persistence",
        "momentum_volume_v10_recovery_transition",
        "momentum_volume_v10_structure_transition",
        "momentum_volume_v10_combined",
        "momentum_volume_v11_bullish_duration",
        "momentum_volume_v11_bearish_duration",
        "momentum_volume_v11_decay_detection",
        "momentum_volume_v11_combined",
        "momentum_volume_v12_bullish_continuation",
        "momentum_volume_v12_decay_exit",
        "momentum_volume_v12_bearish_accumulation",
        "momentum_volume_v12_combined",
        "momentum_volume_v13_momentum_priority",
        "momentum_volume_v13_decay_exit",
        "momentum_volume_v13_bearish_accumulation",
        "momentum_volume_v13_combined",
    }
)
MOMENTUM_V3_ENTRY_GUARD_PROFILES = frozenset(
    {"momentum_volume_v3_entry_guard", "momentum_volume_v3_combined"}
)

# Global entry guards (market structure — not symbol/day/time specific)
ENTRY_V3_MAX_MOMENTUM_PCT = 0.35
ENTRY_V3_MAX_VWAP_DIST_PCT = 1.20
ENTRY_V3_MIN_BOARD_IMBALANCE = 0.50
ENTRY_V3_VOL_WITHOUT_PRICE_VOL_MIN = 0.15
ENTRY_V3_VOL_WITHOUT_PRICE_MOM_MAX = 0.08


def is_entry_v2_profile(name: str) -> bool:
    return name in ENTRY_V2_PROFILE_NAMES


def is_entry_v2_phase24_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE24_PROFILES


def is_entry_v2_phase25_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE25_PROFILES


def is_entry_v2_phase26_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE26_PROFILES


def is_entry_v2_phase27_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE27_PROFILES


def is_entry_v2_phase28_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE28_PROFILES


def is_entry_v2_phase29_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE29_PROFILES


def is_entry_v2_phase30_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE30_PROFILES


def is_entry_v2_phase31_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE31_PROFILES


def is_entry_v2_phase32_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE32_PROFILES


def is_entry_v2_phase33_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE33_PROFILES


def is_entry_v2_phase34_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE34_PROFILES


def is_entry_v2_phase35_profile(name: str) -> bool:
    return name in ENTRY_V2_PHASE35_PROFILES


def uses_momentum_v6_cooldown(name: str) -> bool:
    return name.startswith("momentum_volume_v6_")


def is_momentum_enriched_profile(name: str) -> bool:
    return (
        name == MOMENTUM_V2_REFERENCE
        or name in ENTRY_V2_PHASE25_PROFILE_NAMES
        or name in ENTRY_V2_PHASE26_PROFILE_NAMES
        or name in ENTRY_V2_PHASE27_PROFILE_NAMES
        or name in ENTRY_V2_PHASE28_PROFILE_NAMES
        or name in ENTRY_V2_PHASE29_PROFILE_NAMES
        or name in ENTRY_V2_PHASE30_PROFILE_NAMES
        or name in ENTRY_V2_PHASE31_PROFILE_NAMES
        or name in ENTRY_V2_PHASE32_PROFILE_NAMES
        or name in ENTRY_V2_PHASE33_PROFILE_NAMES
        or name in ENTRY_V2_PHASE34_PROFILE_NAMES
        or name in ENTRY_V2_PHASE35_PROFILE_NAMES
    )


def _f(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _board_f(board: Mapping[str, Any], key: str) -> Optional[float]:
    v = board.get(key)
    return _f(v)


def _minute_key(ts: datetime) -> datetime:
    t = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return t.replace(second=0, microsecond=0)


def _liquidity_ok(rd: Mapping[str, Any], rejects: list[str]) -> bool:
    rs = {str(r) for r in rejects}
    if "G7_TRADING_VALUE" in rs or "G7_TRADING_VALUE_UNKNOWN" in rs:
        return False
    if "G2_SPREAD" in rs or "G2_SPREAD_UNKNOWN" in rs:
        return False
    if "G1_FRESHNESS" in rs:
        return False
    sp = _f(rd.get("spread_bps"))
    if sp is not None and sp > SPREAD_BPS_MAX:
        return False
    return True


def gate_component_scores(rd: Mapping[str, Any], rejects: list[str]) -> dict[str, float]:
    """Partial credit for G3/G5/G6 (0..1 each), not binary removal."""
    vd = _f(rd.get("vwap_distance_pct"))
    g3 = 0.0
    if vd is not None and vd >= 0:
        g3 = min(1.0, vd / max(VWAP_DISTANCE_PCT_MIN, 0.01))

    hp = _f(rd.get("high_proximity_ratio"))
    g5 = 0.0
    if hp is not None:
        g5 = min(1.0, max(0.0, (hp - HIGH_PRICE_PROXIMITY_MIN) / 0.02))

    g6 = 0.0
    vol_d = _f(rd.get("volume_delta_30s"))
    if vol_d is not None and vol_d > 0:
        g6 = min(1.0, vol_d / 50_000.0)

    if g3_classify(rejects) == "reject":
        g3 *= 0.35
    if g5_classify(rejects) == "reject":
        g5 *= 0.35
    if g6_classify(rejects) == "reject":
        g6 *= 0.35

    return {"g3": g3, "g5": g5, "g6": g6}


def continuation_composite(rd: Mapping[str, Any], rejects: list[str], ctx: "EntryV2Context") -> float:
    gates = gate_component_scores(rd, rejects)
    vd = _f(rd.get("vwap_distance_pct")) or 0.0
    hp = _f(rd.get("high_proximity_ratio")) or 0.0
    mtv = _f(ctx.minute_trading_value) or 0.0
    mtv_score = min(1.0, mtv / 80_000_000.0) if mtv > 0 else 0.0
    high_streak = min(1.0, ctx.recent_high_ticks / 8.0)
    base = (
        18.0 * min(1.0, vd / 0.5)
        + 16.0 * min(1.0, max(0.0, (hp - 0.98) / 0.02))
        + 12.0 * mtv_score
        + 14.0 * high_streak
        + 12.0 * gates["g3"]
        + 14.0 * gates["g5"]
        + 14.0 * gates["g6"]
    )
    if bool(rd.get("breakout_event")):
        base += 8.0
    return base


@dataclass
class EntryV2Context:
    profile: str
    candidate_threshold: float
    entry_threshold: float
    last_price: Optional[float] = None
    last_ts: Optional[datetime] = None
    minute_trading_value: Optional[float] = None
    prev_minute_tv: Optional[float] = None
    recent_high_ticks: int = 0
    session_high_seen: float = 0.0
    local_peak: float = 0.0
    pullback_active: bool = False
    rising_streak: int = 0
    above_trigger_streak: int = 0
    last_trigger: Optional[float] = None
    price_30s_ago: Optional[float] = None
    last_entry_v2_score: float = 0.0
    last_reject_top: str = ""
    vwap_below_streak: int = 0
    spread_bps_last: Optional[float] = None
    spread_bps_session_min: float = 9999.0
    recent_prices: list[float] = field(default_factory=list)
    _last_minute: Optional[datetime] = None

    @classmethod
    def create(cls, profile: str) -> "EntryV2Context":
        return cls(
            profile=profile,
            candidate_threshold=CANDIDATE_THRESHOLDS.get(profile, 50.0),
            entry_threshold=ENTRY_THRESHOLDS.get(profile, 65.0),
        )

    def _momentum_scoring_profile(self) -> str:
        if self.profile.startswith("momentum_volume_v3") or self.profile.startswith(
            "momentum_volume_v4"
        ) or self.profile.startswith("momentum_volume_v5") or self.profile.startswith(
            "momentum_volume_v6"
        ) or self.profile.startswith("momentum_volume_v7") or self.profile.startswith(
            "momentum_volume_v8"
        ) or self.profile.startswith("momentum_volume_v9") or self.profile.startswith(
            "momentum_volume_v10"
        ) or self.profile.startswith("momentum_volume_v11") or self.profile.startswith(
            "momentum_volume_v12"
        ) or self.profile.startswith("momentum_volume_v13"):
            return "momentum_volume_v2"
        return self.profile

    def entry_v3_guard_blocks(self, rd: Mapping[str, Any]) -> bool:
        """Common ENTRY filters for MAE-prone structural conditions."""
        mom = self.price_momentum_pct()
        vol = self.volume_increase_ratio()
        vd = _f(rd.get("vwap_distance_pct"))
        imb = _f(rd.get("board_imbalance"))
        sp = self.spread_bps_last or _f(rd.get("spread_bps"))
        if mom > ENTRY_V3_MAX_MOMENTUM_PCT:
            return True
        if vd is not None and vd > ENTRY_V3_MAX_VWAP_DIST_PCT:
            return True
        if imb is not None and imb < ENTRY_V3_MIN_BOARD_IMBALANCE:
            return True
        if vol >= ENTRY_V3_VOL_WITHOUT_PRICE_VOL_MIN and mom < ENTRY_V3_VOL_WITHOUT_PRICE_MOM_MAX:
            return True
        if sp is not None and sp > SPREAD_BPS_MAX * 0.75:
            return True
        if self.spread_worsening():
            return True
        return False

    def price_momentum_pct(self) -> float:
        if self.price_30s_ago and self.price_30s_ago > 0 and self.last_price:
            return (self.last_price - self.price_30s_ago) / self.price_30s_ago * 100.0
        return 0.0

    def volume_increase_ratio(self) -> float:
        if (
            self.minute_trading_value
            and self.prev_minute_tv
            and self.prev_minute_tv > 0
        ):
            return (self.minute_trading_value - self.prev_minute_tv) / self.prev_minute_tv
        return 0.0

    def pullback_depth_pct(self) -> float:
        if self.local_peak > 0 and self.last_price:
            return (self.local_peak - self.last_price) / self.local_peak * 100.0
        return 0.0

    def spread_worsening(self) -> bool:
        sp = self.spread_bps_last
        if sp is None:
            return False
        if self.spread_bps_session_min < 9000 and sp > self.spread_bps_session_min * 1.3:
            return True
        return sp > SPREAD_BPS_MAX * 0.85

    def immediate_reversal(self) -> bool:
        if len(self.recent_prices) < 3:
            return False
        a, b, c = self.recent_prices[-3:]
        return c < b < a

    def update(
        self,
        *,
        ts: datetime,
        rd: Mapping[str, Any],
        board: Mapping[str, Any],
        rejects: list[str],
        csv_meta: Optional[Mapping[str, float]],
    ) -> None:
        px = _f(rd.get("current_price"))
        if px is None:
            return
        sh = _f(rd.get("high_price")) or px
        if sh > self.session_high_seen:
            self.session_high_seen = sh
            self.recent_high_ticks += 1
        else:
            self.recent_high_ticks = max(0, self.recent_high_ticks - 1)

        if csv_meta:
            mtv = _f(csv_meta.get("bar_trading_value"))
            if mtv is not None:
                if self.minute_trading_value is not None and _minute_key(ts) != self._last_minute:
                    self.prev_minute_tv = self.minute_trading_value
                self.minute_trading_value = mtv
                self._last_minute = _minute_key(ts)

        trigger = _f(rd.get("trigger_level"))
        if trigger is not None:
            self.last_trigger = trigger
        if trigger is not None and px >= trigger:
            self.above_trigger_streak += 1
        else:
            self.above_trigger_streak = 0

        vwap = _f(rd.get("vwap"))
        if vwap is not None and vwap > 0:
            if px < vwap:
                self.vwap_below_streak += 1
            else:
                self.vwap_below_streak = 0

        sp = _f(rd.get("spread_bps")) or _board_f(board, "SpreadBps")
        if sp is not None:
            self.spread_bps_last = sp
            self.spread_bps_session_min = min(self.spread_bps_session_min, sp)

        if self.profile in PULLBACK_PROFILES and vwap is not None and vwap > 0:
            if px > vwap:
                self.local_peak = max(self.local_peak, px)
                if self.local_peak > 0 and px < self.local_peak * 0.9985:
                    self.pullback_active = True
                if self.pullback_active and px >= self.local_peak * 0.999:
                    self.rising_streak += 1
                elif px < self.local_peak * 0.999:
                    self.rising_streak = 0
            else:
                self.pullback_active = False
                self.rising_streak = 0
                self.local_peak = px

        self.recent_prices.append(px)
        if len(self.recent_prices) > 6:
            self.recent_prices.pop(0)

        if self.last_price is not None and self.last_ts is not None:
            dt = (ts - self.last_ts).total_seconds()
            if dt >= 25 and self.price_30s_ago is None:
                self.price_30s_ago = self.last_price
            if dt >= 35:
                self.price_30s_ago = self.last_price

        self.last_price = px
        self.last_ts = ts
        if rejects:
            self.last_reject_top = str(rejects[0])

    def composite_score(self, rd: Mapping[str, Any], rejects: list[str]) -> float:
        if self.profile == "continuation_score_v2":
            return continuation_composite(rd, rejects, self)
        if self.profile in ("pullback_vwap_v1", "pullback_vwap_v2"):
            return self._pullback_score(rd, rejects)
        if self.profile == "breakout_confirm_v2":
            return self._breakout_confirm_score(rd, rejects)
        mp = self._momentum_scoring_profile()
        if mp in ("momentum_volume_v1", "momentum_volume_v2"):
            saved = self.profile
            self.profile = mp
            try:
                return self._momentum_volume_score(rd, rejects)
            finally:
                self.profile = saved
        if self.profile == "candidate_only_v1":
            return continuation_composite(rd, rejects, self)
        if self.profile == "hybrid_vwap_momentum_v1":
            return self._hybrid_score(rd, rejects)
        return 0.0

    def _pullback_score(self, rd: Mapping[str, Any], rejects: list[str]) -> float:
        px = _f(rd.get("current_price")) or 0.0
        vwap = _f(rd.get("vwap")) or px
        if px <= vwap:
            return 0.0
        gates = gate_component_scores(rd, rejects)
        depth = self.pullback_depth_pct()
        if self.profile == "pullback_vwap_v2":
            depth_ok = 0.12 <= depth <= 0.45
        else:
            depth_ok = 0.08 <= depth <= 0.55
        score = (
            25.0 * gates["g3"]
            + 20.0 * gates["g5"]
            + 15.0 * gates["g6"]
            + 20.0 * (1.0 if depth_ok else 0.0)
            + 20.0 * min(1.0, self.rising_streak / 3.0)
        )
        if _liquidity_ok(rd, rejects) and not self.spread_worsening():
            score += 10.0
        if self.profile == "pullback_vwap_v2" and self.vwap_below_streak > 0:
            score *= 0.5
        return score

    def _breakout_confirm_score(self, rd: Mapping[str, Any], rejects: list[str]) -> float:
        gates = gate_component_scores(rd, rejects)
        mtv = _f(self.minute_trading_value) or 0.0
        mtv_ok = mtv >= 30_000_000.0
        score = (
            20.0 * gates["g3"]
            + 20.0 * gates["g5"]
            + 15.0 * gates["g6"]
            + 25.0 * min(1.0, self.above_trigger_streak / 3.0)
            + 20.0 * (1.0 if mtv_ok else 0.0)
        )
        if bool(rd.get("breakout_event")):
            score += 5.0
        return score

    def _momentum_volume_score(self, rd: Mapping[str, Any], rejects: list[str]) -> float:
        gates = gate_component_scores(rd, rejects)
        mom = self.price_momentum_pct()
        if self.profile == "momentum_volume_v2":
            mom_score = 1.0 if mom >= 0.06 else min(1.0, max(0.0, mom / 0.06))
        else:
            mom_score = min(1.0, max(0.0, mom / 0.15))
        vol_inc = min(1.0, max(0.0, self.volume_increase_ratio()))
        if self.profile == "momentum_volume_v2" and vol_inc < 0.08:
            vol_inc *= 0.4
        hp = _f(rd.get("high_proximity_ratio")) or 0.0
        vwap_bonus = 0.0
        px = _f(rd.get("current_price")) or 0.0
        vwap = _f(rd.get("vwap")) or 0.0
        if px > vwap > 0:
            vwap_bonus = 8.0
        return (
            22.0 * mom_score
            + 18.0 * vol_inc
            + 18.0 * min(1.0, max(0.0, (hp - 0.985) / 0.015))
            + 14.0 * gates["g3"]
            + 14.0 * gates["g5"]
            + 14.0 * gates["g6"]
            + vwap_bonus
        )

    def _hybrid_score(self, rd: Mapping[str, Any], rejects: list[str]) -> float:
        gates = gate_component_scores(rd, rejects)
        mom = self.price_momentum_pct()
        mom_score = min(1.0, max(0.0, mom / 0.12))
        vol_inc = min(1.0, max(0.0, self.volume_increase_ratio()))
        depth = self.pullback_depth_pct()
        hp = _f(rd.get("high_proximity_ratio")) or 0.0
        pullback_part = 0.0
        if self.pullback_active and 0.08 <= depth <= 0.50:
            pullback_part = min(1.0, self.rising_streak / 2.0)
        proximity_part = min(1.0, max(0.0, (hp - 0.985) / 0.015))
        mtv = _f(self.minute_trading_value) or 0.0
        mtv_score = min(1.0, mtv / 50_000_000.0) if mtv > 0 else 0.0
        return (
            16.0 * mom_score
            + 14.0 * vol_inc
            + 12.0 * mtv_score
            + 14.0 * max(pullback_part, proximity_part * 0.85)
            + 12.0 * gates["g3"]
            + 12.0 * gates["g5"]
            + 10.0 * gates["g6"]
        )

    def is_candidate(self, rd: Mapping[str, Any], rejects: list[str]) -> bool:
        if not _liquidity_ok(rd, rejects):
            return False
        px = _f(rd.get("current_price"))
        vwap = _f(rd.get("vwap"))
        if self.profile in PULLBACK_PROFILES | MOMENTUM_PROFILES | {"hybrid_vwap_momentum_v1"}:
            if vwap is None or px is None or px <= vwap:
                return False
        score = self.composite_score(rd, rejects)
        self.last_entry_v2_score = score
        if self.profile == "breakout_confirm_v2":
            trigger = _f(rd.get("trigger_level"))
            if trigger and px and px >= trigger * 0.998:
                return score >= self.candidate_threshold * 0.85
            return False
        mp = self._momentum_scoring_profile()
        if mp == "momentum_volume_v2":
            if self.volume_increase_ratio() < 0.05 and self.price_momentum_pct() < 0.04:
                return False
        if self.profile == "hybrid_vwap_momentum_v1":
            if self.price_momentum_pct() < 0.02:
                return False
        return score >= self.candidate_threshold

    def is_entry(self, rd: Mapping[str, Any], rejects: list[str], *, tier: str) -> bool:
        if tier.upper() == "C":
            return False
        if not self.is_candidate(rd, rejects):
            return False
        score = self.composite_score(rd, rejects)
        self.last_entry_v2_score = score
        if self.profile == "breakout_confirm_v2":
            return (
                score >= self.entry_threshold
                and self.above_trigger_streak >= 2
                and _liquidity_ok(rd, rejects)
            )
        if self.profile == "pullback_vwap_v1":
            return (
                score >= self.entry_threshold
                and self.rising_streak >= 2
                and self.pullback_active
                and _liquidity_ok(rd, rejects)
            )
        if self.profile == "pullback_vwap_v2":
            depth = self.pullback_depth_pct()
            if not (0.12 <= depth <= 0.45):
                return False
            if self.vwap_below_streak > 0:
                return False
            if self.spread_worsening():
                return False
            if depth > 0.40:
                return False
            return (
                score >= self.entry_threshold
                and self.rising_streak >= 2
                and self.pullback_active
                and _liquidity_ok(rd, rejects)
            )
        if self.profile == "momentum_volume_v1":
            px = _f(rd.get("current_price")) or 0.0
            vwap = _f(rd.get("vwap")) or 0.0
            return score >= self.entry_threshold and px > vwap and _liquidity_ok(rd, rejects)
        mp = self._momentum_scoring_profile()
        if mp == "momentum_volume_v2":
            mom = self.price_momentum_pct()
            vol = self.volume_increase_ratio()
            px = _f(rd.get("current_price")) or 0.0
            vwap = _f(rd.get("vwap")) or 0.0
            if mom < 0.06 or vol < 0.08:
                return False
            if self.immediate_reversal():
                return False
            if self.spread_worsening():
                return False
            if self.profile in MOMENTUM_V3_ENTRY_GUARD_PROFILES and self.entry_v3_guard_blocks(rd):
                return False
            return score >= self.entry_threshold and px > vwap and _liquidity_ok(rd, rejects)
        if self.profile in ("momentum_volume_v3_exit_guard", "momentum_volume_v3_take_guard"):
            mom = self.price_momentum_pct()
            vol = self.volume_increase_ratio()
            px = _f(rd.get("current_price")) or 0.0
            vwap = _f(rd.get("vwap")) or 0.0
            if mom < 0.06 or vol < 0.08:
                return False
            if self.immediate_reversal() or self.spread_worsening():
                return False
            return score >= self.entry_threshold and px > vwap and _liquidity_ok(rd, rejects)
        if self.profile == "hybrid_vwap_momentum_v1":
            mom = self.price_momentum_pct()
            mtv = _f(self.minute_trading_value) or 0.0
            hp = _f(rd.get("high_proximity_ratio")) or 0.0
            pullback_ok = self.pullback_active and self.rising_streak >= 1
            proximity_ok = hp >= 0.988
            if mom < 0.04 or mtv < 20_000_000.0:
                return False
            if not (pullback_ok or proximity_ok):
                return False
            if self.immediate_reversal() or self.spread_worsening():
                return False
            return score >= self.entry_threshold and _liquidity_ok(rd, rejects)
        return score >= self.entry_threshold and _liquidity_ok(rd, rejects)

    def entry_snapshot(self, rd: Mapping[str, Any]) -> dict[str, Any]:
        snap = {
            "vwap_distance_pct": rd.get("vwap_distance_pct"),
            "minute_trading_value": self.minute_trading_value,
            "price_momentum_pct": self.price_momentum_pct(),
            "volume_increase_ratio": self.volume_increase_ratio(),
            "entry_v2_score": self.last_entry_v2_score,
            "spread_bps": rd.get("spread_bps") or self.spread_bps_last,
            "pullback_depth_pct": self.pullback_depth_pct(),
            "board_imbalance_entry": rd.get("board_imbalance"),
            "breakout_event": rd.get("breakout_event"),
        }
        return snap
