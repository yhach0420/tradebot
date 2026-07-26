"""Wide causal ENTRY feature generation at anchors (not v1 26-feature dict)."""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.constants import LOT, PRICE_WINDOWS_SEC
from research.canonical_zero_base_v2.loader import Tick

# Inventory metadata populated at module load / first compute
FEATURE_INVENTORY: list[dict[str, Any]] = []


def _reg(name: str, group: str, formula: str, window: Any, kind: str, hyp: str) -> None:
    FEATURE_INVENTORY.append({
        "feature_id": name,
        "feature_name": name,
        "group": group,
        "formula": formula,
        "window": window,
        "required_raw_fields": "Buy1,Sell1,CurrentPrice,TradingVolume,received_at",
        "causal_availability": "past_only",
        "direction_hypothesis": hyp,
        "static_dynamic_sequence": kind,
        "snapshot_state_transition": "snapshot" if kind == "static" else kind,
        "leakage_status": "PASS_CAUSAL",
        "implementation_status": "COMPUTED",
    })


def _idxs(ticks: Sequence[Tick], i: int, sec: float) -> list[int]:
    t1 = ticks[i].ts
    out = []
    for j in range(i, -1, -1):
        if (t1 - ticks[j].ts).total_seconds() > sec:
            break
        out.append(j)
    return list(reversed(out))


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def compute_entry_features(ticks: Sequence[Tick], i: int) -> dict[str, Optional[float]]:
    """Past-only features at decision index i."""
    out: dict[str, Optional[float]] = {}
    if i < 1 or i >= len(ticks):
        return {"quote_quality": 0.0}
    t = ticks[i]
    b = t.board
    out["quote_quality"] = 1.0 if b.canonical_quote_valid else 0.0
    out["spread"] = _f(b.canonical_spread)
    out["spread_bps"] = _f(b.canonical_spread_bps)
    out["canonical_bid_qty"] = _f(b.canonical_bid_qty)
    out["canonical_ask_qty"] = _f(b.canonical_ask_qty)
    out["top_imbalance"] = _f(b.canonical_top_imbalance)
    out["depth_imbalance"] = _f(b.canonical_depth_imbalance)
    out["exec_qty_ok"] = 1.0 if (b.canonical_ask_qty or 0) >= LOT else 0.0
    out["microprice"] = None
    if b.canonical_best_bid and b.canonical_best_ask and b.canonical_bid_qty and b.canonical_ask_qty:
        den = b.canonical_bid_qty + b.canonical_ask_qty
        if den > 0:
            out["microprice"] = (
                b.canonical_best_bid * b.canonical_ask_qty + b.canonical_best_ask * b.canonical_bid_qty
            ) / den

    # depth aggregates
    for depth_n, tag in ((3, "d3"), (5, "d5"), (10, "d10")):
        bid_q = sum((q or 0) for _, q in (t.depth_bid[:depth_n] if t.depth_bid else []))
        ask_q = sum((q or 0) for _, q in (t.depth_ask[:depth_n] if t.depth_ask else []))
        out[f"bid_qty_{tag}"] = bid_q
        out[f"ask_qty_{tag}"] = ask_q
        tot = bid_q + ask_q
        out[f"depth_imb_{tag}"] = (bid_q - ask_q) / tot if tot > 0 else None

    for w in PRICE_WINDOWS_SEC:
        ix = _idxs(ticks, i, float(w))
        if len(ix) < 2:
            for k in (
                f"return_{w}s", f"log_return_{w}s", f"slope_{w}s", f"accel_{w}s",
                f"range_{w}s", f"rv_{w}s", f"dist_high_{w}s", f"bounce_low_{w}s",
                f"hh_count_{w}s", f"ll_count_{w}s", f"uptick_ratio_{w}s",
                f"signed_vol_{w}s", f"vol_rate_{w}s", f"trade_freq_{w}s",
            ):
                out[k] = None
            continue
        prices = [ticks[j].px for j in ix]
        p0, p1 = prices[0], prices[-1]
        out[f"return_{w}s"] = (p1 - p0) / p0 if p0 > 0 else None
        out[f"log_return_{w}s"] = math.log(p1 / p0) if p0 > 0 and p1 > 0 else None
        out[f"slope_{w}s"] = (p1 - p0) / max(1, len(prices) - 1)
        if len(prices) >= 5:
            mid = len(prices) // 2
            s1 = (prices[mid] - prices[0]) / max(1, mid)
            s2 = (prices[-1] - prices[mid]) / max(1, len(prices) - mid - 1)
            out[f"accel_{w}s"] = s2 - s1
        else:
            out[f"accel_{w}s"] = None
        hi, lo = max(prices), min(prices)
        out[f"high_{w}s"] = hi
        out[f"low_{w}s"] = lo
        out[f"range_{w}s"] = (hi - lo) / lo if lo > 0 else None
        rets = [(prices[k] - prices[k - 1]) / prices[k - 1] for k in range(1, len(prices)) if prices[k - 1] > 0]
        if rets:
            mu = sum(rets) / len(rets)
            out[f"rv_{w}s"] = (sum((x - mu) ** 2 for x in rets) / len(rets)) ** 0.5
        else:
            out[f"rv_{w}s"] = None
        out[f"dist_high_{w}s"] = (hi - p1) / hi if hi > 0 else None
        out[f"bounce_low_{w}s"] = (p1 - lo) / lo if lo > 0 else None
        out[f"fall_high_{w}s"] = out[f"dist_high_{w}s"]
        hh = hl = lh = ll = 0
        for k in range(2, len(prices)):
            if prices[k] > prices[k - 1] > prices[k - 2]:
                hh += 1
            if prices[k] < prices[k - 1] < prices[k - 2]:
                ll += 1
        out[f"hh_count_{w}s"] = float(hh)
        out[f"ll_count_{w}s"] = float(ll)
        # ticks / flow
        up = dn = z = 0
        signed = 0.0
        vols = []
        for k in range(1, len(ix)):
            a, c = ticks[ix[k - 1]], ticks[ix[k]]
            if c.px > a.px:
                up += 1
                signed += (c.vol or 0) - (a.vol or 0) if c.vol and a.vol else 1
            elif c.px < a.px:
                dn += 1
                signed -= (c.vol or 0) - (a.vol or 0) if c.vol and a.vol else 1
            else:
                z += 1
            if c.vol is not None and a.vol is not None and c.vol >= a.vol:
                vols.append(c.vol - a.vol)
        tot = up + dn + z
        out[f"uptick_ratio_{w}s"] = up / tot if tot else None
        out[f"downtick_ratio_{w}s"] = dn / tot if tot else None
        out[f"signed_vol_{w}s"] = signed
        out[f"vol_rate_{w}s"] = sum(vols) / w if vols else None
        out[f"trade_freq_{w}s"] = len(ix) / w
        # consecutive
        cu = cd = 0
        for k in range(len(prices) - 1, 0, -1):
            if prices[k] > prices[k - 1]:
                if cd:
                    break
                cu += 1
            elif prices[k] < prices[k - 1]:
                if cu:
                    break
                cd += 1
            else:
                break
        out[f"consec_up_{w}s"] = float(cu)
        out[f"consec_dn_{w}s"] = float(cd)
        # efficiency
        path = sum(abs(prices[k] - prices[k - 1]) for k in range(1, len(prices)))
        out[f"price_efficiency_{w}s"] = abs(p1 - p0) / path if path > 0 else None
        # board change vs start of window
        b0 = ticks[ix[0]].board
        out[f"bid_qty_chg_{w}s"] = (_f(b.canonical_bid_qty) or 0) - (_f(b0.canonical_bid_qty) or 0)
        out[f"ask_qty_chg_{w}s"] = (_f(b.canonical_ask_qty) or 0) - (_f(b0.canonical_ask_qty) or 0)
        out[f"spread_chg_{w}s"] = (_f(b.canonical_spread) or 0) - (_f(b0.canonical_spread) or 0)
        out[f"bid_depletion_{w}s"] = 1.0 if out[f"bid_qty_chg_{w}s"] < 0 else 0.0
        out[f"ask_depletion_{w}s"] = 1.0 if out[f"ask_qty_chg_{w}s"] < 0 else 0.0
        out[f"bid_replenish_{w}s"] = 1.0 if out[f"bid_qty_chg_{w}s"] > 0 else 0.0
        out[f"ask_replenish_{w}s"] = 1.0 if out[f"ask_qty_chg_{w}s"] > 0 else 0.0

    # wall / absorption (state-ish)
    aq = _f(b.canonical_ask_qty) or 0
    bq = _f(b.canonical_bid_qty) or 0
    out["wall_ask_ratio"] = aq / bq if bq > 0 else None
    out["wall_persistence_proxy"] = None
    if i >= 10:
        persist = sum(1 for j in range(i - 10, i + 1) if (ticks[j].board.canonical_ask_qty or 0) >= bq * 1.2)
        out["wall_persistence_proxy"] = float(persist)
        aq0 = ticks[i - 10].board.canonical_ask_qty or aq
        out["wall_consumption_ratio"] = (aq0 - aq) / aq0 if aq0 > 0 else None
        out["price_stable_while_wall"] = 1.0 if abs(t.px - ticks[i - 10].px) / t.px < 0.002 else 0.0
    out["absorption_confidence"] = None
    if out.get("wall_consumption_ratio") is not None and out.get("price_stable_while_wall") == 1.0:
        out["absorption_confidence"] = max(0.0, float(out["wall_consumption_ratio"]))

    # compression / impulse sequence
    r30 = out.get("range_30s")
    r5 = out.get("range_5s")
    out["compression_ratio"] = (r5 / r30) if r5 is not None and r30 and r30 > 0 else None
    out["impulse_size_60s"] = out.get("return_60s")
    out["pullback_depth"] = out.get("dist_high_30s")
    out["breakout_distance"] = out.get("dist_high_30s")
    out["reclaim_distance"] = out.get("bounce_low_30s")
    # VWAP proxy
    ix60 = _idxs(ticks, i, 60)
    if len(ix60) >= 3:
        num = den = 0.0
        for j in ix60:
            vdelta = 1.0
            if j > 0 and ticks[j].vol is not None and ticks[j - 1].vol is not None:
                vdelta = max(0.0, ticks[j].vol - ticks[j - 1].vol) or 1.0
            num += ticks[j].px * vdelta
            den += vdelta
        vwap = num / den if den else None
        out["vwap_distance"] = (t.px - vwap) / vwap if vwap else None
    else:
        out["vwap_distance"] = None

    # liquidity
    out["spread_to_range"] = None
    if out.get("spread_bps") is not None and out.get("range_30s"):
        out["spread_to_range"] = out["spread_bps"] / max(out["range_30s"] * 10000, 1e-6)
    out["quote_age_proxy"] = (t.ts - ticks[i - 1].ts).total_seconds() if i > 0 else None

    # flow confidence (tick-rule estimate)
    out["flow_direction_confidence"] = 0.6  # tick rule mid confidence
    out["aggressive_buy_persistence"] = out.get("uptick_ratio_15s")
    out["aggressive_sell_persistence"] = out.get("downtick_ratio_15s")
    out["volume_dryup"] = 1.0 if (out.get("vol_rate_30s") or 1) < (out.get("vol_rate_60s") or 1) * 0.5 else 0.0
    out["volume_burst"] = 1.0 if (out.get("vol_rate_5s") or 0) > (out.get("vol_rate_60s") or 0) * 1.5 else 0.0

    # book flip / divergence
    out["top_vs_depth_div"] = None
    if out.get("top_imbalance") is not None and out.get("depth_imb_d5") is not None:
        out["top_vs_depth_div"] = abs(out["top_imbalance"] - out["depth_imb_d5"])
    out["book_flip"] = 0.0
    if i >= 5:
        ti0 = ticks[i - 5].board.canonical_top_imbalance
        ti1 = b.canonical_top_imbalance
        if ti0 is not None and ti1 is not None and (ti0 - 0.5) * (ti1 - 0.5) < 0:
            out["book_flip"] = 1.0

    return out


def ensure_inventory(sample_feats: dict[str, Any]) -> list[dict[str, Any]]:
    """Build inventory from computed keys if empty."""
    if FEATURE_INVENTORY:
        return FEATURE_INVENTORY
    for name in sorted(sample_feats.keys()):
        group = "PRICE"
        kind = "dynamic"
        if any(x in name for x in ("vol_rate", "volume_", "vol_")):
            group = "VOLUME"
        if any(x in name for x in ("uptick", "downtick", "signed_", "flow", "aggressive")):
            group = "FLOW"
        if any(x in name for x in ("imb", "bid_qty", "ask_qty", "spread", "book_", "micro", "depth_", "bid_dep", "ask_dep", "bid_rep", "ask_rep")):
            group = "BOARD"
        if "wall" in name or "absorption" in name:
            group = "WALL"
        if any(x in name for x in ("exec_", "quote_age", "spread_to")):
            group = "LIQUIDITY"
        if any(x in name for x in ("compression", "impulse", "pullback", "reclaim", "breakout", "consec")):
            kind = "sequence"
        if any(x in name for x in ("book_flip", "wall_persistence", "depletion", "replenish")):
            kind = "state-transition"
        if name in ("quote_quality", "exec_qty_ok"):
            kind = "static"
        _reg(name, group, f"computed:{name}", "multi", kind, "exploratory")
    return FEATURE_INVENTORY


def feature_kind_counts(inv: Sequence[dict[str, Any]]) -> dict[str, int]:
    c = {"static": 0, "dynamic": 0, "sequence": 0, "state-transition": 0}
    for r in inv:
        k = r.get("static_dynamic_sequence") or "dynamic"
        if k not in c:
            c[k] = 0
        c[k] += 1
    return c
