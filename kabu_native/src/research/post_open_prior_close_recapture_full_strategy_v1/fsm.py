"""Prior-close below-regime then recapture FSM. One signal per episode. Anchor = frozen PreviousClose."""
from __future__ import annotations

from typing import Any, Optional

from research.post_open_prior_close_recapture_full_strategy_v1 import EXIT_ID, STRATEGY_ID
from research.post_open_prior_close_recapture_full_strategy_v1.fields import (
    observed_trade_update,
    trusted_state,
)


class PriorCloseFsm:
    def __init__(self, *, symbol: str, date: str, flatten_t: float, am_start: float) -> None:
        self.symbol = str(symbol)
        self.date = str(date)
        self.flatten_t = float(flatten_t)
        self.am_start = float(am_start)
        self.last_vol: Optional[float] = None
        self.prev_close: Optional[float] = None
        self.awaiting_recapture = False
        self.ep_seq = 0
        self.ep: Optional[dict[str, Any]] = None
        self.episodes: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.otus: list[dict[str, Any]] = []
        self.integrity_n = 0
        self.prev_valid_n = 0
        self.prev_change_n = 0
        self.below_event_n = 0
        self.special_overlap_n = 0
        self.sq_on_signal_n = 0

    def process(self, *, ingress_t: float, payload: dict[str, Any]) -> None:
        t = float(ingress_t)
        if t + 1e-12 >= self.flatten_t:
            return
        st = trusted_state(payload, event_t=t)
        vol = st["vol"]
        px = st["px"]
        pc_now = st["prev_close"]
        if vol is not None and self.last_vol is not None and float(vol) < float(self.last_vol) and self.ep is not None:
            self.integrity_n += 1
        trade = observed_trade_update(last_vol=self.last_vol, vol=vol, px=px)
        if pc_now is not None:
            self.prev_valid_n += 1
            if self.prev_close is None:
                self.prev_close = float(pc_now)
            elif float(pc_now) != float(self.prev_close):
                self.prev_change_n += 1
        pc = self.prev_close
        session_ok = bool(
            st["opened"]
            and st["continuous"]
            and (not st["special"])
            and (not st["preopen"])
            and t >= self.am_start
        )
        if trade:
            self.otus.append(
                {
                    "t": t,
                    "px": float(px),
                    "prev_close": float(pc) if pc is not None else None,
                    "continuous": bool(st["continuous"]),
                    "special": bool(st["special"]),
                }
            )
        if st["special"] and trade:
            self.special_overlap_n += 1
        if session_ok and trade and pc is not None and px is not None:
            if float(px) < float(pc):
                self.below_event_n += 1
                if self.ep is None or self.ep.get("signaled"):
                    self.ep_seq += 1
                    self.ep = {
                        "date": self.date,
                        "symbol": self.symbol,
                        "episode_seq": int(self.ep_seq),
                        "episode_id": f"{self.date}:{self.symbol}:{self.ep_seq}",
                        "below_t": t,
                        "anchor": float(pc),
                        "signaled": False,
                    }
                    self.episodes.append(self.ep)
                self.awaiting_recapture = True
            elif float(px) > float(pc) and self.awaiting_recapture and self.ep is not None and (not self.ep.get("signaled")):
                if st["special"]:
                    self.sq_on_signal_n += 1
                else:
                    self.ep["signaled"] = True
                    self.ep["recapture_t"] = t
                    self.ep["recapture_px"] = float(px)
                    self.awaiting_recapture = False
                    self.signals.append(
                        {
                            "date": self.date,
                            "session": "AM",
                            "symbol": self.symbol,
                            "t0": t,
                            "signal_t0": t,
                            "episode_id": self.ep["episode_id"],
                            "episode_seq": self.ep["episode_seq"],
                            "anchor": float(self.ep["anchor"]),
                            "px": float(px),
                            "prev_close": float(pc),
                            "thesis": STRATEGY_ID,
                            "exit_id": EXIT_ID,
                            "STRATEGY_ID": STRATEGY_ID,
                        }
                    )
        if trade and not (vol is not None and self.last_vol is not None and float(vol) < float(self.last_vol)):
            pass
        if vol is not None and not (self.last_vol is not None and float(vol) < float(self.last_vol)):
            self.last_vol = float(vol)

    def first_exit_fire(self, *, fill_t: float, anchor: float) -> Optional[float]:
        for rec in self.otus:
            if float(rec["t"]) <= float(fill_t) + 1e-12:
                continue
            if float(rec["t"]) + 1e-12 >= self.flatten_t:
                break
            if float(rec["px"]) < float(anchor):
                return float(rec["t"])
        return None

    def first_below_after(self, *, t0: float, anchor: float) -> Optional[float]:
        for rec in self.otus:
            if float(rec["t"]) <= float(t0) + 1e-12:
                continue
            if float(rec["px"]) < float(anchor):
                return float(rec["t"])
        return None
