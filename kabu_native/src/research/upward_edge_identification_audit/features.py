"""G1–G6 feature engine — causal, single-pass, persistence restored."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Deque, Optional, Sequence

from research.upward_edge_identification_audit.constants import COST_BPS, WARMUP_SEC
from research.upward_edge_identification_audit.loader import Tick


def _safe_div(num: float, den: float) -> Optional[float]:
    if den == 0 or den is None:
        return None
    return num / den


@dataclass
class _Evt:
    ts: datetime
    px: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    side: str
    qty: float
    bid_qty: Optional[float]
    ask_qty: Optional[float]


@dataclass
class FeatureEngine:
    """Per-stream rolling state. Call update(tick) then snapshot()."""
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    vwap_num: float = 0.0
    vwap_den: float = 0.0
    hist: Deque[_Evt] = field(default_factory=lambda: deque(maxlen=8000))
    # persistence
    bid_level: Optional[float] = None
    bid_level_since: Optional[datetime] = None
    ask_level: Optional[float] = None
    ask_level_since: Optional[datetime] = None
    last_low_ts: Optional[datetime] = None
    last_high_ts: Optional[datetime] = None
    last_low_px: Optional[float] = None
    last_high_px: Optional[float] = None
    buy_dom_since: Optional[datetime] = None
    sell_dom_since: Optional[datetime] = None
    consec_buy_dom: int = 0
    consec_sell_dom: int = 0
    bid_follow: int = 0
    ask_follow: int = 0
    bid_replenish: int = 0
    ask_replenish: int = 0
    repl_after_exec: int = 0
    same_price_repl_since: Optional[datetime] = None
    spread_stable_since: Optional[datetime] = None
    last_spread: Optional[float] = None
    last_high_update_ts: Optional[datetime] = None
    last_low_update_ts: Optional[datetime] = None
    high_intervals: list[float] = field(default_factory=list)
    low_intervals: list[float] = field(default_factory=list)
    # flow efficiency accumulators (30s window rebuilt from hist)
    stream_start: Optional[datetime] = None
    last_side: str = "NONE"
    rise_start_px: Optional[float] = None
    prev_bid: Optional[float] = None
    prev_ask: Optional[float] = None
    prev_bq: Optional[float] = None
    prev_aq: Optional[float] = None
    buy_hits_streak: int = 0
    sell_hits_streak: int = 0
    # market context injectables (set externally per tick)
    breadth_up: Optional[float] = None
    breadth_down: Optional[float] = None
    median_ret: Optional[float] = None
    rel_ret: Optional[float] = None
    ret_percentile: Optional[float] = None
    flow_percentile: Optional[float] = None
    rank_strength: Optional[float] = None
    mkt_buy_breadth: Optional[float] = None
    mkt_sell_breadth: Optional[float] = None

    def update(self, t: Tick) -> None:
        if self.stream_start is None:
            self.stream_start = t.ts
        bid = t.board.canonical_best_bid
        ask = t.board.canonical_best_ask
        bq = t.board.canonical_bid_qty
        aq = t.board.canonical_ask_qty
        spr = t.board.canonical_spread_bps
        px = t.px if t.px is not None else ((bid + ask) / 2 if bid and ask else None)
        qty = float(t.volume_delta) if t.volume_delta and t.volume_delta > 0 else 0.0

        if px is not None:
            self.day_high = px if self.day_high is None else max(self.day_high, px)
            self.day_low = px if self.day_low is None else min(self.day_low, px)
            if qty > 0:
                self.vwap_num += px * qty
                self.vwap_den += qty
            # local high/low tracking
            if self.last_high_px is None or px >= self.last_high_px:
                if self.last_high_update_ts is not None:
                    self.high_intervals.append((t.ts - self.last_high_update_ts).total_seconds())
                    if len(self.high_intervals) > 50:
                        self.high_intervals = self.high_intervals[-50:]
                self.last_high_px = px
                self.last_high_ts = t.ts
                self.last_high_update_ts = t.ts
            if self.last_low_px is None or px <= self.last_low_px:
                if self.last_low_update_ts is not None:
                    self.low_intervals.append((t.ts - self.last_low_update_ts).total_seconds())
                    if len(self.low_intervals) > 50:
                        self.low_intervals = self.low_intervals[-50:]
                self.last_low_px = px
                self.last_low_ts = t.ts
                self.last_low_update_ts = t.ts

        # bid/ask survival
        if bid is not None:
            if self.bid_level is None or abs(bid - self.bid_level) > 1e-9:
                self.bid_level = bid
                self.bid_level_since = t.ts
                self.same_price_repl_since = None
            if self.prev_bid is not None and bid > self.prev_bid:
                self.bid_follow += 1
        if ask is not None:
            if self.ask_level is None or abs(ask - self.ask_level) > 1e-9:
                self.ask_level = ask
                self.ask_level_since = t.ts
            if self.prev_ask is not None and ask > self.prev_ask:
                self.ask_follow += 1

        # replenishment after execution
        if bid is not None and bq is not None and self.prev_bid is not None and self.prev_bq is not None:
            if abs(bid - self.prev_bid) < 1e-9 and bq > self.prev_bq:
                if t.trade_side == "SELL" or (self.prev_bq is not None and bq > self.prev_bq):
                    self.bid_replenish += 1
                    if t.trade_side == "SELL":
                        self.repl_after_exec += 1
                    if self.same_price_repl_since is None:
                        self.same_price_repl_since = t.ts
        if ask is not None and aq is not None and self.prev_ask is not None and self.prev_aq is not None:
            if abs(ask - self.prev_ask) < 1e-9 and aq > self.prev_aq:
                self.ask_replenish += 1

        # imbalance persistence (5s window proxy via recent hist)
        buy_v, sell_v = self._flow_sec(5.0)
        tot = buy_v + sell_v
        if tot > 0:
            if buy_v / tot >= 0.58:
                self.consec_buy_dom += 1
                self.consec_sell_dom = 0
                if self.buy_dom_since is None:
                    self.buy_dom_since = t.ts
                self.sell_dom_since = None
            elif sell_v / tot >= 0.58:
                self.consec_sell_dom += 1
                self.consec_buy_dom = 0
                if self.sell_dom_since is None:
                    self.sell_dom_since = t.ts
                self.buy_dom_since = None
            else:
                self.consec_buy_dom = 0
                self.consec_sell_dom = 0
                self.buy_dom_since = None
                self.sell_dom_since = None

        if spr is not None:
            if self.last_spread is not None and abs(spr - self.last_spread) < 2.0:
                if self.spread_stable_since is None:
                    self.spread_stable_since = t.ts
            else:
                self.spread_stable_since = t.ts
            self.last_spread = spr

        if t.trade_side == "BUY":
            self.buy_hits_streak += 1
            self.sell_hits_streak = 0
            if self.rise_start_px is None and px is not None:
                self.rise_start_px = px
        elif t.trade_side == "SELL":
            self.sell_hits_streak += 1
            self.buy_hits_streak = 0
            self.rise_start_px = None

        self.hist.append(_Evt(t.ts, px, bid, ask, t.trade_side, qty, bq, aq))
        self.prev_bid, self.prev_ask = bid, ask
        self.prev_bq, self.prev_aq = bq, aq
        self.last_side = t.trade_side

    def warmed(self, t: Tick) -> bool:
        if self.stream_start is None:
            return False
        return (t.ts - self.stream_start).total_seconds() >= WARMUP_SEC

    def _flow_sec(self, sec: float) -> tuple[float, float]:
        if not self.hist:
            return 0.0, 0.0
        t1 = self.hist[-1].ts
        buy = sell = 0.0
        for e in reversed(self.hist):
            if (t1 - e.ts).total_seconds() > sec:
                break
            if e.qty <= 0:
                continue
            if e.side == "BUY":
                buy += e.qty
            elif e.side == "SELL":
                sell += e.qty
        return buy, sell

    def _ret_sec(self, sec: float) -> Optional[float]:
        if not self.hist or self.hist[-1].px is None:
            return None
        t1 = self.hist[-1].ts
        px1 = self.hist[-1].px
        px0 = None
        for e in reversed(self.hist):
            if (t1 - e.ts).total_seconds() >= sec and e.px is not None:
                px0 = e.px
                break
            if e.px is not None:
                px0 = e.px
        if px0 is None or px0 <= 0:
            return None
        return (px1 - px0) / px0

    def _window_stats(self, sec: float) -> dict[str, Any]:
        if not self.hist:
            return {}
        t1 = self.hist[-1].ts
        buy_n = sell_n = up = down = 0
        buy_v = sell_v = 0.0
        prices = []
        ask_step = bid_step = bid_dn = ask_dn = 0
        ask_hits = bid_hits = 0
        hi_upd = lo_upd = 0
        prev_px = prev_ask = prev_bid = None
        for e in self.hist:
            if (t1 - e.ts).total_seconds() > sec:
                continue
            if e.px is not None:
                if prev_px is not None:
                    if e.px > prev_px:
                        up += 1
                    elif e.px < prev_px:
                        down += 1
                prev_px = e.px
                prices.append(e.px)
            if e.qty > 0:
                if e.side == "BUY":
                    buy_n += 1
                    buy_v += e.qty
                    ask_hits += 1
                elif e.side == "SELL":
                    sell_n += 1
                    sell_v += e.qty
                    bid_hits += 1
            if e.ask is not None and prev_ask is not None:
                if e.ask > prev_ask:
                    ask_step += 1
                elif e.ask < prev_ask:
                    ask_dn += 1
            if e.bid is not None and prev_bid is not None:
                if e.bid > prev_bid:
                    bid_step += 1
                elif e.bid < prev_bid:
                    bid_dn += 1
            prev_ask, prev_bid = e.ask, e.bid
        # high/low updates in window
        if prices:
            running_hi = prices[0]
            running_lo = prices[0]
            for p in prices[1:]:
                if p > running_hi:
                    hi_upd += 1
                    running_hi = p
                if p < running_lo:
                    lo_upd += 1
                    running_lo = p
        tot = buy_v + sell_v
        return {
            "buy_n": buy_n, "sell_n": sell_n, "buy_v": buy_v, "sell_v": sell_v,
            "buy_ratio": buy_v / tot if tot > 0 else None,
            "sell_ratio": sell_v / tot if tot > 0 else None,
            "up": up, "down": down, "prices": prices,
            "ask_step": ask_step, "bid_step": bid_step, "bid_dn": bid_dn, "ask_dn": ask_dn,
            "ask_hits": ask_hits, "bid_hits": bid_hits, "hi_upd": hi_upd, "lo_upd": lo_upd,
            "net_qty": buy_v - sell_v, "net_n": buy_n - sell_n,
            "hi": max(prices) if prices else None, "lo": min(prices) if prices else None,
        }

    def snapshot(self, t: Tick) -> dict[str, Optional[float]]:
        bid = t.board.canonical_best_bid
        ask = t.board.canonical_best_ask
        spr = t.board.canonical_spread_bps
        px = t.px if t.px is not None else ((bid + ask) / 2 if bid and ask else None)
        w5 = self._window_stats(5.0)
        w10 = self._window_stats(10.0)
        w30 = self._window_stats(30.0)
        w1 = self._window_stats(1.0)
        w3 = self._window_stats(3.0)

        # G1
        g1 = {
            "ret_1s": self._ret_sec(1.0),
            "ret_3s": self._ret_sec(3.0),
            "ret_5s": self._ret_sec(5.0),
            "ret_10s": self._ret_sec(10.0),
            "ret_30s": self._ret_sec(30.0),
            "ret_60s": self._ret_sec(60.0),
            "dist_recent_high": _safe_div((w30.get("hi") or px or 0) - (px or 0), px or 0) if px else None,
            "dist_recent_low": _safe_div((px or 0) - (w30.get("lo") or px or 0), px or 0) if px else None,
            "dist_day_high": _safe_div((self.day_high or px or 0) - (px or 0), px or 0) if px else None,
            "dist_day_low": _safe_div((px or 0) - (self.day_low or px or 0), px or 0) if px else None,
            "vwap_pos": _safe_div((px or 0) - (self.vwap_num / self.vwap_den if self.vwap_den else 0), px or 0) if px and self.vwap_den else None,
            "up_ticks_30": float(w30.get("up") or 0),
            "down_ticks_30": float(w30.get("down") or 0),
            "tick_velocity": float((w30.get("up") or 0) - (w30.get("down") or 0)),
            "range_30": _safe_div((w30.get("hi") or 0) - (w30.get("lo") or 0), px or 1) if px and w30.get("hi") else None,
            "spread_bps": spr,
        }

        # G2 — primary windows 5s and 10s (density-aware minimal set)
        def flow_feats(w: dict, pref: str) -> dict:
            buy_n, sell_n = w.get("buy_n") or 0, w.get("sell_n") or 0
            return {
                f"{pref}_buy_trade_count": float(buy_n),
                f"{pref}_sell_trade_count": float(sell_n),
                f"{pref}_buy_trade_qty": w.get("buy_v"),
                f"{pref}_sell_trade_qty": w.get("sell_v"),
                f"{pref}_buy_trade_ratio": w.get("buy_ratio"),
                f"{pref}_sell_trade_ratio": w.get("sell_ratio"),
                f"{pref}_buy_frequency": float(buy_n),
                f"{pref}_sell_frequency": float(sell_n),
                f"{pref}_net_aggressive_qty": w.get("net_qty"),
                f"{pref}_net_aggressive_count": float(w.get("net_n") or 0),
            }

        g2 = {}
        g2.update(flow_feats(w5, "w5"))
        g2.update(flow_feats(w10, "w10"))
        g2["buy_flow_accel"] = None if w5.get("buy_v") is None or w1.get("buy_v") is None else (w5["buy_v"] / 5.0) - (w1.get("buy_v") or 0)
        g2["sell_flow_accel"] = None if w5.get("sell_v") is None or w1.get("sell_v") is None else (w5["sell_v"] / 5.0) - (w1.get("sell_v") or 0)
        g2["consec_ask_hits"] = float(self.buy_hits_streak)
        g2["consec_bid_hits"] = float(self.sell_hits_streak)

        # G3 flow efficiency
        buy_v30, sell_v30 = w30.get("buy_v") or 0.0, w30.get("sell_v") or 0.0
        up30, dn30 = float(w30.get("up") or 0), float(w30.get("down") or 0)
        ask_hits = float(w30.get("ask_hits") or 0)
        bid_hits = float(w30.get("bid_hits") or 0)
        g3 = {
            "up_ticks_per_buy_qty": _safe_div(up30, buy_v30),
            "buy_qty_per_up_tick": _safe_div(buy_v30, up30),
            "down_ticks_per_sell_qty": _safe_div(dn30, sell_v30),
            "sell_qty_per_down_tick": _safe_div(sell_v30, dn30),
            "px_chg_bps_per_net_buy": _safe_div((self._ret_sec(30.0) or 0) * 10000.0, max(0.0, buy_v30 - sell_v30)) if (buy_v30 - sell_v30) > 0 else None,
            "px_chg_bps_per_net_sell": _safe_div(-(self._ret_sec(30.0) or 0) * 10000.0, max(0.0, sell_v30 - buy_v30)) if (sell_v30 - buy_v30) > 0 else None,
            "buy_ask_stepup_rate": _safe_div(float(w30.get("ask_step") or 0), ask_hits),
            "buy_bid_follow_rate": _safe_div(float(w30.get("bid_step") or 0), ask_hits),
            "sell_bid_stepdown_rate": _safe_div(float(w30.get("bid_dn") or 0), bid_hits),
            "sell_ask_follow_rate": _safe_div(float(w30.get("ask_dn") or 0), bid_hits),
            "ask_hit_high_update_rate": _safe_div(float(w30.get("hi_upd") or 0), ask_hits),
            "bid_hit_low_update_rate": _safe_div(float(w30.get("lo_upd") or 0), bid_hits),
            "buy_reaction_accel": _safe_div(up30, buy_v30) if buy_v30 else None,
            "sell_impact_decay": (
                None if (sell_v30 <= 0 or dn30 <= 0)
                else _safe_div(_safe_div(dn30, sell_v30) or 0, _safe_div(float(w10.get("down") or 0), w10.get("sell_v") or 1) or 1)
            ),
        }

        # G4 persistence — restored
        now = t.ts
        g4 = {
            "bid_survival_sec": (now - self.bid_level_since).total_seconds() if self.bid_level_since else None,
            "ask_survival_sec": (now - self.ask_level_since).total_seconds() if self.ask_level_since else None,
            "seconds_since_last_low": (now - self.last_low_ts).total_seconds() if self.last_low_ts else None,
            "seconds_since_last_high": (now - self.last_high_ts).total_seconds() if self.last_high_ts else None,
            "buy_imbalance_persistence_sec": (now - self.buy_dom_since).total_seconds() if self.buy_dom_since else 0.0,
            "sell_imbalance_persistence_sec": (now - self.sell_dom_since).total_seconds() if self.sell_dom_since else 0.0,
            "consecutive_buy_dominant_events": float(self.consec_buy_dom),
            "consecutive_sell_dominant_events": float(self.consec_sell_dom),
            "bid_follow_count": float(self.bid_follow),
            "ask_follow_count": float(self.ask_follow),
            "bid_replenishment_count": float(self.bid_replenish),
            "ask_replenishment_count": float(self.ask_replenish),
            "replenish_after_execution_count": float(self.repl_after_exec),
            "same_price_replenish_survival_sec": (
                (now - self.same_price_repl_since).total_seconds() if self.same_price_repl_since else None
            ),
            "high_update_interval": (sum(self.high_intervals) / len(self.high_intervals)) if self.high_intervals else None,
            "low_update_interval": (sum(self.low_intervals) / len(self.low_intervals)) if self.low_intervals else None,
            "spread_stability_sec": (now - self.spread_stable_since).total_seconds() if self.spread_stable_since else None,
        }

        # G5 market context
        g5 = {
            "watch50_up_ratio": self.breadth_up,
            "watch50_down_ratio": self.breadth_down,
            "watch50_median_return": self.median_ret,
            "symbol_minus_median_return": self.rel_ret,
            "return_percentile": self.ret_percentile,
            "flow_percentile": self.flow_percentile,
            "strength_rank": self.rank_strength,
            "mkt_buy_flow_breadth": self.mkt_buy_breadth,
            "mkt_sell_flow_breadth": self.mkt_sell_breadth,
            "index_return": None,  # unavailable
            "vs_index_return": None,
            "sector_return": None,
            "vs_sector_return": None,
        }

        # G6 remaining upside
        recent_hi = w30.get("hi")
        consumed = None
        if self.rise_start_px and px and self.rise_start_px > 0:
            consumed = (px - self.rise_start_px) / self.rise_start_px * 10000.0
        rem_to_hi = None
        if recent_hi and px and px > 0:
            rem_to_hi = (recent_hi - px) / px * 10000.0
        rem_day = None
        if self.day_high and px and px > 0:
            rem_day = (self.day_high - px) / px * 10000.0
        rng = None
        if w30.get("hi") and w30.get("lo") and px:
            lo, hi = w30["lo"], w30["hi"]
            rng = (px - lo) / (hi - lo) if hi > lo else None
        g6 = {
            "dist_to_recent_high_bps": rem_to_hi,
            "dist_to_day_high_bps": rem_day,
            "dist_to_resistance_proxy_bps": rem_to_hi,
            "dist_to_vwap_bps": (
                ((self.vwap_num / self.vwap_den) - px) / px * 10000.0
                if self.vwap_den and px else None
            ),
            "already_risen_bps": consumed,
            "pos_in_30s_range": rng,
            "remaining_vs_spread": _safe_div(rem_to_hi, spr) if rem_to_hi is not None and spr else None,
            "remaining_vs_5bps": _safe_div(rem_to_hi, COST_BPS) if rem_to_hi is not None else None,
            "recent_mfe_proxy_bps": (self._ret_sec(30.0) or 0) * 10000.0 if self._ret_sec(30.0) is not None else None,
            "consumed_upside_bps_short": consumed,
        }

        out: dict[str, Optional[float]] = {}
        out.update({f"G1_{k}": v for k, v in g1.items()})
        out.update({f"G2_{k}": v for k, v in g2.items()})
        out.update({f"G3_{k}": v for k, v in g3.items()})
        out.update({f"G4_{k}": v for k, v in g4.items()})
        out.update({f"G5_{k}": v for k, v in g5.items()})
        out.update({f"G6_{k}": v for k, v in g6.items()})
        # unused small windows kept for density note
        _ = (w3,)
        return out


GROUP_PREFIX = {
    "G1": "G1_",
    "G2": "G2_",
    "G3": "G3_",
    "G4": "G4_",
    "G5": "G5_",
    "G6": "G6_",
}


def features_for_groups(feats: dict[str, Optional[float]], groups: Sequence[str]) -> dict[str, Optional[float]]:
    if not groups:
        return {}
    prefs = [GROUP_PREFIX[g] for g in groups]
    return {k: v for k, v in feats.items() if any(k.startswith(p) for p in prefs)}
