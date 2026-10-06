"""Dual buy-pressure FSM. One signal per pressure run. No Sign direction."""
from __future__ import annotations

from typing import Any, Optional

from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import EXIT_ID, STRATEGY_ID
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.pressure import (
    buy_pressure,
    observed_trade_update,
    trusted_state,
)


class AopSymbolFsm:
    def __init__(self, *, symbol: str, date: str, flatten_t: float, am_start: float) -> None:
        self.symbol = str(symbol)
        self.date = str(date)
        self.flatten_t = float(flatten_t)
        self.am_start = float(am_start)
        self.last_vol: Optional[float] = None
        self.last_trade_px: Optional[float] = None
        self.last_trade_ingress: Optional[float] = None
        self.pressure_on = False
        self.run_seq = 0
        self.run: Optional[dict[str, Any]] = None
        self.runs: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.otus: list[dict[str, Any]] = []
        self.integrity_n = 0
        self.bp_otu_n = 0

    def process(self, *, ingress_t: float, payload: dict[str, Any]) -> None:
        t = float(ingress_t)
        if t + 1e-12 >= self.flatten_t:
            return
        st = trusted_state(payload, event_t=t)
        bp = buy_pressure(payload)
        vol = st["vol"]
        px = st["px"]
        if vol is not None and self.last_vol is not None and float(vol) < float(self.last_vol) and self.run is not None:
            self.integrity_n += 1
        trade = observed_trade_update(last_vol=self.last_vol, vol=vol, px=px)
        if trade:
            self.otus.append(
                {
                    "t": t,
                    "px": float(px),
                    "BUY_PRESSURE": bool(bp["BUY_PRESSURE"]),
                    "established": bool(bp["established"]),
                    "continuous": bool(st["continuous"]),
                }
            )
            if bool(bp["BUY_PRESSURE"]) and bool(bp["established"]):
                self.bp_otu_n += 1
        true_now = bool(bp["established"] and bp["BUY_PRESSURE"])
        session_ok = bool(
            st["opened"]
            and st["continuous"]
            and (not st["special"])
            and (not st["preopen"])
            and t >= self.am_start
        )
        if session_ok:
            if true_now and not self.pressure_on:
                self.run_seq += 1
                self.run = {
                    "date": self.date,
                    "symbol": self.symbol,
                    "run_seq": int(self.run_seq),
                    "run_id": f"{self.date}:{self.symbol}:{self.run_seq}",
                    "pressure_start": t,
                    "anchor": self.last_trade_px,
                    "anchor_ingress": self.last_trade_ingress,
                    "no_anchor": self.last_trade_px is None
                    or self.last_trade_ingress is None
                    or float(self.last_trade_ingress) >= t - 1e-15,
                    "signaled": False,
                }
                self.runs.append(self.run)
            elif (not true_now) and self.pressure_on:
                if self.run is not None:
                    self.run["pressure_end"] = t
                self.run = None
            self.pressure_on = true_now
        elif self.pressure_on and (not true_now):
            if self.run is not None:
                self.run["pressure_end"] = t
            self.run = None
            self.pressure_on = False

        entry_ok = (
            session_ok
            and self.run is not None
            and (not self.run.get("signaled"))
            and (not self.run.get("no_anchor"))
            and true_now
            and trade
            and px is not None
            and float(px) > float(self.run["anchor"])
        )
        if entry_ok:
            self.run["signaled"] = True
            self.signals.append(
                {
                    "date": self.date,
                    "session": "AM",
                    "symbol": self.symbol,
                    "t0": t,
                    "signal_t0": t,
                    "run_id": self.run["run_id"],
                    "run_seq": self.run["run_seq"],
                    "anchor": float(self.run["anchor"]),
                    "px": float(px),
                    "thesis": STRATEGY_ID,
                    "exit_id": EXIT_ID,
                    "STRATEGY_ID": STRATEGY_ID,
                }
            )

        if trade and not (vol is not None and self.last_vol is not None and float(vol) < float(self.last_vol)):
            self.last_trade_px = float(px)
            self.last_trade_ingress = t
        if vol is not None and not (self.last_vol is not None and float(vol) < float(self.last_vol)):
            self.last_vol = float(vol)

    def first_exit_fire(self, *, fill_t: float, anchor: float) -> Optional[float]:
        for rec in self.otus:
            if float(rec["t"]) <= float(fill_t) + 1e-12:
                continue
            if rec.get("t") is None:
                continue
            if float(rec["t"]) + 1e-12 >= self.flatten_t:
                break
            pressure = bool(rec.get("BUY_PRESSURE")) and bool(rec.get("established", True))
            if (not pressure) and float(rec["px"]) < float(anchor):
                return float(rec["t"])
        return None
