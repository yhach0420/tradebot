"""PreviousClose recapture FSM. One signal per below-close episode. Anchor = PreviousClose."""
from __future__ import annotations

from typing import Any, Optional

from research.post_open_previous_close_recapture_full_strategy_v1 import EXIT_ID, STRATEGY_ID
from research.post_open_previous_close_recapture_full_strategy_v1.fields import (
    observed_trade_update,
    trusted_state,
)


class PrevCloseFsm:
    def __init__(self, *, symbol: str, date: str, flatten_t: float, am_start: float) -> None:
        self.symbol = str(symbol)
        self.date = str(date)
        self.flatten_t = float(flatten_t)
        self.am_start = float(am_start)
        self.last_vol: Optional[float] = None
        self.below_active = False
        self.ep_seq = 0
        self.ep: Optional[dict[str, Any]] = None
        self.episodes: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.otus: list[dict[str, Any]] = []
        self.integrity_n = 0
        self.pc_valid_n = 0
        self.below_event_n = 0
        self.recapture_n = 0
        self.equal_n = 0

    def process(self, *, ingress_t: float, payload: dict[str, Any]) -> None:
        t = float(ingress_t)
        if t + 1e-12 >= self.flatten_t:
            return
        st = trusted_state(payload, event_t=t)
        vol = st["vol"]
        px = st["px"]
        pc = st["prev_close"]
        if vol is not None and self.last_vol is not None and float(vol) < float(self.last_vol) and self.ep is not None:
            self.integrity_n += 1
        trade = observed_trade_update(last_vol=self.last_vol, vol=vol, px=px)
        if pc is not None:
            self.pc_valid_n += 1
        session_ok = bool(
            st["opened"]
            and st["continuous"]
            and (not st["special"])
            and (not st["preopen"])
            and t >= self.am_start
        )
        if trade:
            self.otus.append({"t": t, "px": float(px), "pc": float(pc) if pc is not None else None, "continuous": bool(st["continuous"])})

        if session_ok and trade and pc is not None and px is not None:
            if float(px) < float(pc):
                self.below_event_n += 1
                if not self.below_active:
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
                    self.below_active = True
            elif float(px) > float(pc):
                if self.below_active and self.ep is not None and (not self.ep.get("signaled")):
                    self.ep["signaled"] = True
                    self.ep["recapture_t"] = t
                    self.ep["recapture_px"] = float(px)
                    self.recapture_n += 1
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
                    self.below_active = False
                    self.ep = None
            else:
                self.equal_n += 1

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
