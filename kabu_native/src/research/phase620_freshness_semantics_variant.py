"""
Phase620: freshness semantics variants (research-only monkey-patch).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

from small_paper.entry_scan_controller import (
    REJECT_DATA_STALE_BOARD,
    REJECT_DATA_STALE_PRICE,
    EntryFreshnessDecision,
    EntryFreshnessSnapshot,
    PRICE_FRESHNESS_CURRENT,
    PRICE_FRESHNESS_STALE_REJECT,
    _board_fallback_eligible,
    _price_ts_fresh,
    _spread_bps_from_payload,
    evaluate_entry_data_freshness as _orig_evaluate,
)
from storage.intraday_recorder import parse_kabu_time
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

REJECT_EVENT_STALE_PRICE = "event_stale_price"
TAG_LIQUIDITY_STALE_TRADE = "liquidity_stale_trade"
TAG_LIQUIDITY_GUARD_PASS = "liquidity_guard_pass"

SIM_EVENT_LAG_SEC = float(os.environ.get("PHASE620_EVENT_LAG_SEC", "2.17"))


@dataclass(frozen=True)
class FreshnessVariant:
    variant_id: str
    label: str
    event_threshold_sec: float
    board_threshold_sec: float
    trade_threshold_sec: float = 3.0
    trade_mode: str = "reject_cpt"
    liquidity_max_spread_bps: float = 20.0
    use_event_check: bool = False


VARIANTS: dict[str, FreshnessVariant] = {
    "baseline": FreshnessVariant(
        "baseline",
        "Current CPT>3s data_stale_price + board_stale",
        event_threshold_sec=999.0,
        board_threshold_sec=3.0,
        trade_mode="reject_cpt",
        use_event_check=False,
    ),
    "A": FreshnessVariant(
        "A",
        "event+board reject; trade soft tag",
        event_threshold_sec=3.0,
        board_threshold_sec=3.0,
        trade_mode="soft_tag",
        use_event_check=True,
    ),
    "B": FreshnessVariant(
        "B",
        "event+board reject; trade liquidity guard spread<=20",
        event_threshold_sec=3.0,
        board_threshold_sec=3.0,
        trade_mode="liquidity_guard",
        liquidity_max_spread_bps=20.0,
        use_event_check=True,
    ),
    "C": FreshnessVariant(
        "C",
        "event+board reject; trade OFF",
        event_threshold_sec=3.0,
        board_threshold_sec=3.0,
        trade_mode="off",
        use_event_check=True,
    ),
    "D": FreshnessVariant(
        "D",
        "event 2s + board 3s + trade soft",
        event_threshold_sec=2.0,
        board_threshold_sec=3.0,
        trade_mode="soft_tag",
        use_event_check=True,
    ),
    "E": FreshnessVariant(
        "E",
        "event 5s + board 3s + trade soft",
        event_threshold_sec=5.0,
        board_threshold_sec=3.0,
        trade_mode="soft_tag",
        use_event_check=True,
    ),
    "F": FreshnessVariant(
        "F",
        "event 3s + board 2s + trade soft",
        event_threshold_sec=3.0,
        board_threshold_sec=2.0,
        trade_mode="soft_tag",
        use_event_check=True,
    ),
}

# Phase620 v2 subset (today's adoption check)
V2_VARIANT_IDS: tuple[str, ...] = ("baseline", "A", "B", "C", "D", "P603_ref")

# v2 remaps: C=event 5s, D=event 2s (reuse E/D specs)
V2_VARIANT_ALIASES: dict[str, str] = {
    "baseline": "baseline",
    "A": "A",
    "B": "B",
    "C": "E",
    "D": "D",
    "P603_ref": "P603_ref",
}

_active: Optional[FreshnessVariant] = None
_orig_eval: Any = None
_tag_counter: dict[str, int] = {
    "liquidity_stale_trade": 0,
    "liquidity_guard_pass": 0,
    "soft_tag": 0,
}


def get_active_variant() -> Optional[FreshnessVariant]:
    return _active


def reset_tag_counter() -> None:
    for k in _tag_counter:
        _tag_counter[k] = 0


def tag_counts() -> dict[str, int]:
    return dict(_tag_counter)


def _event_age_sec(payload: Mapping[str, Any], *, reference_now: Optional[datetime]) -> float:
    """Replay evaluates at push recorded_at (lag≈0); model live queue delay as constant."""
    if not _active or not _active.use_event_check:
        return 0.0
    rec = payload.get("recorded_at")
    if rec is None or str(rec).strip() == "":
        return SIM_EVENT_LAG_SEC + 0.5
    if reference_now is not None:
        rec_dt = parse_kabu_time(rec, fallback=reference_now)
        lag = max(0.0, (reference_now - rec_dt).total_seconds())
        if lag <= 0.05:
            return SIM_EVENT_LAG_SEC
        return lag
    return SIM_EVENT_LAG_SEC


def _make_evaluator(variant: FreshnessVariant):
    def _evaluate(
        snap: EntryFreshnessSnapshot,
        payload: Mapping[str, Any],
        *,
        max_price_age_sec: float,
        max_board_age_sec: float,
        guard_enabled: bool = True,
        board_fallback_enabled: bool = False,
        max_fallback_spread_bps: float = 50.0,
        reference_now: Optional[datetime] = None,
        **kwargs: Any,
    ) -> EntryFreshnessDecision:
        if variant.variant_id == "baseline":
            return _orig_evaluate(
                snap,
                payload,
                max_price_age_sec=max_price_age_sec,
                max_board_age_sec=max_board_age_sec,
                guard_enabled=guard_enabled,
                board_fallback_enabled=False,
                max_fallback_spread_bps=max_fallback_spread_bps,
            )

        spread_bps = _spread_bps_from_payload(payload)
        if not guard_enabled:
            return EntryFreshnessDecision(
                reject_reason=None,
                price_freshness_source=PRICE_FRESHNESS_CURRENT,
                spread_bps=spread_bps,
                fallback_used=False,
                fallback_reject_reason=None,
                snapshot=snap,
            )

        event_age = _event_age_sec(payload, reference_now=reference_now)
        if variant.use_event_check and event_age > variant.event_threshold_sec:
            return EntryFreshnessDecision(
                reject_reason=REJECT_EVENT_STALE_PRICE,
                price_freshness_source=PRICE_FRESHNESS_STALE_REJECT,
                spread_bps=spread_bps,
                fallback_used=False,
                fallback_reject_reason=f"event_age={event_age:.3f}",
                snapshot=snap,
            )

        board_age = snap.board_age_sec
        if snap.last_board_update_ts is None or board_age is None or board_age > variant.board_threshold_sec:
            return EntryFreshnessDecision(
                reject_reason=REJECT_DATA_STALE_BOARD,
                price_freshness_source=PRICE_FRESHNESS_CURRENT,
                spread_bps=spread_bps,
                fallback_used=False,
                fallback_reject_reason=None,
                snapshot=snap,
            )

        trade_stale = not _price_ts_fresh(snap, max_price_age_sec=variant.trade_threshold_sec)

        if variant.trade_mode == "reject_cpt" and trade_stale:
            return EntryFreshnessDecision(
                reject_reason=REJECT_DATA_STALE_PRICE,
                price_freshness_source=PRICE_FRESHNESS_STALE_REJECT,
                spread_bps=spread_bps,
                fallback_used=False,
                fallback_reject_reason=None,
                snapshot=snap,
            )

        if trade_stale and variant.trade_mode == "liquidity_guard":
            ok, _ = _board_fallback_eligible(
                payload,
                snap,
                max_board_age_sec=variant.board_threshold_sec,
                max_spread_bps=variant.liquidity_max_spread_bps,
            )
            if ok:
                _tag_counter["liquidity_guard_pass"] += 1
                return EntryFreshnessDecision(
                    reject_reason=None,
                    price_freshness_source=TAG_LIQUIDITY_GUARD_PASS,
                    spread_bps=spread_bps,
                    fallback_used=True,
                    fallback_reject_reason=None,
                    snapshot=snap,
                )
            _tag_counter["liquidity_stale_trade"] += 1

        if trade_stale and variant.trade_mode == "soft_tag":
            _tag_counter["soft_tag"] += 1
            _tag_counter["liquidity_stale_trade"] += 1

        src = PRICE_FRESHNESS_CURRENT
        if trade_stale and variant.trade_mode in ("soft_tag", "off", "liquidity_guard"):
            src = TAG_LIQUIDITY_STALE_TRADE

        return EntryFreshnessDecision(
            reject_reason=None,
            price_freshness_source=src,
            spread_bps=spread_bps,
            fallback_used=False,
            fallback_reject_reason=None,
            snapshot=snap,
        )

    return _evaluate


def apply_variant(variant_id: str) -> FreshnessVariant:
    global _active, _orig_eval
    import small_paper.entry_scan_controller as esc

    resolved = V2_VARIANT_ALIASES.get(variant_id, variant_id)
    if resolved == "P603_ref":
        restore_variant()
        return FreshnessVariant("P603_ref", "Phase603 board_fallback", 3.0, 3.0)

    variant = VARIANTS[resolved]
    _active = variant
    reset_tag_counter()
    if _orig_eval is None:
        _orig_eval = esc.evaluate_entry_data_freshness
    esc.evaluate_entry_data_freshness = _make_evaluator(variant)
    return variant


def restore_variant() -> None:
    global _active, _orig_eval
    import small_paper.entry_scan_controller as esc

    if _orig_eval is not None:
        esc.evaluate_entry_data_freshness = _orig_eval
    _active = None
