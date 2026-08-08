"""FCRR state machine — one advance per observation; no same-event cross+ENTRY."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .config import RETENTION_SEC, THRESHOLDS


def _tick_size_approx(price: float) -> float:
    """Lightweight JPX-ish tick for micro-high tolerance (research-only)."""
    p = float(price)
    if p <= 1000:
        return 0.1
    if p <= 3000:
        return 0.5
    if p <= 5000:
        return 1.0
    if p <= 10000:
        return 1.0
    if p <= 30000:
        return 5.0
    if p <= 50000:
        return 10.0
    return 50.0


@dataclass
class Episode:
    episode_id: int
    started_at: float
    context_high: float = float("nan")
    pullback_low: float = float("nan")
    pullback_low_t: float = float("nan")
    pullback_start_t: float = float("nan")
    micro_high: float = float("nan")
    micro_high_frozen: bool = False
    reclaim_t: float = float("nan")
    reclaim_mid: float = float("nan")
    new_high_after_cross: bool = False
    entry_emitted: bool = False
    entry_emitted_at: float = float("nan")
    prev_spread_at_cross: float = float("nan")


@dataclass
class Machine:
    symbol: str
    candidate_id: str
    state: str = "IDLE"
    episode: Optional[Episode] = None
    next_episode_id: int = 1
    # selling exhaustion trackers
    last_pullback_low: float = float("nan")
    last_pullback_low_t: float = float("nan")
    bid_down_streak: int = 0
    last_bid: float = float("nan")
    prev_mid: float = float("nan")
    transitions: list[dict[str, Any]] = field(default_factory=list)
    volume_abs_floor: float = 0.0  # fit-period q50; 0 means unset → reclaim cannot PASS
    _retain_transitions: bool = False
    last_step_tos: list[str] = field(default_factory=list)

    @property
    def retention_sec(self) -> float:
        return float(RETENTION_SEC[self.candidate_id])

    def _emit(self, t: float, frm: str, to: str, reason: str, feats: dict) -> None:
        self.last_step_tos.append(to)
        if self._retain_transitions:
            self.transitions.append({
                "t": t, "symbol": self.symbol, "candidate_id": self.candidate_id,
                "from": frm, "to": to, "reason": reason,
                "asof_time": feats.get("asof_time"),
                "episode_id": None if self.episode is None else self.episode.episode_id,
                "mid": feats.get("mid"), "micro_high": None if self.episode is None else self.episode.micro_high,
            })
        self.state = to

    def _invalidate(self, t: float, reason: str, feats: dict) -> None:
        frm = self.state
        self._emit(t, frm, "INVALIDATED", reason, feats)
        self.episode = None
        self._emit(t, "INVALIDATED", "IDLE", "RESET", feats)

    def observe(self, t: float, feats: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Process one evaluation observation. Returns ENTRY signal dict or None.

        Guarantees: at most one state advance; ENTRY never on reclaim cross event.
        """
        self.last_step_tos.clear()
        if not feats.get("complete"):
            # do not advance on incomplete; may invalidate if stale mid-episode
            if self.state not in ("IDLE", "EPISODE_LOCKED") and feats.get("reason") in (
                "STALE", "FLOW_DATA_INCOMPLETE", "BAD_QUOTE", "VWAP_MISSING",
            ):
                self._invalidate(t, feats.get("reason") or "QUALITY_FAIL", feats)
            return None

        # EPISODE_LOCKED: wait for new episode conditions (handled via IDLE re-entry rules)
        if self.state == "EPISODE_LOCKED":
            if feats.get("mid") is not None:
                self.prev_mid = float(feats["mid"])
            return None

        # Check global invalidators while in active episode
        if self.state not in ("IDLE",) and self.episode is not None:
            inv = self._check_invalidate(t, feats)
            if inv:
                self._invalidate(t, inv, feats)
                if feats.get("mid") is not None:
                    self.prev_mid = float(feats["mid"])
                return None

        advanced = False
        entry: Optional[dict[str, Any]] = None

        if self.state == "IDLE":
            if self._context_ready(feats):
                self.episode = Episode(
                    episode_id=self.next_episode_id,
                    started_at=t,
                    context_high=float(feats["mid"]),
                )
                self.next_episode_id += 1
                self.last_pullback_low = float("nan")
                self.last_pullback_low_t = float("nan")
                self.bid_down_streak = 0
                self._emit(t, "IDLE", "CONTEXT_READY", "CONTEXT_OK", feats)
                advanced = True

        elif self.state == "CONTEXT_READY" and not advanced:
            assert self.episode is not None
            ep = self.episode
            mid = float(feats["mid"])
            if mid > ep.context_high:
                ep.context_high = mid
            if mid < ep.context_high - 1e-12:
                ep.pullback_low = mid
                ep.pullback_low_t = t
                ep.pullback_start_t = t  # type: ignore[attr-defined]
                self.last_pullback_low = mid
                self.last_pullback_low_t = t
                self._emit(t, "CONTEXT_READY", "PULLBACK_ACTIVE", "PULLBACK_START", feats)
                advanced = True

        elif self.state == "PULLBACK_ACTIVE" and not advanced:
            assert self.episode is not None
            ep = self.episode
            mid = float(feats["mid"])
            if mid < ep.pullback_low - 1e-12:
                ep.pullback_low = mid
                ep.pullback_low_t = t
                self.last_pullback_low = mid
                self.last_pullback_low_t = t
            if mid > ep.pullback_low + 1e-12:
                if (not ep.micro_high_frozen) and (
                    (ep.micro_high != ep.micro_high) or mid > ep.micro_high
                ):
                    ep.micro_high = mid
            geo_ok, why = self._pullback_geometry(t, feats, ep)
            if why.startswith("REJECT_"):
                self._invalidate(t, why, feats)
                return None
            # advance to SE only when geometry OK and selling exhausted
            if geo_ok and self._selling_exhausted(t, feats, ep):
                if not (ep.micro_high == ep.micro_high):
                    ep.micro_high = mid
                ep.micro_high_frozen = True
                self._emit(t, "PULLBACK_ACTIVE", "SELLING_EXHAUSTED", "SELLING_EXHAUSTED", feats)
                advanced = True

        elif self.state == "SELLING_EXHAUSTED" and not advanced:
            assert self.episode is not None
            ep = self.episode
            mid = float(feats["mid"])
            bid = float(feats["bid"])
            if math_isfinite(self.last_bid) and bid < self.last_bid - 1e-12:
                self.bid_down_streak += 1
            else:
                self.bid_down_streak = 0
            self.last_bid = bid
            if mid < ep.pullback_low - 1e-12:
                self._invalidate(t, "PULLBACK_LOW_UPDATED", feats)
                return None
            # reclaim on a later observation only (SE already established prior)
            if self._reclaim_crossed(feats, ep):
                # require previous mid was <= micro_high: approximate via not already above for long
                ep.reclaim_t = t
                ep.reclaim_mid = mid
                ep.prev_spread_at_cross = float(feats["spread_bps"])
                self._emit(t, "SELLING_EXHAUSTED", "RECLAIM_CROSSED", "RECLAIM_OK", feats)
                advanced = True

        elif self.state == "RECLAIM_CROSSED" and not advanced:
            assert self.episode is not None
            ep = self.episode
            mid = float(feats["mid"])
            tick = _tick_size_approx(ep.micro_high)
            if mid < ep.micro_high - tick - 1e-12:
                self._invalidate(t, "RETENTION_MID_BROKEN", feats)
                return None
            if float(feats["bid"]) < ep.micro_high - tick - 1e-12:
                self._invalidate(t, "RETENTION_BID_BROKEN", feats)
                return None
            if mid > ep.reclaim_mid + 1e-12:
                ep.new_high_after_cross = True
                ep.reclaim_mid = mid
            elapsed = t - ep.reclaim_t
            if elapsed + 1e-9 >= self.retention_sec and self._retention_ok(feats, ep):
                self._emit(t, "RECLAIM_CROSSED", "RETENTION_CONFIRMED", "RETENTION_OK", feats)
                advanced = True
            # ENTRY forbidden on this same event even if retention just confirmed

        elif self.state == "RETENTION_CONFIRMED" and not advanced:
            assert self.episode is not None
            ep = self.episode
            # ENTRY only on a subsequent observation after RETENTION_CONFIRMED
            if ep.entry_emitted:
                self._emit(t, "RETENTION_CONFIRMED", "EPISODE_LOCKED", "ALREADY_EMITTED", feats)
                return None
            # emit entry once
            ep.entry_emitted = True
            ep.entry_emitted_at = t
            self._emit(t, "RETENTION_CONFIRMED", "ENTRY_EMITTED", "ENTRY", feats)
            self._emit(t, "ENTRY_EMITTED", "EPISODE_LOCKED", "LOCK", feats)
            entry = {
                "symbol": self.symbol,
                "candidate_id": self.candidate_id,
                "t": t,
                "entry_ask": float(feats["ask"]),
                "bid": float(feats["bid"]),
                "mid": float(feats["mid"]),
                "episode_id": ep.episode_id,
                "micro_high": ep.micro_high,
                "entry_reason": "E1_X6_FCRR",
                "asof_time": feats.get("asof_time"),
            }
            if feats.get("mid") is not None:
                self.prev_mid = float(feats["mid"])
            return entry

        if feats.get("mid") is not None:
            self.prev_mid = float(feats["mid"])
        return None

    def notify_cap_blocked(self, t: float) -> None:
        """CAP blocked still consumes the episode ENTRY slot."""
        if self.episode is not None:
            self.episode.entry_emitted = True
            self.episode.entry_emitted_at = t
            if self.state != "EPISODE_LOCKED":
                self.state = "EPISODE_LOCKED"

    def _context_ready(self, f: dict) -> bool:
        c = THRESHOLDS["context"]
        need = [
            f.get("mid"), f.get("vwap"), f.get("ret_180s"), f.get("linear_slope_180s"),
            f.get("distance_from_session_high"), f.get("distance_above_vwap"),
            f.get("spread_bps"), f.get("price_update_count_60s"),
            f.get("active_volume_windows_120s"), f.get("atr_180s"),
        ]
        if any(v is None or (isinstance(v, float) and v != v) for v in need):
            return False
        if not (f["mid"] > f["vwap"]):
            return False
        if not (f["ret_180s"] > 0):
            return False
        if not (f["linear_slope_180s"] > 0):
            return False
        if f["distance_from_session_high"] > c["distance_from_session_high_atr_max"] + 1e-12:
            return False
        if f["distance_above_vwap"] > c["distance_above_vwap_atr_max"] + 1e-12:
            return False
        if f["spread_bps"] > c["spread_bps_max"] + 1e-12:
            return False
        if f["price_update_count_60s"] < c["price_update_count_60s_min"]:
            return False
        if f["active_volume_windows_120s"] < c["active_volume_windows_120s_min"]:
            return False
        return True

    def _pullback_geometry(self, t: float, f: dict, ep: Episode) -> tuple[bool, str]:
        p = THRESHOLDS["pullback"]
        atr = f.get("atr_180s")
        if atr is None or atr <= 0:
            return False, "ATR_MISSING"
        depth = (ep.context_high - ep.pullback_low) / atr
        start = ep.pullback_start_t if math_isfinite(ep.pullback_start_t) else ep.pullback_low_t
        dur = t - start if math_isfinite(start) else 0.0
        if depth > p["depth_atr_max"] + 1e-12:
            return False, "REJECT_DEPTH_DEEP"
        if dur > p["duration_sec_max"] + 1e-9:
            return False, "REJECT_DURATION_LONG"
        floor = f["vwap"] + p["pullback_low_vwap_atr_floor"] * atr
        if ep.pullback_low < floor - 1e-12:
            return False, "REJECT_BELOW_VWAP"
        if f["spread_bps"] > p["spread_bps_max"] + 1e-12:
            return False, "REJECT_SPREAD"
        r15, r30 = f.get("ret_15s"), f.get("ret_30s")
        if r15 is not None and r30 is not None and r15 < r30 - 1e-12 and r15 < 0:
            return False, "REJECT_DOWN_ACCEL"
        if depth < p["depth_atr_min"] - 1e-12:
            return False, "DEPTH_SHALLOW"
        if dur + 1e-9 < p["duration_sec_min"]:
            return False, "DURATION_SHORT"
        return True, "OK"

    def _selling_exhausted(self, t: float, f: dict, ep: Episode) -> bool:
        s = THRESHOLDS["selling_exhausted"]
        if not math_isfinite(ep.pullback_low_t):
            return False
        if t - ep.pullback_low_t + 1e-9 < s["no_new_low_sec"]:
            return False
        # no new low in last 30s
        if abs(ep.pullback_low - self.last_pullback_low) > 1e-12:
            # updated recently
            if math_isfinite(self.last_pullback_low_t) and t - self.last_pullback_low_t < s["no_new_low_sec"]:
                return False
        r15, r30 = f.get("ret_15s"), f.get("ret_30s")
        if r15 is None or r30 is None or not (r15 >= r30 - 1e-12):
            return False
        d15, d60 = f.get("down_tick_volume_ratio_15s"), f.get("down_tick_volume_ratio_60s")
        if d15 is None or d60 is None or not (d15 < d60 - 1e-12):
            return False
        if f["spread_bps"] > s["spread_bps_max"] + 1e-12:
            return False
        if self.bid_down_streak >= 2:
            return False
        return True

    def _reclaim_crossed(self, f: dict, ep: Episode) -> bool:
        r = THRESHOLDS["reclaim"]
        if not math_isfinite(ep.micro_high):
            return False
        need = [
            f.get("mid"), f.get("bid"), f.get("volume_10s"), f.get("volume_30s"),
            f.get("median_active_volume_10s_120s"), f.get("median_active_volume_30s_300s"),
            f.get("active_10s_windows_120s"), f.get("active_30s_windows_300s"),
            f.get("uptick_volume_ratio_30s"), f.get("price_update_count_10s"),
            f.get("median_price_update_count_10s_120s"), f.get("spread_bps"),
        ]
        if any(v is None for v in need):
            return False
        # prior mid <= micro_high implied by being below; require current cross
        # We don't have previous_mid in feats — use: was not above, now above.
        # Approximate: mid just crossed => mid > micro_high and bid support
        mid = f["mid"]
        if math_isfinite(self.prev_mid) and not (self.prev_mid <= ep.micro_high + 1e-12):
            return False
        if not (mid > ep.micro_high + 1e-12):
            return False
        tick = _tick_size_approx(ep.micro_high)
        if f["bid"] < ep.micro_high - tick - 1e-12:
            return False
        if f["active_10s_windows_120s"] < r["active_10s_windows_120s_min"]:
            return False
        if f["active_30s_windows_300s"] < r["active_30s_windows_300s_min"]:
            return False
        med10 = f["median_active_volume_10s_120s"]
        med30 = f["median_active_volume_30s_300s"]
        if med10 is None or med10 <= 0 or med30 is None or med30 <= 0:
            return False  # denominator 0 → never PASS
        if f["volume_10s"] / med10 < r["vol10_ratio_min"] - 1e-12:
            return False
        if f["volume_30s"] / med30 < r["vol30_ratio_min"] - 1e-12:
            return False
        if self.volume_abs_floor > 0 and f["volume_30s"] < self.volume_abs_floor - 1e-12:
            return False
        if self.volume_abs_floor <= 0:
            return False  # unset floor → do not PASS
        if f["uptick_volume_ratio_30s"] < r["uptick_volume_ratio_30s_min"] - 1e-12:
            return False
        if f["price_update_count_10s"] <= f["median_price_update_count_10s_120s"]:
            return False
        if f["spread_bps"] > r["spread_bps_max"] + 1e-12:
            return False
        return True

    def _retention_ok(self, f: dict, ep: Episode) -> bool:
        r = THRESHOLDS["retention"]
        tick = _tick_size_approx(ep.micro_high)
        if f["mid"] < ep.micro_high - tick - 1e-12:
            return False
        if f["bid"] < ep.micro_high - tick - 1e-12:
            return False
        if r["require_new_high_after_cross"] and not ep.new_high_after_cross:
            return False
        if r["require_cross_return_gt_0"]:
            if ep.reclaim_mid <= ep.micro_high + 1e-12 and not ep.new_high_after_cross:
                return False
        if r["volume_10s_must_be_nonzero"] and (f.get("volume_10s") is None or f["volume_10s"] <= 0):
            return False
        ur = f.get("uptick_volume_ratio_10s")
        if ur is None or ur < r["uptick_volume_ratio_10s_min"] - 1e-12:
            return False
        if f["spread_bps"] > r["spread_bps_max"] + 1e-12:
            return False
        return True

    def _check_invalidate(self, t: float, f: dict) -> Optional[str]:
        q = THRESHOLDS["quality"]
        ep = self.episode
        if ep is None:
            return None
        if t - ep.started_at > THRESHOLDS["episode"]["max_episode_sec"]:
            return "MAX_EPISODE_TIME"
        atr = f.get("atr_180s")
        vwap = f.get("vwap")
        mid = f.get("mid")
        if atr and vwap and mid is not None and atr > 0:
            if mid < vwap - 0.25 * atr:
                return "VWAP_BREAK"
        if f.get("spread_bps") is not None and f["spread_bps"] > q.get("spread_bps_spike_mult", 2.0) * 5.0:
            return "SPREAD_SPIKE"
        return None


def math_isfinite(x: float) -> bool:
    return x == x and abs(x) != float("inf")
