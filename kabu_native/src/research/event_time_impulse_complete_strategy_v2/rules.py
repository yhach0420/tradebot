"""Entry and exit predicates. No threshold search."""
from __future__ import annotations

from research.event_time_impulse_complete_strategy_v2 import RECONFIRM_GAP_SEC


def reconfirm(vol10: bool, vol30: bool, buy: bool, classified: float, tick: bool, price_break: bool) -> bool:
    return bool(vol10 and vol30 and buy and classified > 0.0 and tick and price_break)


def participation_lost(classified: float, ask_vol: float, bid_vol: float) -> bool:
    return bool(classified > 0.0 and ask_vol <= bid_vol)


def activity_lost(vol10: bool, vol30: bool, tick: bool) -> bool:
    return not vol10 and not vol30 and not tick


def exhausted(lost_participation: bool, lost_activity: bool, seconds_since_reconfirm: float) -> bool:
    return bool(lost_participation and lost_activity and seconds_since_reconfirm >= RECONFIRM_GAP_SEC)


def support_failure(price: float, level: float) -> bool:
    return bool(price == price and price > 0.0 and level == level and price <= level)


def ratchet(new_level: float, active: float) -> float:
    if new_level == new_level and active == active and new_level > active:
        return float(new_level)
    return float(active)
