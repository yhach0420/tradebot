"""Spec 1.2 FCRR state machine — quantile thresholds, F1/F2 flow profiles."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .config_v12 import FLOW_PROFILE, RETENTION_SEC, STRUCTURAL


def _tick_size_approx(price: float) -> float:
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


def _finite(x: Any) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return v == v and abs(v) != float("inf")


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


@dataclass
class MachineV12:
    symbol: str
    candidate_id: str
    thresholds: dict[str, Any]
    state: str = "IDLE"
    episode: Optional[Episode] = None
    next_episode_id: int = 1
    prev_mid: float = float("nan")
    last_step_tos: list[str] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    _retain_transitions: bool = False

    @property
    def retention_sec(self) -> float:
        return float(RETENTION_SEC[self.candidate_id])

    @property
    def flow_profile(self) -> str:
        return FLOW_PROFILE[self.candidate_id]

    def _emit(self, t: float, frm: str, to: str, reason: str, feats: dict) -> None:
        self.last_step_tos.append(to)
        if self._retain_transitions:
            self.transitions.append({
                "t": t, "symbol": self.symbol, "candidate_id": self.candidate_id,
                "from": frm, "to": to, "reason": reason,
                "asof_time": feats.get("asof_time"),
                "episode_id": None if self.episode is None else self.episode.episode_id,
                "mid": feats.get("mid"),
                "micro_high": None if self.episode is None else self.episode.micro_high,
            })
        self.state = to

    def _invalidate(self, t: float, reason: str, feats: dict) -> None:
        frm = self.state
        self._emit(t, frm, "INVALIDATED", reason, feats)
        self.episode = None
        self._emit(t, "INVALIDATED", "IDLE", "RESET", feats)

    def notify_cap_blocked(self, t: float) -> None:
        if self.episode is not None:
            self.episode.entry_emitted = True
            self.episode.entry_emitted_at = t
            self.state = "EPISODE_LOCKED"

    def observe(self, t: float, feats: dict[str, Any]) -> Optional[dict[str, Any]]:
        self.last_step_tos.clear()
        if not feats.get("complete"):
            if self.state not in ("IDLE", "EPISODE_LOCKED") and feats.get("reason") in (
                "STALE", "FLOW_DATA_INCOMPLETE", "BAD_QUOTE", "VWAP_MISSING",
            ):
                self._invalidate(t, feats.get("reason") or "QUALITY_FAIL", feats)
            return None
        if self.state == "EPISODE_LOCKED":
            if feats.get("mid") is not None:
                self.prev_mid = float(feats["mid"])
            return None

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
                ep.pullback_start_t = t
                self._emit(t, "CONTEXT_READY", "PULLBACK_ACTIVE", "PULLBACK_START", feats)
                advanced = True

        elif self.state == "PULLBACK_ACTIVE" and not advanced:
            assert self.episode is not None
            ep = self.episode
            mid = float(feats["mid"])
            if mid < ep.pullback_low - 1e-12:
                ep.pullback_low = mid
                ep.pullback_low_t = t
            if mid > ep.pullback_low + 1e-12:
                if (not ep.micro_high_frozen) and (
                    (ep.micro_high != ep.micro_high) or mid > ep.micro_high
                ):
                    ep.micro_high = mid
            geo_ok, why = self._pullback_ok(t, feats, ep)
            if why.startswith("REJECT_"):
                self._invalidate(t, why, feats)
                return None
            if geo_ok and self._selling_exhausted(t, feats, ep):
                if not _finite(ep.micro_high):
                    ep.micro_high = mid
                ep.micro_high_frozen = True
                self._emit(t, "PULLBACK_ACTIVE", "SELLING_EXHAUSTED", "SELLING_EXHAUSTED", feats)
                advanced = True

        elif self.state == "SELLING_EXHAUSTED" and not advanced:
            assert self.episode is not None
            ep = self.episode
            mid = float(feats["mid"])
            if mid < ep.pullback_low - 1e-12:
                self._invalidate(t, "PULLBACK_LOW_UPDATED", feats)
                return None
            if self._reclaim_crossed(feats, ep):
                ep.reclaim_t = t
                ep.reclaim_mid = mid
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

        elif self.state == "RETENTION_CONFIRMED" and not advanced:
            assert self.episode is not None
            ep = self.episode
            if ep.entry_emitted:
                self._emit(t, "RETENTION_CONFIRMED", "EPISODE_LOCKED", "ALREADY_EMITTED", feats)
                return None
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
                "pullback_low": ep.pullback_low,
                "entry_reason": "E1_X6_FCRR",
                "asof_time": feats.get("asof_time"),
            }
            if feats.get("mid") is not None:
                self.prev_mid = float(feats["mid"])
            return entry

        if feats.get("mid") is not None:
            self.prev_mid = float(feats["mid"])
        return None

    def _context_ready(self, f: dict) -> bool:
        """Spec 1.2: <=2 price context features + 1 tradeability guard."""
        th = self.thresholds["context"]
        need = [f.get("mid"), f.get("atr_180s"), f.get("spread_bps"), f.get("active_volume_windows_120s")]
        if any(v is None or (isinstance(v, float) and v != v) for v in need):
            return False
        if f["atr_180s"] <= 0:
            return False
        # price_context_1: ret_180s >= q*
        r = f.get("ret_180s")
        if r is None or r < th["ret_180s_min"] - 1e-12:
            return False
        # optional price_context_2: distance_from_session_high <= q* (negative direction)
        d = f.get("distance_from_session_high")
        if d is None or d > th["dist_high_max"] + 1e-12:
            return False
        # tradeability guard
        if f["spread_bps"] > th["spread_bps_max"] + 1e-12:
            return False
        if f["active_volume_windows_120s"] < th["active_windows_min"]:
            return False
        return True

    def _pullback_ok(self, t: float, f: dict, ep: Episode) -> tuple[bool, str]:
        th = self.thresholds["pullback"]
        atr = f.get("atr_180s")
        if atr is None or atr <= 0:
            return False, "ATR_MISSING"
        if not _finite(ep.pullback_low) or not _finite(ep.context_high):
            return False, "PB_UNSET"
        depth = (ep.context_high - ep.pullback_low) / atr
        start = ep.pullback_start_t if _finite(ep.pullback_start_t) else ep.pullback_low_t
        dur = t - start if _finite(start) else 0.0
        # hard reject: no real decline
        if depth <= 0:
            return False, "REJECT_NO_DECLINE"
        if f.get("spread_bps") is not None and f["spread_bps"] > th["spread_bps_max"] + 1e-12:
            return False, "REJECT_SPREAD"
        # primary band on depth
        if depth < th["depth_lo"] - 1e-12 or depth > th["depth_hi"] + 1e-12:
            if depth < th["depth_lo"]:
                return False, "DEPTH_OUT_OF_BAND_LOW"
            return False, "REJECT_DEPTH_DEEP"
        # optional duration guard (soft min only)
        if dur + 1e-9 < th["duration_min"]:
            return False, "DURATION_SHORT"
        if dur > th["duration_max"] + 1e-9:
            return False, "REJECT_DURATION_LONG"
        return True, "OK"

    def _selling_exhausted(self, t: float, f: dict, ep: Episode) -> bool:
        th = self.thresholds["exhaustion"]
        if not _finite(ep.pullback_low_t):
            return False
        # primary: low update stopped
        if t - ep.pullback_low_t + 1e-9 < th["no_new_low_sec"]:
            return False
        # secondary (exactly one evidence type, precommitted): ret deceleration
        r15, r30 = f.get("ret_15s"), f.get("ret_30s")
        if r15 is None or r30 is None:
            return False
        if not (float(r15) - float(r30) >= th["ret_diff_min"] - 1e-12):
            return False
        if f.get("spread_bps") is not None and f["spread_bps"] > th["spread_bps_max"] + 1e-12:
            return False
        return True

    def _reclaim_crossed(self, f: dict, ep: Episode) -> bool:
        th = self.thresholds["reclaim"]
        if not _finite(ep.micro_high):
            return False
        mid = f.get("mid")
        if mid is None:
            return False
        if _finite(self.prev_mid) and not (self.prev_mid <= ep.micro_high + 1e-12):
            return False
        if not (float(mid) > ep.micro_high + 1e-12):
            return False
        tick = _tick_size_approx(ep.micro_high)
        if f.get("bid") is None or float(f["bid"]) < ep.micro_high - tick - 1e-12:
            return False

        med10 = f.get("median_active_volume_10s_120s")
        med30 = f.get("median_active_volume_30s_300s")
        vol10, vol30 = f.get("volume_10s"), f.get("volume_30s")
        if med10 is None or med10 <= 0 or med30 is None or med30 <= 0:
            return False
        if vol10 is None or vol30 is None:
            return False
        if f.get("active_10s_windows_120s", 0) < th["active_10s_min"]:
            return False
        if f.get("active_30s_windows_300s", 0) < th["active_30s_min"]:
            return False

        vol_impulse = (vol10 / med10 >= th["vol10_ratio_min"] - 1e-12) and (
            vol30 / med30 >= th["vol30_ratio_min"] - 1e-12
        ) and (vol30 >= th["volume_abs_floor"] - 1e-12)
        uptick_ok = (
            f.get("uptick_volume_ratio_30s") is not None
            and float(f["uptick_volume_ratio_30s"]) >= th["uptick_ratio_min"] - 1e-12
        )
        pu10 = f.get("price_update_count_10s")
        pu_med = f.get("median_price_update_count_10s_120s")
        accel_ok = pu10 is not None and pu_med is not None and float(pu10) > float(pu_med)
        spread_ok = f.get("spread_bps") is not None and float(f["spread_bps"]) <= th["spread_bps_max"] + 1e-12

        if self.flow_profile == "F1":
            votes = sum([vol_impulse, uptick_ok, accel_ok])
            return votes >= STRUCTURAL["f1_min_of_three"] and spread_ok
        # F2: both volume + uptick, plus spread_not_widening
        return bool(vol_impulse and uptick_ok and spread_ok)

    def _retention_ok(self, f: dict, ep: Episode) -> bool:
        th = self.thresholds["retention"]
        tick = _tick_size_approx(ep.micro_high)
        if f["mid"] < ep.micro_high - tick - 1e-12:
            return False
        if f["bid"] < ep.micro_high - tick - 1e-12:
            return False
        # one continuation evidence (not all AND)
        evidences = []
        evidences.append(bool(ep.new_high_after_cross))
        evidences.append(bool(ep.reclaim_mid > ep.micro_high + 1e-12))
        ur = f.get("uptick_volume_ratio_10s")
        evidences.append(ur is not None and float(ur) >= th["uptick_10s_min"] - 1e-12)
        vol10 = f.get("volume_10s")
        evidences.append(vol10 is not None and float(vol10) > 0)
        if f.get("spread_bps") is not None and float(f["spread_bps"]) > th["spread_bps_max"] + 1e-12:
            return False
        return any(evidences)

    def _check_invalidate(self, t: float, f: dict) -> Optional[str]:
        ep = self.episode
        if ep is None:
            return None
        if t - ep.started_at > 1800.0:
            return "MAX_EPISODE_TIME"
        atr = f.get("atr_180s")
        vwap = f.get("vwap")
        mid = f.get("mid")
        if atr and vwap and mid is not None and atr > 0:
            if mid < vwap - 0.25 * atr:
                return "VWAP_BREAK"
        if f.get("spread_bps") is not None and f["spread_bps"] > 15.0:
            return "SPREAD_SPIKE"
        return None
