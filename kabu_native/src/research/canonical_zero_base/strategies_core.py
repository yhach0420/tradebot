"""Z1–Z4 zero-base ENTRY triggers (no legacy PBv2 / Board mid-high / trailing)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from research.canonical_zero_base.episode_builder import EpisodeEvent


@dataclass
class Trigger:
    strategy_id: str
    episode_id: str
    event: EpisodeEvent
    entry_ask: float
    groups_hit: dict[str, bool]
    base_ok: bool


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _z1_base(f: dict[str, Any], setup: str) -> bool:
    if setup not in ("pullback", "impulse"):
        # allow impulse→pullback reclaim
        if setup != "pullback":
            return False
    dd = _f(f.get("drawdown_from_high"))
    bounce = _f(f.get("bounce_from_low"))
    hl = f.get("higher_low")
    ll = f.get("lower_low")
    if dd is None or bounce is None:
        return False
    if ll is True:
        return False
    return dd > 0.0015 and bounce > 0.0005 and (hl is True or bounce > 0.001)


def _z2_base(f: dict[str, Any], setup: str) -> bool:
    dist = _f(f.get("distance_from_high"))
    r5 = _f(f.get("return_5s"))
    hh = f.get("higher_high")
    if dist is None or r5 is None:
        return False
    return dist <= 0.0008 and r5 > 0 and (hh is True or setup == "breakout")


def _z3_base(f: dict[str, Any], setup: str) -> bool:
    ask_q = _f(f.get("canonical_ask_qty"))
    bid_q = _f(f.get("canonical_bid_qty"))
    ask_dep = f.get("ask_depletion")
    ll = f.get("lower_low")
    r5 = _f(f.get("return_5s"))
    if ask_q is None or bid_q is None:
        return False
    if ll is True:
        return False
    # sell wall: ask qty large and depleting while price not falling
    return ask_q >= bid_q and ask_dep is True and (r5 is None or r5 >= -0.0005)


def _z4_base(f: dict[str, Any], setup: str) -> bool:
    comp = _f(f.get("compression_ratio"))
    r5 = _f(f.get("return_5s"))
    dry = f.get("volume_dryup")
    if comp is None or r5 is None:
        return False
    return (setup == "compression" or (comp < 0.45 and dry is True)) and r5 > 0.0003


def group_flags(f: dict[str, Any], *, thr: dict[str, float]) -> dict[str, bool]:
    """Coarse group confirmations using frozen thresholds."""
    price = True  # base already includes price
    vol_base = _f(f.get("volume_vs_recent_baseline"))
    vol_acc = _f(f.get("volume_acceleration"))
    volume = (vol_base is not None and vol_base >= thr.get("vol_base", 1.0)) or (
        vol_acc is not None and vol_acc > thr.get("vol_acc", 0.0)
    )
    ur = _f(f.get("uptick_ratio"))
    cu = f.get("consecutive_upticks") or 0
    flow = (ur is not None and ur >= thr.get("uptick_ratio", 0.55)) or (cu >= thr.get("consec_up", 2))
    top = _f(f.get("canonical_top_imbalance"))
    board = (
        (top is not None and top >= thr.get("top_imb", 0.52))
        or f.get("ask_depletion") is True
        or f.get("bid_replenishment") is True
    )
    sbps = _f(f.get("spread_bps"))
    bq = _f(f.get("canonical_bid_qty"))
    aq = _f(f.get("canonical_ask_qty"))
    liquidity = (sbps is not None and sbps <= thr.get("spread_bps_max", 40.0)) and (
        bq is not None and aq is not None and bq + aq >= thr.get("min_qty", 100.0)
    )
    context = f.get("quote_quality") is True
    return {
        "PRICE": price,
        "VOLUME": bool(volume),
        "FLOW": bool(flow),
        "BOARD": bool(board),
        "LIQUIDITY": bool(liquidity),
        "CONTEXT": bool(context),
    }


BASE_FN = {"Z1": _z1_base, "Z2": _z2_base, "Z3": _z3_base, "Z4": _z4_base}


def scan_triggers(events: Sequence[EpisodeEvent], strategy_id: str, *, thr: dict[str, float]) -> list[Trigger]:
    fn = BASE_FN[strategy_id]
    out: list[Trigger] = []
    last_ep: dict[str, int] = {}
    for e in events:
        f = e.features
        if not f.get("quote_quality"):
            continue
        ask = _f(f.get("best_ask"))
        if ask is None or ask <= 0:
            continue
        if not fn(f, e.setup):
            continue
        # one trigger per episode max at scan stage (one episode one entry)
        if e.episode_id in last_ep:
            continue
        g = group_flags(f, thr=thr)
        out.append(
            Trigger(
                strategy_id=strategy_id,
                episode_id=e.episode_id,
                event=e,
                entry_ask=float(ask),
                groups_hit=g,
                base_ok=True,
            )
        )
        last_ep[e.episode_id] = 1
    return out


# Template: which groups required beyond PRICE
TEMPLATES: dict[str, tuple[str, ...]] = {
    "T0": (),
    "T1": ("VOLUME",),
    "T2": ("FLOW",),
    "T3": ("BOARD",),
    "T4": ("VOLUME", "FLOW"),
    "T5": ("VOLUME", "BOARD"),
    "T6": ("FLOW", "BOARD"),
    "T7": ("VOLUME", "FLOW", "BOARD"),
    "T8": ("VOLUME", "FLOW", "BOARD", "LIQUIDITY"),  # T7 + LIQUIDITY
    "T9": ("VOLUME", "FLOW", "BOARD", "LIQUIDITY", "CONTEXT"),
}


def pass_template(g: dict[str, bool], template: str) -> bool:
    need = TEMPLATES[template]
    if not g.get("PRICE", True):
        return False
    return all(g.get(x, False) for x in need)
