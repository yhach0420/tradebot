"""Causal 5-minute bars from native 1-minute data.

LIVE_CAUSAL SMA uses prior completed 5m closes plus the current 1m close
as the provisional close of the forming 5m bar.

CONFIRMED SMA uses completed 5m bars only.

No lunch synthetic bars. No overnight synthetic bars.
Prior-session completed 5m closes remain in the MA window.
Never uses a 5m close that does not yet exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1 import SMA25, SMA5, SMA75
from research.one_minute_native_playbook_discovery_v1.states import to_min


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def bucket_start_min(t: str) -> int | None:
    m = to_min(t)
    if m is None:
        return None
    return (int(m) // 5) * 5


def is_clock_last_minute(t: str) -> bool:
    m = to_min(t)
    if m is None:
        return False
    return int(m) % 5 == 4


def _sma(closes: list[float], n: int) -> float:
    if n <= 0 or len(closes) < n:
        return float("nan")
    xs = closes[-n:]
    if any(not _finite(v) for v in xs):
        return float("nan")
    return float(sum(xs) / float(n))


@dataclass
class FiveMinChart:
    """Continuous 5-minute chart SMA. History spans sessions. No lunch fill."""

    completed_closes: list[float] = field(default_factory=list)
    forming_key: int | None = None
    forming_c: float | None = None
    forming_o: float | None = None
    forming_h: float | None = None
    forming_l: float | None = None
    future_5m_close_usage_n: int = 0
    lunch_synthetic_n: int = 0
    overnight_synthetic_n: int = 0
    completed_n: int = 0
    live_sma_defined_n: int = 0
    confirmed_sma_defined_n: int = 0
    live_eq_confirmed_n: int = 0
    live_ne_confirmed_n: int = 0

    def _start_forming(self, key: int, o: float, h: float, l: float, c: float) -> None:
        self.forming_key = key
        self.forming_o = o
        self.forming_h = h
        self.forming_l = l
        self.forming_c = c

    def _update_forming(self, o: float, h: float, l: float, c: float) -> None:
        if not _finite(self.forming_o) and _finite(o):
            self.forming_o = o
        if _finite(h):
            self.forming_h = h if not _finite(self.forming_h) else max(float(self.forming_h), float(h))
        if _finite(l):
            self.forming_l = l if not _finite(self.forming_l) else min(float(self.forming_l), float(l))
        if _finite(c):
            self.forming_c = c

    def _complete_forming(self) -> None:
        if self.forming_key is None or not _finite(self.forming_c):
            self.forming_key = None
            self.forming_c = None
            return
        self.completed_closes.append(float(self.forming_c))
        self.completed_n += 1
        if len(self.completed_closes) > 400:
            self.completed_closes = self.completed_closes[-200:]
        self.forming_key = None
        self.forming_c = None
        self.forming_o = None
        self.forming_h = None
        self.forming_l = None

    def on_minute(self, t: str, o: Any, h: Any, l: Any, c: Any) -> dict[str, Any]:
        """Advance one completed 1m bar. Uses only this close as the current provisional 5m close."""
        if in_lunch(t):
            return {
                "skipped_lunch": True,
                "live_sma5": float("nan"),
                "live_sma25": float("nan"),
                "live_sma75": float("nan"),
                "confirmed_sma5": float("nan"),
                "confirmed_sma25": float("nan"),
                "confirmed_sma75": float("nan"),
                "bar_complete": False,
                "provisional": True,
            }
        key = bucket_start_min(t)
        of = float(o) if _finite(o) else float("nan")
        hf = float(h) if _finite(h) else float("nan")
        lf = float(l) if _finite(l) else float("nan")
        cf = float(c) if _finite(c) else float("nan")
        if key is None or not _finite(cf):
            return {
                "skipped_lunch": False,
                "live_sma5": float("nan"),
                "live_sma25": float("nan"),
                "live_sma75": float("nan"),
                "confirmed_sma5": float("nan"),
                "confirmed_sma25": float("nan"),
                "confirmed_sma75": float("nan"),
                "bar_complete": False,
                "provisional": True,
            }
        if self.forming_key is not None and int(key) != int(self.forming_key):
            self._complete_forming()
        if self.forming_key is None:
            self._start_forming(int(key), of, hf, lf, cf)
        else:
            self._update_forming(of, hf, lf, cf)
        bar_complete = bool(is_clock_last_minute(t))
        live_src = list(self.completed_closes)
        if _finite(self.forming_c):
            live_src.append(float(self.forming_c))
        confirmed_src = list(self.completed_closes)
        if bar_complete and _finite(self.forming_c):
            confirmed_src.append(float(self.forming_c))
        live5 = _sma(live_src, int(SMA5))
        live25 = _sma(live_src, int(SMA25))
        live75 = _sma(live_src, int(SMA75))
        conf5 = _sma(confirmed_src, int(SMA5))
        conf25 = _sma(confirmed_src, int(SMA25))
        conf75 = _sma(confirmed_src, int(SMA75))
        if _finite(live5) and _finite(live25) and _finite(live75):
            self.live_sma_defined_n += 1
        if _finite(conf5) and _finite(conf25) and _finite(conf75):
            self.confirmed_sma_defined_n += 1
        if _finite(live5) and _finite(conf5):
            if abs(float(live5) - float(conf5)) < 1e-12:
                self.live_eq_confirmed_n += 1
            else:
                self.live_ne_confirmed_n += 1
        if bar_complete:
            self._complete_forming()
        return {
            "skipped_lunch": False,
            "bucket_start_min": int(key),
            "bar_complete": bar_complete,
            "provisional": not bar_complete,
            "live_sma5": live5,
            "live_sma25": live25,
            "live_sma75": live75,
            "confirmed_sma5": conf5,
            "confirmed_sma25": conf25,
            "confirmed_sma75": conf75,
            "completed_n": int(self.completed_n),
            "forming_c": float(cf),
        }

    def close_session(self) -> None:
        """Complete a leftover forming bar at session end. No synthetic price."""
        if self.forming_key is not None and _finite(self.forming_c):
            self._complete_forming()

    def audit(self) -> dict[str, Any]:
        return {
            "FUTURE_5M_CLOSE_USAGE_N": int(self.future_5m_close_usage_n),
            "lunch_synthetic_n": int(self.lunch_synthetic_n),
            "overnight_synthetic_n": int(self.overnight_synthetic_n),
            "completed_5m_n": int(self.completed_n),
            "live_sma_defined_n": int(self.live_sma_defined_n),
            "confirmed_sma_defined_n": int(self.confirmed_sma_defined_n),
            "live_eq_confirmed_n": int(self.live_eq_confirmed_n),
            "live_ne_confirmed_n": int(self.live_ne_confirmed_n),
            "ma_history_spans_sessions": True,
            "sma_reset_at_open": False,
        }


@dataclass
class CausalATR1m:
    """Causal 1-minute ATR20 from completed 1m true ranges. Spans sessions."""

    n: int = 20
    trs: list[float] = field(default_factory=list)
    prev_c: float | None = None

    def observe(self, h: Any, l: Any, c: Any) -> float:
        hf = float(h) if _finite(h) else float("nan")
        lf = float(l) if _finite(l) else float("nan")
        cf = float(c) if _finite(c) else float("nan")
        if _finite(hf) and _finite(lf):
            tr = float(hf) - float(lf)
            if _finite(self.prev_c):
                tr = max(tr, abs(float(hf) - float(self.prev_c)), abs(float(lf) - float(self.prev_c)))
            if tr >= 0:
                self.trs.append(float(tr))
                if len(self.trs) > 80:
                    self.trs = self.trs[-40:]
        if _finite(cf):
            self.prev_c = cf
        if len(self.trs) < int(self.n):
            return float("nan")
        xs = self.trs[-int(self.n) :]
        return float(sum(xs) / float(len(xs)))
