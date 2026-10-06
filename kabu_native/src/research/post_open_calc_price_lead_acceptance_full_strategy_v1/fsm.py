"""CalcPrice up-lead FSM. One signal per episode. Anchor frozen at start."""
from __future__ import annotations

from typing import Any, Optional

from research.post_open_calc_price_lead_acceptance_full_strategy_v1 import EXIT_ID, STRATEGY_ID
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.calc import (
    observed_trade_update,
    trusted_state,
)


class CalcLeadFsm:
    def __init__(self, *, symbol: str, date: str, flatten_t: float, am_start: float) -> None:
        self.symbol = str(symbol)
        self.date = str(date)
        self.flatten_t = float(flatten_t)
        self.am_start = float(am_start)
        self.last_vol: Optional[float] = None
        self.last_trade_px: Optional[float] = None
        self.last_valid_calc: Optional[float] = None
        self.lead_on = False
        self.ep_seq = 0
        self.ep: Optional[dict[str, Any]] = None
        self.episodes: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.otus: list[dict[str, Any]] = []
        self.integrity_n = 0
        self.calc_valid_n = 0
        self.calc_change_n = 0
        self.no_prior_trade_n = 0
        self.preaccept_failure_n = 0
        self._no_prior_counted = False

    def process(self, *, ingress_t: float, payload: dict[str, Any]) -> None:
        t = float(ingress_t)
        if t + 1e-12 >= self.flatten_t:
            return
        st = trusted_state(payload, event_t=t)
        vol = st["vol"]
        px = st["px"]
        calc = st["calc"]
        if vol is not None and self.last_vol is not None and float(vol) < float(self.last_vol) and self.ep is not None:
            self.integrity_n += 1
        trade = observed_trade_update(last_vol=self.last_vol, vol=vol, px=px)
        if calc is not None:
            self.calc_valid_n += 1
            if self.last_valid_calc is not None and float(calc) != float(self.last_valid_calc):
                self.calc_change_n += 1
        session_ok = bool(
            st["opened"]
            and st["continuous"]
            and (not st["special"])
            and (not st["preopen"])
            and t >= self.am_start
        )
        has_prior = self.last_trade_px is not None
        if session_ok and calc is not None and (not has_prior) and (not self._no_prior_counted):
            self.no_prior_trade_n += 1
            self._no_prior_counted = True
        lead_now = bool(session_ok and calc is not None and has_prior and float(calc) > float(self.last_trade_px))
        if trade:
            self.otus.append(
                {
                    "t": t,
                    "px": float(px),
                    "calc": float(calc) if calc is not None else self.last_valid_calc,
                    "continuous": bool(st["continuous"]),
                }
            )

        if session_ok:
            if lead_now and not self.lead_on:
                self.ep_seq += 1
                self.ep = {
                    "date": self.date,
                    "symbol": self.symbol,
                    "episode_seq": int(self.ep_seq),
                    "episode_id": f"{self.date}:{self.symbol}:{self.ep_seq}",
                    "calc_lead_start": t,
                    "anchor": float(calc),
                    "signaled": False,
                    "preaccept_fail": False,
                }
                self.episodes.append(self.ep)
            elif (not lead_now) and self.lead_on:
                if self.ep is not None and (not self.ep.get("signaled")):
                    self.ep["preaccept_fail"] = True
                    self.ep["end_reason"] = "CALC_LEAD_FAILED_BEFORE_ACCEPTANCE"
                    self.preaccept_failure_n += 1
                if self.ep is not None:
                    self.ep["lead_end"] = t
                self.ep = None
            self.lead_on = lead_now
        elif self.lead_on and (not lead_now):
            if self.ep is not None and (not self.ep.get("signaled")):
                self.ep["preaccept_fail"] = True
                self.ep["end_reason"] = "CALC_LEAD_FAILED_BEFORE_ACCEPTANCE"
                self.preaccept_failure_n += 1
            if self.ep is not None:
                self.ep["lead_end"] = t
            self.ep = None
            self.lead_on = False

        entry_ok = (
            session_ok
            and self.ep is not None
            and (not self.ep.get("signaled"))
            and lead_now
            and trade
            and px is not None
            and float(px) >= float(self.ep["anchor"])
        )
        if entry_ok:
            self.ep["signaled"] = True
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
                    "calc": float(calc) if calc is not None else None,
                    "thesis": STRATEGY_ID,
                    "exit_id": EXIT_ID,
                    "STRATEGY_ID": STRATEGY_ID,
                }
            )

        if calc is not None:
            self.last_valid_calc = float(calc)
        if trade and not (vol is not None and self.last_vol is not None and float(vol) < float(self.last_vol)):
            self.last_trade_px = float(px)
        if vol is not None and not (self.last_vol is not None and float(vol) < float(self.last_vol)):
            self.last_vol = float(vol)

    def first_exit_fire(self, *, fill_t: float, anchor: float) -> Optional[float]:
        for rec in self.otus:
            if float(rec["t"]) <= float(fill_t) + 1e-12:
                continue
            if float(rec["t"]) + 1e-12 >= self.flatten_t:
                break
            c = rec.get("calc")
            if c is None:
                continue
            if float(rec["px"]) < float(anchor) and float(c) < float(anchor):
                return float(rec["t"])
        return None
