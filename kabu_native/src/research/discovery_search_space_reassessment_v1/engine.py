"""Quote tapes for fixed-horizon labels. Reuses discovery causal Ask/Bid gates. No strategy."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.anchor_vs_event_driven.run_comparison import _bare
from research.new_full_strategy_implementation_and_dev_eval_v1.quotes import payload_of, quote_snap
from research.profitable_move_mechanism_discovery_v1.engine import DiscoveryEngine


class TapeEngine(DiscoveryEngine):
    def __init__(self, day: str, universe: list[str]) -> None:
        super().__init__(day, universe)
        self.mid_t: dict[str, list[float]] = {s: [] for s in self.universe}
        self.mid_bid: dict[str, list[float]] = {s: [] for s in self.universe}
        self.mid_ask: dict[str, list[float]] = {s: [] for s in self.universe}

    def _apply(self, rec: dict[str, Any], arrival: float) -> None:
        super()._apply(rec, arrival)
        pay = payload_of(rec)
        sym = _bare(rec.get("symbol") or pay.get("Symbol"))
        if sym not in self.uni_set:
            return
        if float(arrival) >= float(self.am_end) - 1e-12:
            return
        snap = quote_snap(rec, pay, float(arrival))
        try:
            bf = float(snap.get("bid"))
            af = float(snap.get("ask"))
        except (TypeError, ValueError):
            return
        if bf == bf and af == af and bf > 0 and af > 0:
            self.mid_t[sym].append(float(arrival))
            self.mid_bid[sym].append(bf)
            self.mid_ask[sym].append(af)

    def result(self) -> dict[str, Any]:
        out = super().result()
        mid: dict[str, dict[str, np.ndarray]] = {}
        for s in self.universe:
            mid[s] = {
                "t": np.asarray(self.mid_t[s], dtype=float),
                "bid": np.asarray(self.mid_bid[s], dtype=float),
                "ask": np.asarray(self.mid_ask[s], dtype=float),
            }
        out["mid"] = mid
        return out


def replay_records(day: str, universe: list[str], records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda r: int(r.get("sequence") or 0))
    eng = TapeEngine(day, universe)
    for rec in ordered:
        eng.ingest(rec)
    eng.finish()
    return eng.result()


def first_ge(t: np.ndarray, px: np.ndarray, t0: float) -> tuple[Optional[float], Optional[float]]:
    if int(t.size) == 0:
        return None, None
    i = int(np.searchsorted(t, float(t0), side="left"))
    if i >= int(t.size):
        return None, None
    return float(t[i]), float(px[i])


def first_in(t: np.ndarray, px: np.ndarray, lo: float, hi: float) -> tuple[Optional[float], Optional[float]]:
    if int(t.size) == 0:
        return None, None
    i = int(np.searchsorted(t, float(lo), side="left"))
    j = int(np.searchsorted(t, float(hi), side="left"))
    if i >= j:
        return None, None
    return float(t[i]), float(px[i])


def last_le_mid(t: np.ndarray, bid: np.ndarray, ask: np.ndarray, t0: float) -> Optional[float]:
    if int(t.size) == 0:
        return None
    i = int(np.searchsorted(t, float(t0), side="right") - 1)
    if i < 0:
        return None
    return (float(bid[i]) + float(ask[i])) / 2.0


def first_in_mid(t: np.ndarray, bid: np.ndarray, ask: np.ndarray, lo: float, hi: float) -> Optional[float]:
    if int(t.size) == 0:
        return None
    i = int(np.searchsorted(t, float(lo), side="left"))
    j = int(np.searchsorted(t, float(hi), side="left"))
    if i >= j:
        return None
    return (float(bid[i]) + float(ask[i])) / 2.0
